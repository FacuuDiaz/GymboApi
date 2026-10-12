"""Pure configuration checks, kept outside settings.py so they are unit-testable."""

SECURE_SSLMODES = ("require", "verify-ca", "verify-full")


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
