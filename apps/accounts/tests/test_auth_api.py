"""Registration, password login, tokens (rotation, reuse, revocation) and the /users/me endpoints."""
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import User
from apps.accounts.throttles import AuthIPThrottle, LoginIdentifierThrottle

pytestmark = pytest.mark.django_db

PASSWORD = "Sentadilla-Profunda-42"
REGISTER = "/api/v1/auth/register/"
LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"
LOGOUT_ALL = "/api/v1/auth/logout-all/"
ME = "/api/v1/users/me/"
CHANGE_PASSWORD = "/api/v1/users/me/password/"


def bearer(api, access):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api


def backdated_access(user, seconds=5):
    """An access token issued `seconds` ago (revocation works at 1-second granularity)."""
    token = AccessToken.for_user(user)
    token.set_iat(at_time=timezone.now() - timedelta(seconds=seconds))
    return str(token)


# ── Registration ────────────────────────────────────────────────────────────

def test_register_with_only_required_fields(api):
    response = api.post(REGISTER, {"username": "ana", "email": "  Ana@Example.com", "password": PASSWORD}, format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert set(body) == {"access", "refresh", "user"}
    assert body["user"]["email"] == "ana@example.com"
    assert body["user"]["has_password"] is True
    assert (body["user"]["phone"], body["user"]["gender"], body["user"]["birth_date"]) == ("", "", None)
    assert "password" not in body["user"]
    user = User.objects.get(username="ana")
    assert user.password.startswith("argon2") and PASSWORD not in user.password


def test_register_stores_optional_fields_when_given(api):
    payload = {"username": "beto", "email": "beto@example.com", "password": PASSWORD, "first_name": "Beto",
               "phone": "+54 11 5555-1234", "gender": "male", "birth_date": "1990-05-02"}
    assert api.post(REGISTER, payload, format="json").status_code == 201
    user = User.objects.get(username="beto")
    assert (user.first_name, user.phone, user.gender, user.birth_date) == ("Beto", "+54 11 5555-1234", "male", date(1990, 5, 2))


@pytest.mark.parametrize("missing", ["username", "email", "password"])
def test_register_requires_username_email_and_password(api, missing):
    payload = {"username": "ana", "email": "ana@example.com", "password": PASSWORD}
    payload.pop(missing)
    response = api.post(REGISTER, payload, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert missing in response.json()["error"]["fields"]


def test_register_rejects_duplicates_case_insensitively(api, make_user):
    make_user(username="Ana", email="ana@example.com")
    r1 = api.post(REGISTER, {"username": "ANA", "email": "otra@example.com", "password": PASSWORD}, format="json")
    r2 = api.post(REGISTER, {"username": "otra", "email": "ANA@EXAMPLE.COM", "password": PASSWORD}, format="json")
    assert r1.status_code == r2.status_code == 400
    assert "username" in r1.json()["error"]["fields"] and "email" in r2.json()["error"]["fields"]


@pytest.mark.parametrize("password", ["corta1", "12345678901", "password1234", "anarodriguez1"])
def test_register_enforces_password_policy(api, password):
    response = api.post(REGISTER, {"username": "anarodriguez", "email": "ana@example.com", "password": password}, format="json")
    assert response.status_code == 400
    assert "password" in response.json()["error"]["fields"]


@pytest.mark.parametrize(
    "extra",
    [{"phone": "llamame"}, {"birth_date": (date.today() + timedelta(days=1)).isoformat()}, {"gender": "robot"},
     {"username": "no válido!"}, {"username": "ab"}, {"email": "no-es-un-email"}],
)
def test_register_validates_fields(api, extra):
    payload = {"username": "ana", "email": "ana@example.com", "password": PASSWORD, **extra}
    assert api.post(REGISTER, payload, format="json").status_code == 400


def test_register_ignores_privilege_fields_sent_by_the_client(api):
    payload = {"username": "ana", "email": "ana@example.com", "password": PASSWORD, "is_staff": True, "is_superuser": True}
    assert api.post(REGISTER, payload, format="json").status_code == 201
    user = User.objects.get(username="ana")
    assert not user.is_staff and not user.is_superuser


# ── Login ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("login", ["ana", "ANA", "ana@example.com", "Ana@Example.COM"])
def test_login_with_username_or_email(api, make_user, login):
    make_user(username="ana", email="ana@example.com", password=PASSWORD)
    response = api.post(LOGIN, {"login": login, "password": PASSWORD}, format="json")
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "ana"


@pytest.mark.parametrize(
    "login,password",
    [("ana", "incorrecta-123"), ("nadie", PASSWORD), ("nadie@example.com", PASSWORD), ("inactiva", PASSWORD)],
)
def test_failed_logins_are_indistinguishable(api, make_user, login, password):
    make_user(username="ana", email="ana@example.com", password=PASSWORD)
    make_user(username="inactiva", password=PASSWORD, is_active=False)
    response = api.post(LOGIN, {"login": login, "password": password}, format="json")
    assert response.status_code == 401
    assert response.json() == {"error": {"code": "invalid_credentials", "message": "Usuario/email o contraseña incorrectos."}}


def test_google_only_account_cannot_log_in_with_a_password(api):
    User.objects.create_user(username="gabi", email="gabi@example.com", password=None)
    assert api.post(LOGIN, {"login": "gabi", "password": ""}, format="json").status_code == 400
    assert api.post(LOGIN, {"login": "gabi", "password": "cualquier-cosa"}, format="json").status_code == 401


# ── Protected endpoints and access tokens ───────────────────────────────────

def test_private_endpoints_require_a_valid_access_token(api, make_user):
    for path, method in [(ME, "get"), (LOGOUT_ALL, "post"), (CHANGE_PASSWORD, "post"), ("/api/v1/auth/google/link/", "post"), ("/api/v1/auth/google/unlink/", "post")]:
        assert getattr(api, method)(path).status_code == 401
    bearer(api, "basura.no.es.un.jwt")
    assert api.get(ME).status_code == 401


def test_expired_access_token_is_rejected(api, make_user):
    token = AccessToken.for_user(make_user())
    token.set_exp(lifetime=-timedelta(seconds=1))
    assert bearer(api, str(token)).get(ME).status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(api, make_user):
    assert bearer(api, str(RefreshToken.for_user(make_user()))).get(ME).status_code == 401


def test_access_token_lifetime_is_short():
    from django.conf import settings

    assert settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] <= timedelta(minutes=15)


# ── Refresh rotation and reuse detection ────────────────────────────────────

def test_refresh_rotates_and_the_old_refresh_stops_working(api, make_user):
    tokens = api.post(LOGIN, {"login": make_user(username="ana", password=PASSWORD).username, "password": PASSWORD}, format="json").json()
    rotated = api.post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert rotated.status_code == 200
    assert rotated.json()["refresh"] != tokens["refresh"]
    assert bearer(api, rotated.json()["access"]).get(ME).status_code == 200


def test_reusing_a_rotated_refresh_token_revokes_every_session(api, make_user):
    user = make_user()
    first = str(RefreshToken.for_user(user))
    other_device = str(RefreshToken.for_user(user))
    old_access = backdated_access(user)
    second = api.post(REFRESH, {"refresh": first}, format="json").json()["refresh"]

    replay = api.post(REFRESH, {"refresh": first}, format="json")  # attacker replays the copied token
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "token_reused"
    for token in (second, other_device):  # the legitimate chain and other devices are revoked too
        assert api.post(REFRESH, {"refresh": token}, format="json").status_code == 401
    assert bearer(api, old_access).get(ME).status_code == 401


def test_invalid_or_tampered_refresh_is_rejected(api, make_user):
    good = str(RefreshToken.for_user(make_user()))
    for bad in ["", "basura", good[:-3] + "abc"]:
        response = api.post(REFRESH, {"refresh": bad}, format="json")
        assert response.status_code in (400, 401)


def test_refresh_for_deactivated_user_is_rejected(api, make_user):
    user = make_user()
    refresh = str(RefreshToken.for_user(user))
    user.is_active = False
    user.save()
    assert api.post(REFRESH, {"refresh": refresh}, format="json").status_code == 401


# ── Logout ──────────────────────────────────────────────────────────────────

def test_logout_revokes_that_refresh_token_and_is_idempotent(api, make_user):
    refresh = str(RefreshToken.for_user(make_user()))
    # No access token needed: holding the refresh token is the authorization.
    assert api.post(LOGOUT, {"refresh": refresh}, format="json").status_code == 204
    assert api.post(LOGOUT, {"refresh": refresh}, format="json").status_code == 204
    assert api.post(REFRESH, {"refresh": refresh}, format="json").status_code == 401


def test_logout_only_revokes_the_given_session(api, make_user):
    user = make_user()
    this_device, other_device = str(RefreshToken.for_user(user)), str(RefreshToken.for_user(user))
    api.post(LOGOUT, {"refresh": this_device}, format="json")
    assert api.post(REFRESH, {"refresh": other_device}, format="json").status_code == 200


@pytest.mark.parametrize("bad", ["", "basura.no.jwt"])
def test_logout_rejects_invalid_tokens(api, bad):
    assert api.post(LOGOUT, {"refresh": bad}, format="json").status_code in (400, 401)


def test_logout_rejects_an_access_token_in_place_of_a_refresh(api, make_user):
    access = str(AccessToken.for_user(make_user()))
    response = api.post(LOGOUT, {"refresh": access}, format="json")
    assert response.status_code == 401


def test_logout_all_revokes_refresh_and_access_tokens_immediately(api, make_user):
    user = make_user()
    refresh_a, refresh_b = str(RefreshToken.for_user(user)), str(RefreshToken.for_user(user))
    access = backdated_access(user)
    assert bearer(api, access).post(LOGOUT_ALL).status_code == 204
    for token in (refresh_a, refresh_b):
        assert api.post(REFRESH, {"refresh": token}, format="json").status_code == 401
    response = api.get(ME)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_not_valid"


# ── /users/me ───────────────────────────────────────────────────────────────

def test_me_returns_only_my_own_account(auth_client, make_user):
    client, user, _ = auth_client()
    make_user(username="otra")
    body = client.get(ME).json()
    assert body["id"] == str(user.id) and body["username"] == user.username


def test_me_updates_optional_fields_but_not_identity_fields(auth_client):
    client, user, _ = auth_client()
    response = client.patch(ME, {"phone": "+54 9 11 1234-5678", "gender": "", "birth_date": None, "first_name": "Ana",
                                 "username": "hacker", "email": "hacker@example.com", "id": "00000000-0000-0000-0000-000000000000",
                                 "is_staff": True}, format="json")
    assert response.status_code == 200
    user.refresh_from_db()
    assert (user.phone, user.first_name) == ("+54 9 11 1234-5678", "Ana")
    assert user.username != "hacker" and user.email != "hacker@example.com" and not user.is_staff


def test_me_does_not_allow_put(auth_client):
    client, _, _ = auth_client()
    assert client.put(ME, {}, format="json").status_code == 405


# ── Password change ─────────────────────────────────────────────────────────

def test_change_password_requires_the_current_one(auth_client, make_user):
    client, _, _ = auth_client(make_user(password=PASSWORD))
    response = client.post(CHANGE_PASSWORD, {"current_password": "incorrecta", "new_password": "Otra-Clave-Larga-77"}, format="json")
    assert response.status_code == 400
    assert "current_password" in response.json()["error"]["fields"]


def test_change_password_closes_other_sessions_and_returns_new_tokens(api, make_user):
    user = make_user(password=PASSWORD)
    other_device_refresh = str(RefreshToken.for_user(user))
    bearer(api, backdated_access(user))
    response = api.post(CHANGE_PASSWORD, {"current_password": PASSWORD, "new_password": "Otra-Clave-Larga-77"}, format="json")
    assert response.status_code == 200
    assert api.get(ME).status_code == 401  # the old access token
    assert api.post(REFRESH, {"refresh": other_device_refresh}, format="json").status_code == 401
    assert bearer(api, response.json()["access"]).get(ME).status_code == 200
    user.refresh_from_db()
    assert user.check_password("Otra-Clave-Larga-77")


def test_google_only_account_can_set_a_first_password_without_current(auth_client):
    user = User.objects.create_user(username="gabi", email="gabi@example.com", password=None)
    client, _, _ = auth_client(user)
    assert client.post(CHANGE_PASSWORD, {"new_password": "Primera-Clave-Segura-1"}, format="json").status_code == 200
    user.refresh_from_db()
    assert user.has_usable_password()


# ── Brute-force protection ──────────────────────────────────────────────────

def test_login_is_throttled_per_account_even_across_ips(api, make_user):
    make_user(username="ana", password=PASSWORD)
    cache.clear()
    with patch.object(LoginIdentifierThrottle, "THROTTLE_RATES", {"login_identifier": "3/min"}):
        codes = [
            api.post(LOGIN, {"login": "ana", "password": "mala"}, format="json", REMOTE_ADDR=f"10.0.0.{i}").status_code
            for i in range(5)
        ]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]


def test_auth_endpoints_are_throttled_per_ip(api):
    cache.clear()
    with patch.object(AuthIPThrottle, "THROTTLE_RATES", {"auth": "2/min"}):
        codes = [api.post(REGISTER, {}, format="json").status_code for _ in range(3)]
    assert codes == [400, 400, 429]
