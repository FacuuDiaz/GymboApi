# syntax=docker/dockerfile:1
#
# Stages:
#   dev   — runserver with autoreload, dev/test dependencies (pytest). Used by compose.yaml.
#   prod  — Gunicorn, runtime dependencies only, static files collected at build time.
# Dependencies come from uv.lock (exact, hashed versions), so builds are reproducible.

ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.21

FROM ${UV_IMAGE} AS uv

# ── base: runtime user and environment shared by every stage ────────────────
FROM ${PYTHON_IMAGE} AS base
ARG APP_UID=1000
ARG APP_GID=1000
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN groupadd --gid "${APP_GID}" app \
 && useradd --uid "${APP_UID}" --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app

# ── dev ──────────────────────────────────────────────────────────────────────
FROM base AS dev
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
COPY --chown=app:app . .
USER app
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ── prod dependencies (uv itself does not reach the final image) ─────────────
FROM base AS prod-deps
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
# No BuildKit cache mount here: some PaaS builders (Railway) reject cache mounts
# without their own id prefix. The lock file keeps this step cached as a layer.
RUN UV_COMPILE_BYTECODE=1 uv sync --frozen --no-dev --no-install-project --no-cache

# ── prod ─────────────────────────────────────────────────────────────────────
FROM base AS prod
COPY --from=prod-deps /opt/venv /opt/venv
COPY --chown=app:app . .
# collectstatic needs importable settings but no database and no secrets:
# the values below are build-time placeholders, never used at runtime.
RUN DJANGO_SETTINGS_MODULE=config.settings.collectstatic \
    DJANGO_SECRET_KEY=build-time-placeholder \
    POSTGRES_HOST=unused POSTGRES_DB=unused POSTGRES_USER=unused POSTGRES_PASSWORD=unused \
    python manage.py collectstatic --noinput \
 && chown -R app:app /app/staticfiles
USER app
ENV DJANGO_SETTINGS_MODULE=config.settings.production
EXPOSE 8000
# Gunicorn listens on $PORT when the platform sets it (see docker/gunicorn.conf.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--config", "docker/gunicorn.conf.py"]
