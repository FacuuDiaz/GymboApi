import pytest
from rest_framework.exceptions import NotAuthenticated, ValidationError
from rest_framework.test import APIClient

from common.config import production_config_problems
from common.exceptions import api_exception_handler

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
def test_health_endpoint_is_public_and_checks_database():
    response = APIClient().get("/api/v1/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_openapi_schema_is_served():
    response = APIClient().get("/api/v1/schema/")
    assert response.status_code == 200
    assert b"Gymbo API" in response.content
