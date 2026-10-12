from rest_framework import serializers

from .models import CoachingGroup, CoachingRelationship, RoutineAssignment


class PersonSerializer(serializers.Serializer):
    """The minimum needed to recognize the other person. Never the email or personal data."""

    id = serializers.UUIDField()
    username = serializers.CharField()
    first_name = serializers.CharField()


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoachingGroup
        fields = ["id", "name"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("El nombre no puede estar vacío.")
        return value


class RelationshipSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField(help_text="Tu rol en esta relación: coach o student.")
    coach = PersonSerializer()
    student = PersonSerializer()
    permissions = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()

    class Meta:
        model = CoachingRelationship
        fields = ["id", "role", "coach", "student", "status", "message", "permissions", "group", "expires_at", "responded_at", "ended_at", "created_at"]

    def get_role(self, rel) -> str:
        return "coach" if rel.coach_id == self.context["request"].user.pk else "student"

    def get_permissions(self, rel) -> dict:
        return {"progress": rel.can_view_progress, "sessions": rel.can_view_sessions, "measurements": rel.can_view_measurements}

    def get_group(self, rel) -> dict | None:
        # Groups are the coach's private organization: students never see them.
        if rel.group is None or self.get_role(rel) != "coach":
            return None
        return {"id": str(rel.group.id), "name": rel.group.name}


class InviteSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    message = serializers.CharField(max_length=280, required=False, allow_blank=True)


class PermissionsSerializer(serializers.Serializer):
    progress = serializers.BooleanField(required=False)
    sessions = serializers.BooleanField(required=False)
    measurements = serializers.BooleanField(required=False)

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        return {f"can_view_{k}": v for k, v in values.items()}


class AcceptSerializer(serializers.Serializer):
    # Nothing is shared unless the student opts in explicitly.
    progress = serializers.BooleanField(default=False)
    sessions = serializers.BooleanField(default=False)
    measurements = serializers.BooleanField(default=False)


class RelationshipUpdateSerializer(serializers.Serializer):
    permissions = PermissionsSerializer(required=False)
    group = serializers.UUIDField(allow_null=True, required=False)


class AssignmentSerializer(serializers.ModelSerializer):
    relationship = serializers.UUIDField(source="relationship_id")
    coach = serializers.SerializerMethodField()
    student = serializers.SerializerMethodField()
    source_routine = serializers.SerializerMethodField()
    student_routine = serializers.UUIDField(source="student_routine_id")

    class Meta:
        model = RoutineAssignment
        fields = ["id", "relationship", "coach", "student", "source_routine", "student_routine", "applied_version_number", "can_edit_plan", "status", "created_at", "ended_at"]

    def get_coach(self, a) -> str:
        return a.relationship.coach.username

    def get_student(self, a) -> str:
        return a.relationship.student.username

    def get_source_routine(self, a) -> dict | None:
        # Only the coach needs to know which of their routines it came from.
        if a.relationship.coach_id != self.context["request"].user.pk:
            return None
        return {"id": str(a.source_routine_id), "name": a.source_routine.name}


class AssignSerializer(serializers.Serializer):
    relationship = serializers.UUIDField()
    routine = serializers.UUIDField()
    can_edit_plan = serializers.BooleanField(default=False)


class AssignmentUpdateSerializer(serializers.Serializer):
    can_edit_plan = serializers.BooleanField()
