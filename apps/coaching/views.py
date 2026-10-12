from datetime import date, timedelta

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import TrainingPreferences
from apps.training import progress
from apps.training.models import BodyMeasurement, WorkoutSession
from apps.training.serializers import MeasurementSerializer, WorkoutSessionSerializer
from apps.training.views import SessionPagination, _date_param

from . import services
from .models import CoachingGroup, CoachingRelationship, RoutineAssignment
from .serializers import (
    AcceptSerializer,
    AssignmentSerializer,
    AssignmentUpdateSerializer,
    AssignSerializer,
    GroupSerializer,
    InviteSerializer,
    RelationshipSerializer,
    RelationshipUpdateSerializer,
)


def _valid(serializer_class, data, **kwargs):
    serializer = serializer_class(data=data, **kwargs)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@extend_schema_view(
    list=extend_schema(tags=["coaching"], parameters=[OpenApiParameter("role", str, enum=["coach", "student"]), OpenApiParameter("status", str)]),
    retrieve=extend_schema(tags=["coaching"]),
)
class RelationshipViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Coach ↔ student relationships the requester takes part in (as either role)."""

    serializer_class = RelationshipSerializer
    lookup_value_converter = "uuid"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CoachingRelationship.objects.none()
        qs = services.relationships_of(self.request.user)
        role = self.request.query_params.get("role")
        if role == "coach":
            qs = qs.filter(coach=self.request.user)
        elif role == "student":
            qs = qs.filter(student=self.request.user)
        if status_ := self.request.query_params.get("status"):
            qs = qs.filter(status=status_)
        return qs

    def _respond(self, rel, code=status.HTTP_200_OK):
        return Response(self.get_serializer(rel).data, status=code)

    @extend_schema(tags=["coaching"], request=InviteSerializer, responses={201: RelationshipSerializer, 409: OpenApiResponse(description="relationship_exists")})
    @action(detail=False, methods=["post"])
    def invite(self, request):
        data = _valid(InviteSerializer, request.data)
        return self._respond(services.invite(request.user, data["username"], data.get("message", "")), status.HTTP_201_CREATED)

    @extend_schema(tags=["coaching"], request=AcceptSerializer, responses={200: RelationshipSerializer})
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        data = _valid(AcceptSerializer, request.data)
        rel = services.accept(request.user, pk, can_view_progress=data["progress"], can_view_sessions=data["sessions"], can_view_measurements=data["measurements"])
        return self._respond(rel)

    @extend_schema(tags=["coaching"], request=None, responses={200: RelationshipSerializer})
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._respond(services.reject(request.user, pk))

    @extend_schema(tags=["coaching"], request=None, responses={200: RelationshipSerializer})
    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        """Either side ends the relationship (or the coach cancels a pending invitation)."""
        return self._respond(services.end_relationship(request.user, pk))

    @extend_schema(tags=["coaching"], request=RelationshipUpdateSerializer, responses={200: RelationshipSerializer})
    def partial_update(self, request, pk=None):
        """Students change what they share; coaches change the group."""
        data = _valid(RelationshipUpdateSerializer, request.data)
        rel = None
        if "permissions" in data:
            rel = services.update_permissions(request.user, pk, data["permissions"])
        if "group" in data:
            group = CoachingGroup.objects.filter(id=data["group"], coach=request.user).first() if data["group"] else None
            if data["group"] and group is None:
                raise exceptions.ValidationError({"group": ["Grupo inexistente."]})
            rel = services.set_group(request.user, pk, group)
        if rel is None:
            raise exceptions.ValidationError({"non_field_errors": ["Nada para actualizar."]})
        return self._respond(rel)

    # ── The student's data, as far as the student allows ────────────────────

    @extend_schema(tags=["coaching"], parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str)], responses={200: OpenApiResponse(description="Igual que /progress/weekly/, del alumno.")})
    @action(detail=True, methods=["get"], url_path="progress/weekly")
    def progress_weekly(self, request, pk=None):
        rel = services.student_data_access(request.user, pk, "can_view_progress")
        end = _date_param(request, "to") or date.today()
        start = _date_param(request, "from") or end - timedelta(weeks=8)
        if start > end or (end - start).days > 104 * 7:
            raise exceptions.ValidationError({"from": ["Rango inválido (máximo 104 semanas)."]})
        prefs = TrainingPreferences.objects.filter(user=rel.student).first()
        unit = prefs.unit if prefs else "kg"
        return Response({"unit": unit, "weeks": progress.weekly_summary(rel.student, start, end, unit, unit)})

    @extend_schema(tags=["coaching"], responses={200: OpenApiResponse(description="Un punto por sesión del alumno.")})
    @action(detail=True, methods=["get"], url_path=r"progress/exercises/(?P<exercise_ref>[^/]+)")
    def progress_exercise(self, request, pk=None, exercise_ref=None):
        rel = services.student_data_access(request.user, pk, "can_view_progress")
        prefs = TrainingPreferences.objects.filter(user=rel.student).first()
        unit = prefs.unit if prefs else "kg"
        return Response({"exercise_id": exercise_ref, "unit": unit, "points": progress.exercise_history(rel.student, exercise_ref, unit, unit)})

    @extend_schema(tags=["coaching"], responses={200: WorkoutSessionSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def sessions(self, request, pk=None):
        rel = services.student_data_access(request.user, pk, "can_view_sessions")
        qs = WorkoutSession.objects.filter(owner=rel.student, deleted_at__isnull=True).select_related("routine_version").prefetch_related("exercises__sets")
        return self._paginated(qs, SessionPagination, WorkoutSessionSerializer)

    @extend_schema(tags=["coaching"], responses={200: MeasurementSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def measurements(self, request, pk=None):
        rel = services.student_data_access(request.user, pk, "can_view_measurements")
        from apps.training.views import MeasurementPagination

        qs = BodyMeasurement.objects.filter(owner=rel.student, deleted_at__isnull=True)
        return self._paginated(qs, MeasurementPagination, MeasurementSerializer)

    def _paginated(self, qs, pagination_class, serializer_class):
        paginator = pagination_class()
        page = paginator.paginate_queryset(qs, self.request, view=self)
        return paginator.get_paginated_response(serializer_class(page, many=True).data)


@extend_schema_view(list=extend_schema(tags=["coaching"]), retrieve=extend_schema(tags=["coaching"]), create=extend_schema(tags=["coaching"]), partial_update=extend_schema(tags=["coaching"]), destroy=extend_schema(tags=["coaching"]))
class GroupViewSet(viewsets.ModelViewSet):
    """The coach's own groups (by schedule, gym, level...)."""

    serializer_class = GroupSerializer
    lookup_value_converter = "uuid"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CoachingGroup.objects.none()
        return CoachingGroup.objects.filter(coach=self.request.user)

    def perform_create(self, serializer):
        if CoachingGroup.objects.filter(coach=self.request.user, name=serializer.validated_data["name"]).exists():
            raise services.Conflict("Ya tenés un grupo con ese nombre.", code="group_exists")
        serializer.save(coach=self.request.user)


@extend_schema_view(
    list=extend_schema(tags=["coaching"], parameters=[OpenApiParameter("role", str, enum=["coach", "student"]), OpenApiParameter("relationship", str)]),
    retrieve=extend_schema(tags=["coaching"]),
)
class AssignmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = AssignmentSerializer
    lookup_value_converter = "uuid"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RoutineAssignment.objects.none()
        user = self.request.user
        qs = RoutineAssignment.objects.filter(Q(relationship__coach=user) | Q(relationship__student=user)).select_related(
            "relationship__coach", "relationship__student", "source_routine"
        )
        role = self.request.query_params.get("role")
        if role == "coach":
            qs = qs.filter(relationship__coach=user)
        elif role == "student":
            qs = qs.filter(relationship__student=user)
        if rel := self.request.query_params.get("relationship"):
            qs = qs.filter(relationship_id=rel)
        return qs

    @extend_schema(tags=["coaching"], request=AssignSerializer, responses={201: AssignmentSerializer})
    def create(self, request):
        data = _valid(AssignSerializer, request.data)
        assignment = services.assign(request.user, data["relationship"], data["routine"], can_edit_plan=data["can_edit_plan"])
        return Response(self.get_serializer(assignment).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=["coaching"], request=AssignmentUpdateSerializer, responses={200: AssignmentSerializer})
    def partial_update(self, request, pk=None):
        data = _valid(AssignmentUpdateSerializer, request.data)
        return Response(self.get_serializer(services.set_assignment_edit_permission(request.user, pk, data["can_edit_plan"])).data)

    @extend_schema(tags=["coaching"], request=None, responses={200: AssignmentSerializer})
    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        """Coach or student. The student keeps the routine as their own, unlocked."""
        return Response(self.get_serializer(services.end_assignment(request.user, pk)).data)
