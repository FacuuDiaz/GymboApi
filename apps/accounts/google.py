"""
Google Sign-In (OpenID Connect) ID-token verification.

The app obtains an ID token from Google with the native SDK and sends it here.
`google.oauth2.id_token.verify_oauth2_token` checks the RS256 signature against
Google's published keys, the expiry and the issuer; we additionally require
the audience to be one of OUR client IDs and the email to be verified.
Accounts are matched by the stable `sub` claim — never by email.
"""
from dataclasses import dataclass

from django.conf import settings
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleNotConfigured(Exception):
    pass


class InvalidGoogleToken(Exception):
    pass


class GoogleUnavailable(Exception):
    pass


@dataclass(frozen=True)
class GoogleClaims:
    subject: str
    email: str
    given_name: str = ""
    family_name: str = ""


def verify_google_id_token(raw_token: str) -> GoogleClaims:
    client_ids = settings.GOOGLE_OAUTH_CLIENT_IDS
    if not client_ids:
        raise GoogleNotConfigured()
    try:
        claims = id_token.verify_oauth2_token(raw_token, google_requests.Request(), audience=client_ids, clock_skew_in_seconds=10)
    except google_exceptions.TransportError as exc:
        raise GoogleUnavailable() from exc
    except (ValueError, google_exceptions.GoogleAuthError) as exc:
        raise InvalidGoogleToken() from exc
    return claims_from_payload(claims, client_ids)


def claims_from_payload(payload: dict, client_ids: list[str]) -> GoogleClaims:
    """Checks every claim we rely on. Separate from signature verification so it is unit-testable."""
    if payload.get("iss") not in GOOGLE_ISSUERS:
        raise InvalidGoogleToken()
    if payload.get("aud") not in client_ids:
        raise InvalidGoogleToken()
    subject = payload.get("sub")
    email = payload.get("email")
    if not subject or not email or payload.get("email_verified") is not True:
        raise InvalidGoogleToken()
    return GoogleClaims(
        subject=str(subject),
        email=email,
        given_name=payload.get("given_name", "")[:150],
        family_name=payload.get("family_name", "")[:150],
    )
