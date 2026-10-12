"""
Token lifecycle on top of SimpleJWT:

- access token: short-lived (JWT_ACCESS_MINUTES), never stored server-side.
- refresh token: rotates on every use; the used one is blacklisted.
- reuse detection: presenting an already-rotated (blacklisted) refresh token
  means it was copied or replayed, so every session of that account is revoked.
"""
import logging

from django.db import transaction
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger("gymbo.auth")


def issue_token_pair(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


@transaction.atomic
def revoke_all_sessions(user) -> int:
    """
    Log out everywhere: blacklist every outstanding refresh token and reject
    every access token issued until now (via User.sessions_revoked_at).
    """
    from django.utils import timezone

    user.sessions_revoked_at = timezone.now()
    user.save(update_fields=["sessions_revoked_at"])
    pending = OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False)
    revoked = [BlacklistedToken(token=token) for token in pending]
    BlacklistedToken.objects.bulk_create(revoked, ignore_conflicts=True)
    return len(revoked)


def is_blacklisted(jti: str) -> bool:
    return BlacklistedToken.objects.filter(token__jti=jti).exists()


def handle_refresh_reuse(user_id) -> None:
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return
    count = revoke_all_sessions(user)
    # The user id is enough to investigate; the token itself is never logged.
    logger.warning("refresh_token_reuse_detected user_id=%s revoked_sessions=%s", user.pk, count)
