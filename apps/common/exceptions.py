"""Consistent error envelope for every API error: {"error": {"code", "message", "fields"?}}."""
from rest_framework import exceptions
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None  # unhandled -> Django's 500 (no stack trace leaked when DEBUG is off)

    if isinstance(exc, exceptions.ValidationError):
        code, message = "validation_error", "Los datos enviados no son válidos."
        fields = response.data if isinstance(response.data, dict) else {"non_field_errors": response.data}
        response.data = {"error": {"code": code, "message": message, "fields": fields}}
        return response

    detail = getattr(exc, "detail", None)
    code = getattr(detail, "code", None) or getattr(exc, "default_code", "error")
    message = str(detail) if detail is not None else str(exc)
    response.data = {"error": {"code": code, "message": message}}
    # Optimistic-concurrency conflicts carry the server's current state so the client can merge.
    if hasattr(exc, "current"):
        response.data["error"]["current"] = exc.current
    return response
