import hashlib

from rest_framework.throttling import SimpleRateThrottle


class AuthIPThrottle(SimpleRateThrottle):
    """Per client IP, on every unauthenticated auth endpoint."""

    scope = "auth"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class LoginIdentifierThrottle(SimpleRateThrottle):
    """
    Per targeted account (the username/email typed), regardless of IP, so
    password guessing against one account stays slow from a botnet too.
    The identifier is hashed so cache keys never contain emails.
    """

    scope = "login_identifier"

    def get_cache_key(self, request, view):
        login = str(request.data.get("login", "")).strip().lower() if hasattr(request, "data") else ""
        if not login:
            return None
        digest = hashlib.sha256(login.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}
