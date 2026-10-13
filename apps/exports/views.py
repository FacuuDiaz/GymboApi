import json

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import exceptions
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import format as fmt
from .builder import build_document

TYPES = ("json", "csv", "zip")


class ExportView(APIView):
    """
    The requester's own data, generated on demand and streamed back: nothing is
    stored on the server, so there is no file to leak, expire or clean up.
    (`type`, not `format`: DRF reserves `?format=` for content negotiation.)
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "export"

    @extend_schema(
        tags=["exports"],
        parameters=[
            OpenApiParameter("type", str, enum=list(TYPES), description="json (todo), csv (una tabla), zip (datos.json + todas las tablas CSV + LEEME.txt)"),
            OpenApiParameter("table", str, enum=list(fmt.TABLES), description="Solo con type=csv. Por defecto: series."),
        ],
        responses={200: OpenApiResponse(description="Archivo adjunto (Content-Disposition: attachment).")},
    )
    def get(self, request):
        kind = request.query_params.get("type", "json")
        if kind not in TYPES:
            raise exceptions.ValidationError({"type": [f"Elegí uno de: {', '.join(TYPES)}."]})
        table = request.query_params.get("table", "series")
        if kind == "csv" and table not in fmt.TABLES:
            raise exceptions.ValidationError({"table": [f"Elegí una de: {', '.join(fmt.TABLES)}."]})

        doc = build_document(request.user)
        json_text = json.dumps(doc, ensure_ascii=False, indent=2)
        stem = f"gymbo-{request.user.username}-{timezone.localdate().isoformat()}"
        if kind == "json":
            response = HttpResponse(json_text, content_type="application/json; charset=utf-8")
            name = f"{stem}.json"
        elif kind == "csv":
            response = HttpResponse(fmt.tables(doc)[table], content_type="text/csv; charset=utf-8")
            name = f"{stem}-{table}.csv"
        else:
            response = HttpResponse(fmt.to_zip(doc, json_text), content_type="application/zip")
            name = f"{stem}.zip"
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        # Personal data: never stored by browsers, proxies or the app's HTTP cache.
        response["Cache-Control"] = "no-store"
        return response
