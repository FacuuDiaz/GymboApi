from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView

from apps.common.pagination import DefaultCursorPagination
from apps.training.serializers import ExerciseSerializer, RoutineSerializer

from . import services
from .models import RoutineShare, RoutineStar
from .serializers import (
    LinkCreatedSerializer,
    LinkCreateSerializer,
    LinkSerializer,
    PublicExerciseSerializer,
    PublicRoutineSerializer,
    ReportSerializer,
    ShareCreateSerializer,
    ShareRoleSerializer,
    ShareSerializer,
    SharedEditSerializer,
    StarredRoutineSerializer,
    TokenSerializer,
)


def _valid(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _own_routine_response(request, routine, code=status.HTTP_200_OK):
    return Response(RoutineSerializer(routine, context={"request": request}).data, status=code)


# ── Owner side: shares, links, publishing (nested under /routines/{id}/) ────

class RoutineSharingView(viewsets.ViewSet):
    lookup_value_converter = "uuid"

    @extend_schema(tags=["sharing"], responses={200: ShareSerializer(many=True)})
    def list_shares(self, request, pk=None):
        return Response(ShareSerializer(services.shares_of_routine(request.user, pk), many=True).data)

    @extend_schema(tags=["sharing"], request=ShareCreateSerializer, responses={201: ShareSerializer, 409: OpenApiResponse(description="share_exists")})
    def create_share(self, request, pk=None):
        data = _valid(ShareCreateSerializer, request.data)
        return Response(ShareSerializer(services.share_with(request.user, pk, data["username"], data["role"])).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=["sharing"], responses={200: LinkSerializer(many=True)})
    def list_links(self, request, pk=None):
        return Response(LinkSerializer(services.links_of_routine(request.user, pk), many=True).data)

    @extend_schema(tags=["sharing"], request=LinkCreateSerializer, responses={201: LinkCreatedSerializer})
    def create_link(self, request, pk=None):
        data = _valid(LinkCreateSerializer, request.data)
        link, token = services.create_link(request.user, pk, data["role"], data.get("expires_in_days"))
        return Response({**LinkSerializer(link).data, "token": token}, status=status.HTTP_201_CREATED)

    @extend_schema(tags=["sharing"], request=None, responses={200: RoutineSerializer})
    def publish(self, request, pk=None):
        return _own_routine_response(request, services.set_routine_published(request.user, pk, True))

    @extend_schema(tags=["sharing"], request=None, responses={200: RoutineSerializer})
    def unpublish(self, request, pk=None):
        return _own_routine_response(request, services.set_routine_published(request.user, pk, False))


class ExercisePublishingView(viewsets.ViewSet):
    @extend_schema(tags=["sharing"], request=None, responses={200: ExerciseSerializer})
    def publish(self, request, pk=None):
        exercise = services.set_exercise_published(request.user, pk, True)
        return Response(ExerciseSerializer(exercise, context={"request": request}).data)

    @extend_schema(tags=["sharing"], request=None, responses={200: ExerciseSerializer})
    def unpublish(self, request, pk=None):
        exercise = services.set_exercise_published(request.user, pk, False)
        return Response(ExerciseSerializer(exercise, context={"request": request}).data)


# ── Shares: both sides ──────────────────────────────────────────────────────

@extend_schema_view(list=extend_schema(tags=["sharing"], parameters=[OpenApiParameter("direction", str, enum=["received", "sent"])]))
class ShareViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ShareSerializer
    lookup_value_converter = "uuid"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RoutineShare.objects.none()
        user = self.request.user
        qs = RoutineShare.objects.select_related("routine__owner", "grantee").filter(Q(grantee=user) | Q(routine__owner=user))
        direction = self.request.query_params.get("direction")
        if direction == "received":
            qs = qs.filter(grantee=user, routine__deleted_at__isnull=True)
        elif direction == "sent":
            qs = qs.filter(routine__owner=user)
        return qs

    @extend_schema(tags=["sharing"], request=None, responses={200: ShareSerializer})
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return Response(ShareSerializer(services.respond_share(request.user, pk, True)).data)

    @extend_schema(tags=["sharing"], request=None, responses={200: ShareSerializer})
    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        return Response(ShareSerializer(services.respond_share(request.user, pk, False)).data)

    @extend_schema(tags=["sharing"], request=ShareRoleSerializer, responses={200: ShareSerializer})
    def partial_update(self, request, pk=None):
        data = _valid(ShareRoleSerializer, request.data)
        return Response(ShareSerializer(services.change_share_role(request.user, pk, data["role"])).data)

    @extend_schema(tags=["sharing"], responses={204: None})
    def destroy(self, request, pk=None):
        services.revoke_share(request.user, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LinkRevokeView(APIView):
    @extend_schema(tags=["sharing"], responses={204: None})
    def delete(self, request, pk):
        services.revoke_link(request.user, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SharedRoutineView(viewsets.ViewSet):
    """A routine someone shared with me (accepted share)."""

    @extend_schema(tags=["sharing"], responses={200: PublicRoutineSerializer})
    def retrieve(self, request, pk=None):
        routine, share = services.shared_routine(request.user, pk, "viewer")
        return Response({**PublicRoutineSerializer(routine).data, "role": share.role, "version": routine.version})

    @extend_schema(tags=["sharing"], request=SharedEditSerializer, responses={200: PublicRoutineSerializer, 409: OpenApiResponse(description="version_conflict")})
    def partial_update(self, request, pk=None):
        data = dict(_valid(SharedEditSerializer, request.data))
        represent = lambda r: {**PublicRoutineSerializer(r).data, "version": r.version}  # noqa: E731
        routine = services.edit_shared(request.user, pk, data, represent)
        return Response(represent(routine))

    @extend_schema(tags=["sharing"], request=None, responses={201: RoutineSerializer})
    def copy(self, request, pk=None):
        routine, _ = services.shared_routine(request.user, pk, "copier")
        return _own_routine_response(request, services.copy_routine(request.user, routine), status.HTTP_201_CREATED)


# ── Links: anyone with the token ────────────────────────────────────────────

class LinkResolveView(APIView):
    """
    The token travels in the request BODY (POST), never in the URL, so it does
    not end up in server or proxy access logs. Viewing works without an account.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle, UserRateThrottle]

    @extend_schema(tags=["sharing"], request=TokenSerializer, responses={200: PublicRoutineSerializer, 404: OpenApiResponse(description="link_invalid")})
    def post(self, request):
        link = services.resolve_link(_valid(TokenSerializer, request.data)["token"])
        return Response({**PublicRoutineSerializer(link.routine).data, "role": link.role, "expires_at": link.expires_at})


class LinkCopyView(APIView):
    @extend_schema(tags=["sharing"], request=TokenSerializer, responses={201: RoutineSerializer, 403: OpenApiResponse(description="El enlace no permite copiar")})
    def post(self, request):
        link = services.resolve_link(_valid(TokenSerializer, request.data)["token"], count_use=False)
        if link.role != "copier":
            raise exceptions.PermissionDenied("Este enlace permite ver la rutina, pero no copiarla.", code="insufficient_role")
        return _own_routine_response(request, services.copy_routine(request.user, link.routine), status.HTTP_201_CREATED)


# ── Community catalog: browsing is public, copying needs an account ─────────

class CommunityPagination(DefaultCursorPagination):
    ordering = ("-published_at", "-id")


class CommunityRoutinePagination(CommunityPagination):
    """`?ordering=stars` lists the most starred first (like sorting repos by stars)."""

    def get_ordering(self, request, queryset, view):
        if request.query_params.get("ordering") == "stars":
            return ("-stars_count", "-published_at", "-id")
        return super().get_ordering(request, queryset, view)


@extend_schema_view(
    list=extend_schema(tags=["community"], parameters=[
        OpenApiParameter("q", str), OpenApiParameter("goal", str, enum=["hypertrophy", "strength"]), OpenApiParameter("author", str),
        OpenApiParameter("ordering", str, enum=["recent", "stars"]),
    ]),
    retrieve=extend_schema(tags=["community"]),
)
class CommunityRoutineViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = PublicRoutineSerializer
    pagination_class = CommunityRoutinePagination
    lookup_value_converter = "uuid"

    def get_permissions(self):
        return [IsAuthenticated()] if self.action in ("copy", "report", "star") else [AllowAny()]

    @extend_schema(tags=["community"], responses={200: PublicRoutineSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def top(self, request):
        """Ranking de las 100 rutinas con más estrellas (solo las que tienen al menos una). Sin paginar."""
        routines = services.top_starred_routines(request.user)
        return Response(PublicRoutineSerializer(routines, many=True, context={"request": request}).data)

    def get_queryset(self):
        qs = services.with_starred(services.community_routines(), self.request.user)
        params = self.request.query_params
        if q := params.get("q", "").strip():
            qs = qs.filter(name__icontains=q[:80])
        if goal := params.get("goal"):
            qs = qs.filter(goal=goal)
        if author := params.get("author"):
            qs = qs.filter(owner__username__iexact=author)
        return qs

    @extend_schema(tags=["community"], request=None, responses={201: RoutineSerializer})
    @action(detail=True, methods=["post"])
    def copy(self, request, pk=None):
        return _own_routine_response(request, services.copy_routine(request.user, self.get_object()), status.HTTP_201_CREATED)

    @extend_schema(
        tags=["community"], request=None,
        responses={201: OpenApiResponse(description="Estrella agregada: {starred, stars_count}"), 200: OpenApiResponse(description="Ya tenía estrella: {starred, stars_count}"), 204: OpenApiResponse(description="Estrella quitada"), 400: OpenApiResponse(description="own_routine")},
    )
    @action(detail=True, methods=["post", "delete"])
    def star(self, request, pk=None):
        """POST pone una estrella (una sola vez por persona; no a rutinas propias). DELETE la quita."""
        if request.method == "DELETE":
            services.unstar_routine(request.user, pk)
            return Response(status=status.HTTP_204_NO_CONTENT)
        created = services.star_routine(request.user, pk)
        stars_count = RoutineStar.objects.filter(routine_id=pk).count()
        return Response({"starred": True, "stars_count": stars_count}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(tags=["community"], parameters=[OpenApiParameter("q", str), OpenApiParameter("muscle", str), OpenApiParameter("equipment", str)]),
    retrieve=extend_schema(tags=["community"]),
)
class CommunityExerciseViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = PublicExerciseSerializer
    pagination_class = CommunityPagination
    lookup_value_converter = "uuid"

    def get_permissions(self):
        return [IsAuthenticated()] if self.action == "copy" else [AllowAny()]

    def get_queryset(self):
        qs = services.community_exercises()
        params = self.request.query_params
        if q := params.get("q", "").strip():
            qs = qs.filter(name__icontains=q[:80])
        if muscle := params.get("muscle"):
            qs = qs.filter(primary_muscle=muscle)
        if equipment := params.get("equipment"):
            qs = qs.filter(equipment=equipment)
        return qs

    @extend_schema(tags=["community"], request=None, responses={201: ExerciseSerializer})
    @action(detail=True, methods=["post"])
    def copy(self, request, pk=None):
        exercise = services.copy_exercise(request.user, self.get_object())
        return Response(ExerciseSerializer(exercise, context={"request": request}).data, status=status.HTTP_201_CREATED)


class StarPagination(DefaultCursorPagination):
    ordering = ("-starred_at", "-id")


@extend_schema(tags=["community"])
class StarredRoutinesView(ListAPIView):
    """Las rutinas a las que les pusiste estrella y siguen publicadas (como las «stars» de GitHub)."""

    serializer_class = StarredRoutineSerializer
    pagination_class = StarPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return services.with_starred(services.starred_routines(self.request.user), self.request.user)


class ReportView(APIView):
    @extend_schema(tags=["community"], request=ReportSerializer, responses={201: ReportSerializer, 409: OpenApiResponse(description="already_reported")})
    def post(self, request):
        serializer = ReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        created = services.report(request.user, data["kind"], data["object_id"], data["reason"], data.get("details", ""))
        return Response(ReportSerializer(created).data, status=status.HTTP_201_CREATED)
