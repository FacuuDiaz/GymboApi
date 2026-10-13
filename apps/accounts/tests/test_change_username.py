"""Changing the username, and moving a Google-only account to username/email + password."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import SocialIdentity, User
from apps.training.models import Routine

pytestmark = pytest.mark.django_db
U = "/api/v1/users/me/username/"
PASSWORD = "una-clave-segura-123"


@pytest.fixture
def ana(auth_client, make_user):
    return auth_client(make_user(username="ana_vieja", email="ana@example.com"))


def login(api, login_id, password=PASSWORD):
    return api.post("/api/v1/auth/login/", {"login": login_id, "password": password}, format="json")


def test_change_username_with_password_and_sign_in_with_the_new_one(ana, api):
    client, user, _ = ana
    response = client.post(U, {"username": "faq", "password": PASSWORD}, format="json")
    assert response.status_code == 200
    assert response.json()["username"] == "faq"
    assert response.json()["username_change_allowed_at"]  # next change is in the future
    assert login(api, "faq").status_code == 200
    assert login(api, "ana@example.com").status_code == 200  # email login keeps working
    assert login(api, "ana_vieja").status_code == 401


@pytest.mark.parametrize("body,field", [
    ({"username": "faq", "password": "otra"}, "password"),
    ({"username": "ana_vieja", "password": PASSWORD}, "username"),  # same name
    ({"username": "OCUPADO", "password": PASSWORD}, "username"),  # taken, any case
    ({"username": "a b", "password": PASSWORD}, "username"),  # invalid characters
    ({"username": "ab", "password": PASSWORD}, "username"),  # too short
])
def test_rejected_changes_leave_the_username_alone(ana, make_user, body, field):
    make_user(username="ocupado")
    client, user, _ = ana
    response = client.post(U, body, format="json")
    assert response.status_code == 400 and field in response.json()["error"]["fields"]
    user.refresh_from_db()
    assert user.username == "ana_vieja"


def test_one_change_per_cooldown(ana, settings):
    client, user, _ = ana
    assert client.post(U, {"username": "faq", "password": PASSWORD}, format="json").status_code == 200
    again = client.post(U, {"username": "faq2", "password": PASSWORD}, format="json")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "username_change_too_soon")
    User.objects.filter(pk=user.pk).update(username_changed_at=timezone.now() - timedelta(days=settings.USERNAME_CHANGE_COOLDOWN_DAYS + 1))
    assert client.post(U, {"username": "faq2", "password": PASSWORD}, format="json").status_code == 200


def test_mentions_in_other_peoples_copies_follow_the_new_name(ana, make_user):
    client, user, _ = ana
    other = make_user(username="beto")
    copy = Routine.objects.create(owner=other, name="X", goal="strength", planning_mode="repeat", start_date="2026-10-05", attribution="Basada en «X» de @ana_vieja")
    namesake = Routine.objects.create(owner=other, name="Y", goal="strength", planning_mode="repeat", start_date="2026-10-05", attribution="Basada en «Y» de @ana_vieja2")
    client.post(U, {"username": "faq", "password": PASSWORD}, format="json")
    copy.refresh_from_db()
    namesake.refresh_from_db()
    assert copy.attribution == "Basada en «X» de @faq" and copy.version == 2
    assert namesake.attribution == "Basada en «Y» de @ana_vieja2"


def test_google_only_account_confirms_with_its_current_username(auth_client, make_user):
    user = make_user(username="facundodiazgira")
    user.set_unusable_password()
    user.save()
    client, _, _ = auth_client(user)
    assert client.post(U, {"username": "faq", "confirm_username": "otro"}, format="json").status_code == 400
    assert client.post(U, {"username": "faq", "confirm_username": "FacundoDiazGira"}, format="json").status_code == 200


def test_google_account_creates_a_password_signs_in_with_it_and_leaves_google(auth_client, make_user, api):
    user = make_user(username="facundodiazgira", email="facu@example.com")
    user.set_unusable_password()
    user.save()
    SocialIdentity.objects.create(user=user, provider="google", subject="sub-facu")
    client, _, _ = auth_client(user)
    assert login(api, "facu@example.com").status_code == 401  # no password yet: only Google

    created = client.post("/api/v1/users/me/password/", {"new_password": "Sentadilla-Profunda-42"}, format="json")
    assert created.status_code == 200 and {"access", "refresh"} <= set(created.json())
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {created.json()['access']}")
    assert client.get("/api/v1/users/me/").json()["has_password"] is True

    assert login(api, "facu@example.com", "Sentadilla-Profunda-42").status_code == 200
    assert login(api, "facundodiazgira", "Sentadilla-Profunda-42").status_code == 200

    assert client.post("/api/v1/auth/google/unlink/", {"password": "Sentadilla-Profunda-42"}, format="json").status_code == 204
    assert client.get("/api/v1/users/me/").json()["linked_providers"] == []
