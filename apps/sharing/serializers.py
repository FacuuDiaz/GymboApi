from rest_framework import serializers

from apps.training.models import Exercise, Routine

from apps.training.choices import PlanningMode, RoutineGoal

from .models import ContentReport, LinkRole, RoutineShare, ShareLink, ShareRole


class PublicRoutineSerializer(serializers.ModelSerializer):
    """
    What other people see of a routine (catalog, links, shares): the plan and
    the author's username. Never the author's id, email, profile, weight,
    sessions or progress.
    """

    author = serializers.SerializerMethodField()
    plan = serializers.SerializerMethodField()
    plan_version = serializers.SerializerMethodField()
    copies_count = serializers.SerializerMethodField()

    class Meta:
        model = Routine
        fields = ["id", "name", "goal", "duration_weeks", "planning_mode", "author", "attribution", "plan", "plan_version", "published_at", "copies_count", "updated_at"]

    def get_author(self, routine) -> str:
        return routine.owner.username

    def _latest(self, routine):
        if not hasattr(routine, "_latest_cache"):
            routine._latest_cache = routine.latest_version
        return routine._latest_cache

    def get_plan(self, routine) -> dict | None:
        latest = self._latest(routine)
        return latest.content if latest else None

    def get_plan_version(self, routine) -> int | None:
        latest = self._latest(routine)
        return latest.number if latest else None

    def get_copies_count(self, routine) -> int:
        return getattr(routine, "copies_count", 0)


class PublicExerciseSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()

    class Meta:
        model = Exercise
        fields = ["id", "name", "primary_muscle", "secondary_muscles", "equipment", "category", "modality", "description", "instructions", "video_url", "author", "published_at"]

    def get_author(self, exercise) -> str | None:
        return exercise.owner.username if exercise.owner else None


class ShareSerializer(serializers.ModelSerializer):
    grantee = serializers.CharField(source="grantee.username")
    routine = serializers.UUIDField(source="routine_id")
    routine_name = serializers.CharField(source="routine.name")
    owner = serializers.CharField(source="routine.owner.username")

    class Meta:
        model = RoutineShare
        fields = ["id", "routine", "routine_name", "owner", "grantee", "role", "status", "created_at", "responded_at", "revoked_at"]


class ShareCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    role = serializers.ChoiceField(choices=ShareRole.choices, default=ShareRole.VIEWER)


class ShareRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ShareRole.choices)


class LinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShareLink
        fields = ["id", "token_hint", "role", "expires_at", "revoked_at", "last_used_at", "uses", "created_at"]


class LinkCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=LinkRole.choices, default=LinkRole.VIEWER)
    expires_in_days = serializers.IntegerField(min_value=1, max_value=365, required=False, allow_null=True)


class LinkCreatedSerializer(LinkSerializer):
    token = serializers.CharField(help_text="Se muestra una única vez. Guardalo o compartilo ahora.")

    class Meta(LinkSerializer.Meta):
        fields = [*LinkSerializer.Meta.fields, "token"]


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=128)


class SharedEditSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, required=False)
    goal = serializers.ChoiceField(choices=RoutineGoal.choices, required=False)
    duration_weeks = serializers.IntegerField(min_value=1, max_value=104, allow_null=True, required=False)
    planning_mode = serializers.ChoiceField(choices=PlanningMode.choices, required=False)
    plan = serializers.JSONField(required=False)
    base_version = serializers.IntegerField(min_value=1)


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContentReport
        fields = ["id", "kind", "object_id", "reason", "details", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]
