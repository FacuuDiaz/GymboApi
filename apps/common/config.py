"""Pure configuration helpers, kept outside settings so they are unit-testable."""
from collections.abc import Mapping

import environ
from django.core.exceptions import ImproperlyConfigured

SECURE_SSLMODES = ("require", "verify-ca", "verify-full")
REQUIRED_POSTGRES_PARTS = ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")


def build_database_settings(environ_vars: Mapping[str, str]) -> dict:
    """
    One source of truth for the connection, never a mix:

    - `DATABASE_URL` (typical for an external/managed PostgreSQL), or
    - the individual `POSTGRES_*` variables (what compose.yaml provides).

    Setting `DATABASE_URL` together with `POSTGRES_HOST` is rejected instead of
    silently preferring one, so a stale variable can never point the API at
    the wrong database.
    """
    url = environ_vars.get("DATABASE_URL", "").strip()
    if url:
        if environ_vars.get("POSTGRES_HOST"):
            raise ImproperlyConfigured("Set either DATABASE_URL or POSTGRES_HOST/POSTGRES_* — not both.")
        config = environ.Env.db_url_config(url)
    else:
        missing = [k for k in REQUIRED_POSTGRES_PARTS if not environ_vars.get(k)]
        if missing:
            raise ImproperlyConfigured(f"Database not configured. Set DATABASE_URL or {', '.join(missing)}.")
        config = {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": environ_vars["POSTGRES_HOST"],
            "PORT": environ_vars.get("POSTGRES_PORT") or "5432",
            "NAME": environ_vars["POSTGRES_DB"],
            "USER": environ_vars["POSTGRES_USER"],
            "PASSWORD": environ_vars["POSTGRES_PASSWORD"],
        }
    # PostgreSQL only, in every environment: SQLite is never a silent substitute.
    if config.get("ENGINE") != "django.db.backends.postgresql":
        raise ImproperlyConfigured("The database must be PostgreSQL (postgres://...).")
    config.setdefault("OPTIONS", {})
    config["OPTIONS"]["sslmode"] = environ_vars.get("DATABASE_SSLMODE") or "prefer"
    return config


def production_config_problems(*, debug: bool, secret_key: str, allowed_hosts: list[str], db_sslmode: str) -> list[str]:
    problems = []
    if debug:
        problems.append("DJANGO_DEBUG must be false")
    if len(secret_key) < 50 or secret_key.startswith(("change-me", "django-insecure", "dev-")):
        problems.append("DJANGO_SECRET_KEY must be a random value of at least 50 characters")
    if not allowed_hosts or "*" in allowed_hosts:
        problems.append("DJANGO_ALLOWED_HOSTS must list explicit hostnames")
    if db_sslmode not in SECURE_SSLMODES:
        problems.append("DATABASE_SSLMODE must be require, verify-ca or verify-full")
    return problems
