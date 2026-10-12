import pytest
from rest_framework.exceptions import NotAuthenticated, ValidationError
from rest_framework.test import APIClient

from django.core.exceptions import ImproperlyConfigured

from apps.common.config import build_database_settings, production_config_problems
from apps.common.exceptions import api_exception_handler

SAFE = dict(debug=False, secret_key="x" * 64, allowed_hosts=["api.gymbo.app"], db_sslmode="verify-full")


def test_safe_production_config_passes():
    assert production_config_problems(**SAFE) == []


@pytest.mark.parametrize(
    "override",
    [
        {"debug": True},
        {"secret_key": "short"},
        {"secret_key": "change-me" + "x" * 60},
        {"allowed_hosts": []},
        {"allowed_hosts": ["*"]},
        {"db_sslmode": "prefer"},
        {"db_sslmode": "disable"},
    ],
)
def test_unsafe_production_config_is_rejected(override):
    assert len(production_config_problems(**{**SAFE, **override})) == 1


def test_error_envelope_for_auth_errors():
    response = api_exception_handler(NotAuthenticated(), {})
    assert response.status_code == 401
    assert response.data == {"error": {"code": "not_authenticated", "message": str(NotAuthenticated.default_detail)}}


def test_error_envelope_for_validation_errors():
    response = api_exception_handler(ValidationError({"email": ["Requerido."]}), {})
    assert response.status_code == 400
    assert response.data["error"]["code"] == "validation_error"
    assert response.data["error"]["fields"] == {"email": ["Requerido."]}


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/health/live/", "/health/ready/"])
def test_health_endpoints_are_public_and_reveal_nothing(path):
    response = APIClient().get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_unavailable_when_database_fails(monkeypatch):
    from apps.common import views

    class BrokenConnection:
        def cursor(self):
            raise RuntimeError("password=secret host=db")

    monkeypatch.setattr(views, "connection", BrokenConnection())
    response = APIClient().get("/health/ready/")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}  # no error details leaked


INDIVIDUAL = {"POSTGRES_HOST": "db", "POSTGRES_DB": "gymbo", "POSTGRES_USER": "app", "POSTGRES_PASSWORD": "pw"}


def test_database_from_individual_variables():
    config = build_database_settings(INDIVIDUAL)
    assert (config["HOST"], config["PORT"], config["NAME"], config["USER"]) == ("db", "5432", "gymbo", "app")
    assert config["ENGINE"] == "django.db.backends.postgresql"


def test_database_from_url():
    config = build_database_settings({"DATABASE_URL": "postgres://u:p@pg.example.com:6543/gymbo", "DATABASE_SSLMODE": "require"})
    assert (config["HOST"], config["PORT"], config["NAME"]) == ("pg.example.com", 6543, "gymbo")
    assert config["OPTIONS"]["sslmode"] == "require"


def test_database_url_and_host_together_are_rejected():
    with pytest.raises(ImproperlyConfigured):
        build_database_settings({**INDIVIDUAL, "DATABASE_URL": "postgres://u:p@other/gymbo"})


def test_database_missing_configuration_is_rejected_clearly():
    with pytest.raises(ImproperlyConfigured, match="POSTGRES_PASSWORD"):
        build_database_settings({k: v for k, v in INDIVIDUAL.items() if k != "POSTGRES_PASSWORD"})


def test_sqlite_is_never_accepted():
    with pytest.raises(ImproperlyConfigured):
        build_database_settings({"DATABASE_URL": "sqlite:///db.sqlite3"})


@pytest.mark.django_db
def test_openapi_schema_is_served():
    response = APIClient().get("/api/v1/schema/")
    assert response.status_code == 200
    assert b"Gymbo API" in response.content


def test_suite_runs_with_test_settings_and_a_separate_database():
    from django.conf import settings
    from django.db import connection

    assert settings.SETTINGS_MODULE == "config.settings.test"
    assert connection.settings_dict["NAME"].startswith("test_")
