"""
Google Sign-In. Token verification is exercised with REAL RS256 signatures
(a local key pair + self-signed certificate standing in for Google's published
certs), so signature, audience, issuer and expiry checks are the library's
actual behaviour — not a mock.
"""
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.test import override_settings
from google.auth import crypt, jwt

from apps.accounts import google
from apps.accounts.models import SocialIdentity, User

CLIENT_ID = "test-android.apps.googleusercontent.com"
GOOGLE_LOGIN = "/api/v1/auth/google/"
LINK = "/api/v1/auth/google/link/"
UNLINK = "/api/v1/auth/google/unlink/"
PASSWORD = "Sentadilla-Profunda-42"


def _key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-google")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


GOOGLE_KEY, GOOGLE_CERT = _key_and_cert()
OTHER_KEY, _ = _key_and_cert()


def make_id_token(key=GOOGLE_KEY, **overrides):
    now = int(time.time())
    payload = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "1098765432109876543210",
               "email": "gabi@gmail.com", "email_verified": True, "given_name": "Gabi", "family_name": "Pérez",
               "iat": now, "exp": now + 3600}
    payload.update(overrides)
    return jwt.encode(crypt.RSASigner.from_string(key, key_id="kid-1"), payload).decode()


@pytest.fixture
def google_certs():
    with patch("google.oauth2.id_token._fetch_certs", return_value={"kid-1": GOOGLE_CERT}):
        yield


# ── Verification with real signatures ───────────────────────────────────────

def test_valid_google_token_is_accepted(google_certs):
    claims = google.verify_google_id_token(make_id_token())
    assert (claims.subject, claims.email, claims.given_name) == ("1098765432109876543210", "gabi@gmail.com", "Gabi")


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"key": OTHER_KEY},  # forged signature
        {"aud": "someone-elses-app.apps.googleusercontent.com"},  # token issued for another app
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 600, "iat": int(time.time()) - 4200},  # expired
        {"email_verified": False},
        {"sub": ""},
    ],
)
def test_invalid_google_tokens_are_rejected(google_certs, token_kwargs):
    with pytest.raises(google.InvalidGoogleToken):
        google.verify_google_id_token(make_id_token(**token_kwargs))


def test_garbage_is_rejected(google_certs):
    with pytest.raises(google.InvalidGoogleToken):
        google.verify_google_id_token("not-a-jwt")


@override_settings(GOOGLE_OAUTH_CLIENT_IDS=[])
def test_google_disabled_without_client_ids():
    with pytest.raises(google.GoogleNotConfigured):
        google.verify_google_id_token(make_id_token())


# ── Endpoints ───────────────────────────────────────────────────────────────



@pytest.mark.django_db
def test_first_google_login_creates_an_account(api, google_certs):
    response = api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["created"] is True and body["access"] and body["refresh"]
    user = User.objects.get(email="gabi@gmail.com")
    assert not user.has_usable_password()
    assert (user.first_name, user.last_name, user.username) == ("Gabi", "Pérez", "gabi")
    assert SocialIdentity.objects.get(user=user).subject == "1098765432109876543210"


@pytest.mark.django_db
def test_next_google_login_signs_into_the_same_account(api, google_certs):
    first = api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json").json()
    # Even if the Google email changed, the stable `sub` identifies the account.
    again = api.post(GOOGLE_LOGIN, {"id_token": make_id_token(email="nuevo@gmail.com")}, format="json")
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["user"]["id"] == first["user"]["id"]
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_google_never_takes_over_an_existing_account_by_email(api, google_certs, make_user):
    owner = make_user(username="gabi", email="gabi@gmail.com", password=PASSWORD)
    response = api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "google_email_in_use"
    assert not SocialIdentity.objects.filter(user=owner).exists()
    assert "access" not in response.json()


@pytest.mark.django_db
def test_invalid_google_token_returns_401(api, google_certs):
    response = api.post(GOOGLE_LOGIN, {"id_token": make_id_token(key=OTHER_KEY)}, format="json")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_google_token"


@pytest.mark.django_db
@override_settings(GOOGLE_OAUTH_CLIENT_IDS=[])
def test_google_login_when_not_configured_returns_503(api):
    response = api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "google_not_configured"


@pytest.mark.django_db
def test_inactive_google_account_cannot_sign_in(api, google_certs):
    api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json")
    User.objects.filter(email="gabi@gmail.com").update(is_active=False)
    assert api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json").status_code == 401


# ── Linking and unlinking ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_link_google_to_my_account_then_sign_in_with_it(api, auth_client, make_user, google_certs):
    client, user, _ = auth_client(make_user(username="gabi", email="gabi@gmail.com", password=PASSWORD))
    response = client.post(LINK, {"id_token": make_id_token()}, format="json")
    assert response.status_code == 200
    assert response.json()["linked_providers"] == ["google"]
    assert client.post(LINK, {"id_token": make_id_token()}, format="json").status_code == 200  # idempotent
    login = api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json")
    assert login.status_code == 200 and login.json()["user"]["id"] == str(user.id)


@pytest.mark.django_db
def test_cannot_link_a_google_account_that_belongs_to_someone_else(api, auth_client, google_certs):
    api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json")  # someone already uses it
    client, _, _ = auth_client()
    response = client.post(LINK, {"id_token": make_id_token()}, format="json")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "google_identity_in_use"


@pytest.mark.django_db
def test_only_one_google_account_per_user(auth_client, google_certs):
    client, _, _ = auth_client()
    client.post(LINK, {"id_token": make_id_token()}, format="json")
    response = client.post(LINK, {"id_token": make_id_token(sub="otro-sub")}, format="json")
    assert response.status_code == 409


@pytest.mark.django_db
def test_unlink_requires_the_password(auth_client, make_user, google_certs):
    client, user, _ = auth_client(make_user(password=PASSWORD))
    client.post(LINK, {"id_token": make_id_token()}, format="json")
    assert client.post(UNLINK, {"password": "incorrecta"}, format="json").status_code == 403
    assert client.post(UNLINK, {"password": PASSWORD}, format="json").status_code == 204
    assert not user.social_identities.exists()
    assert client.post(UNLINK, {"password": PASSWORD}, format="json").status_code == 404


@pytest.mark.django_db
def test_cannot_unlink_the_only_way_to_sign_in(api, auth_client, google_certs):
    api.post(GOOGLE_LOGIN, {"id_token": make_id_token()}, format="json")
    client, _, _ = auth_client(User.objects.get(email="gabi@gmail.com"))
    response = client.post(UNLINK, {"password": "x"}, format="json")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "password_required"
