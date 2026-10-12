"""
Gymbo API settings.

Everything environment-specific comes from environment variables (or a local
`.env` file in development — see `.env.example`). `DJANGO_ENV` selects the
profile: `development`, `test` or `production`. Production refuses to start
with an insecure configuration; see `validate_configuration()` at the bottom.
"""
from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

from common.config import production_config_problems

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
# A local .env is a development convenience only; real deployments inject
# variables through the environment and never ship a .env file.
if (BASE_DIR / ".env").exists():
    env.read_env(BASE_DIR / ".env")

ENVIRONMENTS = ("development", "test", "production")
DJANGO_ENV = env.str("DJANGO_ENV", default="development")
IS_PRODUCTION = DJANGO_ENV == "production"

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "common",
    "accounts",
    "training",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gymbo_api.urls"
WSGI_APPLICATION = "gymbo_api.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ── Database ─────────────────────────────────────────────────────────────────
# PostgreSQL only, in every environment: SQLite is never used as a silent
# substitute (constraints like case-insensitive uniqueness and partial unique
# indexes behave differently there).
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["sslmode"] = env.str("DATABASE_SSLMODE", default="prefer")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# ── Passwords ────────────────────────────────────────────────────────────────
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── I18N / time ──────────────────────────────────────────────────────────────
LANGUAGE_CODE = "es"
TIME_ZONE = "UTC"
USE_I18N = True
# Timestamps are stored in UTC. Calendar dates of a workout (scheduled_date)
# are the user's *local* date and are never recomputed by the server.
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ── REST framework ───────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    # Private by default: every endpoint requires authentication unless it
    # explicitly opts out (health check, register, login, public catalog).
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "common.pagination.DefaultCursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env.str("THROTTLE_ANON", default="60/min"),
        "user": env.str("THROTTLE_USER", default="600/min"),
        "auth": env.str("THROTTLE_AUTH", default="10/min"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=10)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=30)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "SIGNING_KEY": env.str("JWT_SIGNING_KEY", default=SECRET_KEY),
    "USER_ID_FIELD": "id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Gymbo API",
    "DESCRIPTION": "API REST de Gymbo. Ver README.md del repositorio GymboAPI.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    # Interactive docs are open in development; in production only staff
    # (logged in through /admin/) can read them.
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser" if DJANGO_ENV == "production" else "rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": ["rest_framework.authentication.SessionAuthentication"],
}

# ── Logging ──────────────────────────────────────────────────────────────────
# Request bodies and Authorization headers are never logged.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING", "propagate": True}},
}

# ── Production hardening ─────────────────────────────────────────────────────
if IS_PRODUCTION:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if env.bool("DJANGO_BEHIND_PROXY", default=True) else None
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = "DENY"


def validate_configuration() -> None:
    """Fail fast at startup on a missing or unsafe configuration."""
    if DJANGO_ENV not in ENVIRONMENTS:
        raise ImproperlyConfigured(f"DJANGO_ENV must be one of {ENVIRONMENTS}, got {DJANGO_ENV!r}.")
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("DATABASE_URL must point to PostgreSQL (postgres://...).")
    if not IS_PRODUCTION:
        return
    problems = production_config_problems(
        debug=DEBUG,
        secret_key=SECRET_KEY,
        allowed_hosts=ALLOWED_HOSTS,
        db_sslmode=DATABASES["default"]["OPTIONS"]["sslmode"],
    )
    if problems:
        raise ImproperlyConfigured("Insecure production configuration: " + "; ".join(problems) + ".")


validate_configuration()
