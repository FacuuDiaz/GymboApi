import pytest
from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction

from accounts.models import SocialIdentity, TrainingPreferences, User


def test_email_is_normalized_and_password_hashed_with_argon2(make_user):
    user = make_user(username="ana", email="  Ana.Perez@Example.COM ")
    assert user.email == "ana.perez@example.com"
    assert user.password.startswith("argon2")
    assert "una-clave-segura-123" not in user.password
    assert user.check_password("una-clave-segura-123")


def test_optional_personal_fields_can_stay_empty(make_user):
    user = make_user()
    assert (user.phone, user.gender, user.birth_date, user.first_name) == ("", "", None, "")


def test_email_is_unique_case_insensitively(make_user):
    make_user(email="ana@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(username="otra", email="ANA@example.com")


def test_username_is_unique_case_insensitively(make_user):
    make_user(username="Ana")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_user(username="ana")


def test_email_cannot_be_blank_at_database_level(make_user):
    user = make_user()
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(email="")


def test_email_is_required_by_manager(db):
    with pytest.raises(ValueError):
        User.objects.create_user(username="sinmail", email="", password="una-clave-segura-123")


def test_login_by_username_is_case_insensitive(make_user):
    make_user(username="Ana", password="una-clave-segura-123")
    assert authenticate(username="ANA", password="una-clave-segura-123") is not None
    assert authenticate(username="ana", password="incorrecta") is None


def test_google_subject_can_only_belong_to_one_account(make_user):
    a, b = make_user(), make_user()
    SocialIdentity.objects.create(user=a, provider="google", subject="1234567890")
    with pytest.raises(IntegrityError), transaction.atomic():
        SocialIdentity.objects.create(user=b, provider="google", subject="1234567890")


def test_one_google_identity_per_user(make_user):
    a = make_user()
    SocialIdentity.objects.create(user=a, provider="google", subject="1")
    with pytest.raises(IntegrityError), transaction.atomic():
        SocialIdentity.objects.create(user=a, provider="google", subject="2")


def test_preferences_defaults_match_the_app(make_user):
    prefs = TrainingPreferences.objects.create(user=make_user())
    assert (prefs.unit, prefs.theme, prefs.level, prefs.goal, prefs.days_per_week) == ("kg", "dark", "beginner", "hypertrophy", 3)
    assert (float(prefs.increment_barbell), float(prefs.increment_dumbbell), float(prefs.increment_machine)) == (2.5, 2.0, 5.0)


@pytest.mark.parametrize("days", [0, 8])
def test_preferences_days_per_week_range(make_user, days):
    with pytest.raises(IntegrityError), transaction.atomic():
        TrainingPreferences.objects.create(user=make_user(), days_per_week=days)
