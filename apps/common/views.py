from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

_status = inline_serializer("HealthStatus", {"status": serializers.CharField()})


class _HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []


class LiveView(_HealthView):
    """The API process is up. Touches no dependency, exposes no details."""

    @extend_schema(responses={200: _status}, tags=["health"])
    def get(self, request):
        return Response({"status": "ok"})


class ReadyView(_HealthView):
    """The API can serve requests: the database answers. Never exposes connection details."""

    @extend_schema(responses={200: _status, 503: _status}, tags=["health"])
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            return Response({"status": "unavailable"}, status=503)
        return Response({"status": "ok"})
