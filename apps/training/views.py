"""
Training API. Every queryset starts from `owner=request.user` (or what the
user may see), so another person's records answer 404 exactly like
non-existent ones — no IDOR/BOLA, and no leaking that an id exists.
"""
from datetime import date, timedelta

from django.db.models import Prefetch
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import TrainingPreferences
from apps.common.pagination import DefaultCursorPagination

from . import progress, services
from .models import BodyMeasurement, Exercise, ExerciseSession, Routine, WorkoutSession
from .serializers import (
    ExerciseSerializer,
    ExerciseUpdateSerializer,
    ExerciseWriteSerializer,
    MeasurementSerializer,
    MeasurementUpdateSerializer,
    MeasurementWriteSerializer,
    PreferencesSerializer,
    RoutineSerializer,
    RoutineUpdateSerializer,
    RoutineVersionSerializer,
    RoutineWriteSerializer,
    SessionUpdateSerializer,
    SessionWriteSerializer,
    WorkoutSessionSerializer,
)

UPDATED_SINCE = OpenApiParameter(
    "updated_since", str, description="ISO datetime. Devuelve lo modificado desde entonces, incluidos los borrados (deleted_at).",
)


def _updated_since_filter(request, queryset):
    """Lists hide tombstones, except when a client asks for changes since a point in time (sync)."""
    raw = request.query_params.get("updated_since")
    if not raw:
        return queryset.filter(deleted_at__isnull=True)
    since = parse_datetime(raw)
    if since is None:
        raise exceptions.ValidationError({"updated_since": ["Fecha/hora ISO inválida."]})
    return queryset.filter(updated_at__gte=since)


def _date_param(request, name):
    raw = request.query_params.get(name)
    if raw is None:
        return None
    value = parse_date(raw)
    if value is None:
        raise exceptions.ValidationError({name: ["Fecha inválida (aaaa-mm-dd)."]})
    return value


def _base_version_param(request):
    raw = request.query_params.get("base_version")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise exceptions.ValidationError({"base_version": ["Debe ser un entero."]})


class _OwnedViewSet(viewsets.GenericViewSet):
    lookup_value_converter = "uuid"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    write_serializer_class = None
    update_serializer_class = None

    def represent(self, instance) -> dict:
        return self.get_serializer(instance).data

    def _validated(self, serializer_class, **kwargs) -> dict:
        serializer = serializer_class(data=self.request.data, context=self.get_serializer_context(), **kwargs)
        serializer.is_valid(raise_exception=True)
        return dict(serializer.validated_data)

    def _created_response(self, instance, created: bool) -> Response:
        # 201 when created, 200 when the same id was already stored (a retry): idempotent.
        return Response(self.represent(instance), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


# ── Exercises ────────────────────────────────────────────────────────────────

class ExercisePagination(DefaultCursorPagination):
    ordering = ("-is_builtin", "name", "id")
    page_size = 200
    max_page_size = 500


@extend_schema_view(
    list=extend_schema(
        tags=["exercises"],
        parameters=[
            OpenApiParameter("mine", bool, description="Solo mis ejercicios personalizados."),
            OpenApiParameter("q", str, description="Buscar por nombre."),
            OpenApiParameter("muscle", str), OpenApiParameter("equipment", str), UPDATED_SINCE,
        ],
    ),
    retrieve=extend_schema(tags=["exercises"]),
)
class ExerciseViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, _OwnedViewSet):
    """Built-in library + my exercises (+ published ones). Only my own exercises can be changed."""

    serializer_class = ExerciseSerializer
    pagination_class = ExercisePagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI generation, no real user
            return Exercise.objects.none()
        qs = services.visible_exercises(self.request.user)
        if self.action == "list":
            params = self.request.query_params
            if params.get("mine") in ("1", "true"):
                qs = qs.filter(owner=self.request.user)
            if q := params.get("q", "").strip():
                qs = qs.filter(name__icontains=q[:80])
            if muscle := params.get("muscle"):
                qs = qs.filter(primary_muscle=muscle)
            if equipment := params.get("equipment"):
                qs = qs.filter(equipment=equipment)
            return _updated_since_filter(self.request, qs)
        return qs.filter(deleted_at__isnull=True)

    def _own(self, pk):
        exercise = self.get_queryset().filter(pk=pk).first()
        if exercise is None:
            raise exceptions.NotFound()
        if exercise.owner_id != self.request.user.pk:
            raise exceptions.PermissionDenied("Solo podés modificar tus propios ejercicios.")
        return exercise

    @extend_schema(tags=["exercises"], request=ExerciseWriteSerializer, responses={201: ExerciseSerializer, 200: ExerciseSerializer})
    def create(self, request):
        data = self._validated(ExerciseWriteSerializer)
        exercise_id = data.pop("id", None)
        if exercise_id is not None:
            existing = Exercise.objects.filter(id=exercise_id).first()
            if existing is not None:
                if existing.owner_id != request.user.pk:
                    raise services.id_conflict()
                return self._created_response(existing, False)
        exercise = Exercise.objects.create(owner=request.user, **({"id": exercise_id} if exercise_id else {}), **data)
        return self._created_response(exercise, True)

    @extend_schema(tags=["exercises"], request=ExerciseUpdateSerializer, responses={200: ExerciseSerializer, 409: OpenApiResponse(description="version_conflict")})
    def partial_update(self, request, pk=None):
        exercise = self._own(pk)
        data = self._validated(ExerciseUpdateSerializer)
        services.check_base_version(exercise, data.pop("base_version"), self.represent)
        for field, value in data.items():
            setattr(exercise, field, value)
        exercise.version += 1
        exercise.save()
        return Response(self.represent(exercise))

    @extend_schema(tags=["exercises"], responses={204: None})
    def destroy(self, request, pk=None):
        """Tombstone. Routines and past sessions keep their snapshot of the exercise."""
        from django.utils import timezone

        exercise = self._own(pk)
        exercise.deleted_at = timezone.now()
        exercise.version += 1
        exercise.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Routines ─────────────────────────────────────────────────────────────────

@extend_schema_view(
    list=extend_schema(tags=["routines"], parameters=[UPDATED_SINCE]),
    retrieve=extend_schema(tags=["routines"]),
)
class RoutineViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, _OwnedViewSet):
    serializer_class = RoutineSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI generation, no real user
            return Routine.objects.none()
        qs = Routine.objects.filter(owner=self.request.user).prefetch_related("date_overrides")
        if self.action == "list":
            return _updated_since_filter(self.request, qs)
        return qs.filter(deleted_at__isnull=True)

    @extend_schema(tags=["routines"], request=RoutineWriteSerializer, responses={201: RoutineSerializer, 200: RoutineSerializer})
    def create(self, request):
        routine, created = services.create_routine(request.user, self._validated(RoutineWriteSerializer))
        return self._created_response(routine, created)

    @extend_schema(tags=["routines"], request=RoutineUpdateSerializer, responses={200: RoutineSerializer, 409: OpenApiResponse(description="version_conflict")})
    def partial_update(self, request, pk=None):
        data = self._validated(RoutineUpdateSerializer, partial=True)
        routine = services.update_routine(request.user, pk, data, self.represent)
        return Response(self.represent(routine))

    @extend_schema(tags=["routines"], parameters=[OpenApiParameter("base_version", int)], responses={204: None, 409: OpenApiResponse(description="version_conflict")})
    def destroy(self, request, pk=None):
        services.delete_routine(request.user, pk, _base_version_param(request), self.represent)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=["routines"], responses={200: RoutineVersionSerializer(many=True)})
    @action(detail=True, methods=["get"], pagination_class=None)
    def versions(self, request, pk=None):
        """Every immutable version of the plan, oldest first."""
        routine = self.get_object()
        return Response(RoutineVersionSerializer(routine.versions.order_by("number"), many=True).data)


# ── Workout sessions ─────────────────────────────────────────────────────────

class SessionPagination(DefaultCursorPagination):
    ordering = ("-scheduled_date", "-started_at")


@extend_schema_view(
    list=extend_schema(
        tags=["workouts"],
        parameters=[
            OpenApiParameter("from", str, description="scheduled_date desde (aaaa-mm-dd)"),
            OpenApiParameter("to", str, description="scheduled_date hasta (aaaa-mm-dd)"),
            OpenApiParameter("status", str), OpenApiParameter("routine", str), UPDATED_SINCE,
        ],
    ),
    retrieve=extend_schema(tags=["workouts"]),
)
class WorkoutSessionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, _OwnedViewSet):
    serializer_class = WorkoutSessionSerializer
    pagination_class = SessionPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI generation, no real user
            return WorkoutSession.objects.none()
        qs = (
            WorkoutSession.objects.filter(owner=self.request.user)
            .select_related("routine_version")
            .prefetch_related(Prefetch("exercises", queryset=ExerciseSession.objects.prefetch_related("sets")))
        )
        if self.action != "list":
            return qs.filter(deleted_at__isnull=True)
        if start := _date_param(self.request, "from"):
            qs = qs.filter(scheduled_date__gte=start)
        if end := _date_param(self.request, "to"):
            qs = qs.filter(scheduled_date__lte=end)
        if status_ := self.request.query_params.get("status"):
            qs = qs.filter(status=status_)
        if routine := self.request.query_params.get("routine"):
            qs = qs.filter(routine_id=routine)
        return _updated_since_filter(self.request, qs)

    def _fresh(self, session):
        return self.get_queryset().get(pk=session.pk)

    @extend_schema(tags=["workouts"], request=SessionWriteSerializer, responses={201: WorkoutSessionSerializer, 200: WorkoutSessionSerializer})
    def create(self, request):
        session, created = services.create_session(request.user, self._validated(SessionWriteSerializer))
        return self._created_response(self._fresh(session), created)

    @extend_schema(tags=["workouts"], request=SessionUpdateSerializer, responses={200: WorkoutSessionSerializer, 409: OpenApiResponse(description="version_conflict")})
    def partial_update(self, request, pk=None):
        instance = self.get_queryset().filter(pk=pk).first()
        if instance is None:
            raise exceptions.NotFound()
        serializer = SessionUpdateSerializer(instance=instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        session = services.update_session(request.user, pk, dict(serializer.validated_data), lambda s: self.represent(self._fresh(s)))
        return Response(self.represent(self._fresh(session)))

    @extend_schema(tags=["workouts"], responses={204: None})
    def destroy(self, request, pk=None):
        services.delete_session(request.user, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Measurements ─────────────────────────────────────────────────────────────

class MeasurementPagination(DefaultCursorPagination):
    ordering = ("-date", "-created_at")


@extend_schema_view(
    list=extend_schema(tags=["measurements"], parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str), OpenApiParameter("kind", str), UPDATED_SINCE]),
    retrieve=extend_schema(tags=["measurements"]),
)
class MeasurementViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, _OwnedViewSet):
    serializer_class = MeasurementSerializer
    pagination_class = MeasurementPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI generation, no real user
            return BodyMeasurement.objects.none()
        qs = BodyMeasurement.objects.filter(owner=self.request.user)
        if self.action != "list":
            return qs.filter(deleted_at__isnull=True)
        if start := _date_param(self.request, "from"):
            qs = qs.filter(date__gte=start)
        if end := _date_param(self.request, "to"):
            qs = qs.filter(date__lte=end)
        if kind := self.request.query_params.get("kind"):
            qs = qs.filter(kind=kind)
        return _updated_since_filter(self.request, qs)

    @extend_schema(
        tags=["measurements"], request=MeasurementWriteSerializer,
        responses={201: MeasurementSerializer, 200: OpenApiResponse(MeasurementSerializer, description="Ya existía un valor para esa fecha: se reemplazó.")},
    )
    def create(self, request):
        measurement, created = services.upsert_measurement(request.user, self._validated(MeasurementWriteSerializer))
        return self._created_response(measurement, created)

    @extend_schema(tags=["measurements"], request=MeasurementUpdateSerializer, responses={200: MeasurementSerializer})
    def partial_update(self, request, pk=None):
        measurement = self.get_object()
        data = self._validated(MeasurementUpdateSerializer)
        services.check_base_version(measurement, data.pop("base_version"), self.represent)
        for field, value in data.items():
            setattr(measurement, field, value)
        measurement.version += 1
        measurement.save()
        return Response(self.represent(measurement))

    @extend_schema(tags=["measurements"], responses={204: None})
    def destroy(self, request, pk=None):
        from django.utils import timezone

        measurement = self.get_object()
        measurement.deleted_at = timezone.now()
        measurement.version += 1
        measurement.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Preferences and progress ────────────────────────────────────────────────

class PreferencesView(APIView):
    """Synced training preferences. Conflicts resolve last-write-wins per field."""

    def _prefs(self, user):
        prefs, _ = TrainingPreferences.objects.get_or_create(user=user)
        return prefs

    @extend_schema(tags=["users"], responses={200: PreferencesSerializer})
    def get(self, request):
        return Response(PreferencesSerializer(self._prefs(request.user)).data)

    @extend_schema(tags=["users"], request=PreferencesSerializer, responses={200: PreferencesSerializer})
    def patch(self, request):
        prefs = self._prefs(request.user)
        serializer = PreferencesSerializer(prefs, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(version=prefs.version + 1)
        return Response(serializer.data)


def _unit_params(request):
    prefs = TrainingPreferences.objects.filter(user=request.user).first()
    default_unit = prefs.unit if prefs else "kg"
    unit = request.query_params.get("unit", default_unit)
    if unit not in ("kg", "lb"):
        raise exceptions.ValidationError({"unit": ["Usá kg o lb."]})
    return unit, default_unit


class WeeklyProgressView(APIView):
    MAX_WEEKS = 104

    @extend_schema(
        tags=["progress"],
        parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str), OpenApiParameter("unit", str, enum=["kg", "lb"])],
        responses={200: OpenApiResponse(description="Semanas (lunes a domingo) con sesiones, series, volumen y duración; null = sin datos.")},
    )
    def get(self, request):
        end = _date_param(request, "to") or date.today()
        start = _date_param(request, "from") or end - timedelta(weeks=8)
        if start > end:
            raise exceptions.ValidationError({"from": ["Debe ser anterior a 'to'."]})
        if (end - start).days > self.MAX_WEEKS * 7:
            raise exceptions.ValidationError({"from": [f"El rango máximo es de {self.MAX_WEEKS} semanas."]})
        unit, default_unit = _unit_params(request)
        return Response({"unit": unit, "weeks": progress.weekly_summary(request.user, start, end, unit, default_unit)})


class ExerciseProgressView(APIView):
    @extend_schema(
        tags=["progress"],
        parameters=[OpenApiParameter("unit", str, enum=["kg", "lb"])],
        responses={200: OpenApiResponse(description="Un punto por sesión registrada: peso máximo, 1RM estimado, volumen.")},
    )
    def get(self, request, exercise_ref: str):
        unit, default_unit = _unit_params(request)
        return Response({
            "exercise_id": exercise_ref,
            "unit": unit,
            "points": progress.exercise_history(request.user, exercise_ref, unit, default_unit),
        })
