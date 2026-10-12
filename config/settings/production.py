"""
Production. Refuses to start with an insecure configuration. HTTPS is
terminated by a reverse proxy / load balancer in front of Gunicorn.
"""
from django.core.exceptions import ImproperlyConfigured

from apps.common.config import production_config_problems

from .base import *  # noqa: F401,F403
from .base import ADMIN_URL, ALLOWED_HOSTS, DATABASES, DEBUG, LOGGING, MIDDLEWARE, SECRET_KEY, SPECTACULAR_SETTINGS, env

problems = production_config_problems(
    debug=DEBUG,
    secret_key=SECRET_KEY,
    allowed_hosts=ALLOWED_HOSTS,
    db_sslmode=DATABASES["default"]["OPTIONS"]["sslmode"],
)
if ADMIN_URL == "admin/":
    problems.append("DJANGO_ADMIN_URL must be set to a non-default, hard-to-guess path (e.g. 'gestion-8f3k2/')")
if problems:
    raise ImproperlyConfigured("Insecure production configuration: " + "; ".join(problems) + ".")

# The proxy sets X-Forwarded-Proto; Django trusts it only because the API is
# reachable exclusively through that proxy (no published port in compose.prod.yaml).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# Health checks come from inside the container over plain HTTP.
SECURE_REDIRECT_EXEMPT = [r"^health/"]
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
X_FRAME_OPTIONS = "DENY"

from .production_storages import STORAGES  # noqa: E402,F401

# Serves the admin's static files from Gunicorn, so production needs no
# separate static-file server.
MIDDLEWARE = [MIDDLEWARE[0], "whitenoise.middleware.WhiteNoiseMiddleware", *MIDDLEWARE[1:]]

# API docs only for staff logged in through the admin.
SPECTACULAR_SETTINGS = {**SPECTACULAR_SETTINGS, "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"]}

LOGGING["handlers"]["console"]["formatter"] = env.str("LOG_FORMAT", default="json")
