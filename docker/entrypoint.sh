#!/bin/sh
# Container entrypoint. Only non-destructive steps live here: it never runs
# flush, dropdb or `migrate --fake`, and never creates users.
set -eu

# Migrations are an explicit step by default (`docker compose exec api python
# manage.py migrate`). Opt in to running them at startup with
# DJANGO_MIGRATE_ON_START=true — `migrate` only applies pending, versioned
# migrations and is safe to repeat.
if [ "${DJANGO_MIGRATE_ON_START:-false}" = "true" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
