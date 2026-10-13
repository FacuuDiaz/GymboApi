"""
Settings shared by every environment. Environment-specific modules
(development, test, production) import from here and override.

All configuration comes from environment variables (see .env.example).
Missing required variables fail at startup with a clear message.
"""
import os
from datetime import timedelta
from pathlib import Path

import environ

from apps.common.config import build_database_settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
ADMIN_URL = env.str("DJANGO_ADMIN_URL", default="admin/")

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
    "apps.common",
    "apps.accounts",
    "apps.training",
    "apps.coaching",
    "apps.sharing",
    "apps.exports",
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

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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
DATABASES = {"default": build_database_settings(os.environ)}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# Shared cache in PostgreSQL (table created by a migration): rate limits must
# be shared by every Gunicorn worker, and this needs no extra service.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "django_cache"}}

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
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.GymboJWTAuthentication"],
    # Private by default: every endpoint requires authentication unless it
    # explicitly opts out (health, register, login, public catalog).
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.DefaultCursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env.str("THROTTLE_ANON", default="60/min"),
        "user": env.str("THROTTLE_USER", default="600/min"),
        # Per client IP on login/register/refresh/Google endpoints.
        "auth": env.str("THROTTLE_AUTH", default="10/min"),
        # Per account identifier on login: slows password guessing against one
        # account even when the attacker rotates IPs.
        "login_identifier": env.str("THROTTLE_LOGIN_IDENTIFIER", default="5/min"),
        # Per account on data exports: each one reads the whole account.
        "export": env.str("THROTTLE_EXPORT", default="30/hour"),
        # Per account on people search, so the directory can't be scraped quickly.
        "search": env.str("THROTTLE_SEARCH", default="60/min"),
    },
    # Behind a reverse proxy, the client IP is the last X-Forwarded-For hop
    # added by that proxy (set NUM_PROXIES to the number of trusted proxies).
    "NUM_PROXIES": env.int("DJANGO_NUM_PROXIES", default=0) or None,
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

# Google Sign-In: the OAuth client IDs (Android, iOS, web) whose ID tokens are
# accepted as `aud`. They are public identifiers, not secrets. Empty = Google
# login disabled (the endpoint answers 503 google_not_configured).
GOOGLE_OAUTH_CLIENT_IDS = env.list("GOOGLE_OAUTH_CLIENT_IDS", default=[])

SPECTACULAR_SETTINGS = {
    "TITLE": "Gymbo API",
    "DESCRIPTION": "API REST de Gymbo. Ver README.md del repositorio GymboAPI.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "ENUM_NAME_OVERRIDES": {
        "MuscleGroupEnum": "apps.training.choices.MuscleGroup",
        "SessionStatusEnum": "apps.training.choices.SessionStatus",
        "RelationshipStatusEnum": "apps.coaching.models.CoachingRelationship.Status",
        "AssignmentStatusEnum": "apps.coaching.models.RoutineAssignment.Status",
        "ShareRoleEnum": "apps.sharing.models.ShareRole",
        "LinkRoleEnum": "apps.sharing.models.LinkRole",
        "ReportKindEnum": "apps.sharing.models.ContentReport.Kind",
        "MeasurementKindEnum": "apps.training.choices.MeasurementKind",
    },
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": ["rest_framework.authentication.SessionAuthentication"],
}

# ── Logging ──────────────────────────────────────────────────────────────────
# Request bodies and Authorization headers are never logged.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        "json": {"()": "apps.common.logging.JsonFormatter"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": env.str("LOG_FORMAT", default="plain")}},
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        # Requests with a foreign Host header (internet scanners) already get a
        # 400; logging each one with a traceback would only bury real errors.
        "django.security.DisallowedHost": {"handlers": [], "level": "CRITICAL", "propagate": False},
    },
}
