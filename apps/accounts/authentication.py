from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class GymboJWTAuthentication(JWTAuthentication):
    """JWT auth that also rejects access tokens issued before the user's last mass revocation."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        revoked_at = user.sessions_revoked_at
        if revoked_at is not None and int(validated_token.get("iat", 0)) < int(revoked_at.timestamp()):
            raise InvalidToken({"detail": "La sesión fue cerrada. Iniciá sesión de nuevo.", "code": "session_revoked"})
        return user
