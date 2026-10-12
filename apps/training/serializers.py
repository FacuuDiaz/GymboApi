from datetime import timedelta
from decimal import Decimal
from zoneinfo import available_timezones

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import TrainingPreferences

from .choices import (
    EquipmentType,
    ExerciseCategory,
    ExerciseModality,
    MeasurementKind,
    MeasurementUnit,
    MuscleGroup,
    PlanningMode,
    RoutineGoal,
    SessionStatus,
    WeightUnit,
)
from .models import BodyMeasurement, Exercise, Routine, WorkoutSession

MAX_EXERCISES_PER_SESSION = 50
MAX_SETS_PER_EXERCISE = 50
_TIMEZONES = available_timezones()


# ── Exercises ────────────────────────────────────────────────────────────────

class ExerciseSerializer(serializers.ModelSerializer):
    is_own = serializers.SerializerMethodField()

    class Meta:
        model = Exercise
        fields = [
            "id", "slug", "is_builtin", "is_own", "name", "primary_muscle", "secondary_muscles", "equipment",
            "category", "modality", "description", "instructions", "video_url", "visibility", "version",
            "created_at", "updated_at", "deleted_at",
        ]
        read_only_fields = fields

    def get_is_own(self, exercise) -> bool:
        return exercise.owner_id == self.context["request"].user.pk


class ExerciseWriteSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    name = serializers.CharField(max_length=80)
    primary_muscle = serializers.ChoiceField(choices=MuscleGroup.choices)
    secondary_muscles = serializers.ListField(child=serializers.ChoiceField(choices=MuscleGroup.choices), max_length=12, required=False)
    equipment = serializers.ChoiceField(choices=EquipmentType.choices)
    category = serializers.ChoiceField(choices=ExerciseCategory.choices)
    modality = serializers.ChoiceField(choices=ExerciseModality.choices)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    instructions = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    video_url = serializers.URLField(max_length=500, required=False, allow_blank=True)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("El nombre no puede estar vacío.")
        return value

    def validate_secondary_muscles(self, value):
        return list(dict.fromkeys(value))  # drop duplicates, keep order


class ExerciseUpdateSerializer(ExerciseWriteSerializer):
    base_version = serializers.IntegerField(min_value=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("id")
        for name, field in self.fields.items():
            if name != "base_version":
                field.required = False


# ── Routines ─────────────────────────────────────────────────────────────────

class DateOverrideSerializer(serializers.Serializer):
    from_date = serializers.DateField()
    to_date = serializers.DateField()


class RoutineSerializer(serializers.ModelSerializer):
    plan = serializers.SerializerMethodField()
    plan_version = serializers.SerializerMethodField()
    date_overrides = serializers.SerializerMethodField()
    assignment = serializers.SerializerMethodField()

    class Meta:
        model = Routine
        fields = [
            "id", "name", "goal", "duration_weeks", "planning_mode", "is_active", "start_date", "archived_at",
            "visibility", "attribution", "assignment", "date_overrides", "plan", "plan_version", "version",
            "created_at", "updated_at", "deleted_at",
        ]

    def get_assignment(self, routine) -> dict | None:
        """Set when a coach assigned this routine: the app locks plan editing unless can_edit_plan."""
        from apps.coaching.services import active_assignment_of

        assignment = active_assignment_of(routine)
        if assignment is None:
            return None
        return {
            "id": str(assignment.id),
            "coach_username": assignment.relationship.coach.username,
            "can_edit_plan": assignment.can_edit_plan,
        }
        read_only_fields = fields

    def _latest(self, routine):
        if not hasattr(routine, "_latest_version_cache"):
            routine._latest_version_cache = routine.latest_version
        return routine._latest_version_cache

    def get_plan(self, routine) -> dict | None:
        latest = self._latest(routine)
        return latest.content if latest else None

    def get_plan_version(self, routine) -> int | None:
        latest = self._latest(routine)
        return latest.number if latest else None

    def get_date_overrides(self, routine) -> list[dict]:
        return [
            {"from_date": o.from_date.isoformat(), "to_date": o.to_date.isoformat()}
            for o in routine.date_overrides.order_by("from_date")
        ]


class RoutineWriteSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    name = serializers.CharField(max_length=80)
    goal = serializers.ChoiceField(choices=RoutineGoal.choices)
    duration_weeks = serializers.IntegerField(min_value=1, max_value=104, allow_null=True, required=False)
    planning_mode = serializers.ChoiceField(choices=PlanningMode.choices)
    is_active = serializers.BooleanField(required=False, default=False)
    start_date = serializers.DateField()
    archived_at = serializers.DateTimeField(allow_null=True, required=False)
    date_overrides = DateOverrideSerializer(many=True, required=False)
    plan = serializers.JSONField()

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("El nombre no puede estar vacío.")
        return value

    def validate_date_overrides(self, value):
        if len(value) > 366:
            raise serializers.ValidationError("Demasiadas fechas movidas.")
        if len({o["from_date"] for o in value}) != len(value):
            raise serializers.ValidationError("Cada fecha original puede moverse una sola vez.")
        return value


class RoutineUpdateSerializer(RoutineWriteSerializer):
    base_version = serializers.IntegerField(min_value=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("id")
        for name, field in self.fields.items():
            if name != "base_version":
                field.required = False
        self.fields["is_active"].default = serializers.empty


class RoutineVersionSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    origin = serializers.CharField()
    created_at = serializers.DateTimeField()
    content = serializers.JSONField()


# ── Workout sessions ─────────────────────────────────────────────────────────

class WorkoutSetSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    set_number = serializers.IntegerField(min_value=1, max_value=MAX_SETS_PER_EXERCISE)
    # null = not recorded, which is different from 0.
    weight = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0, max_value=2000, allow_null=True, required=False)
    reps = serializers.IntegerField(min_value=0, max_value=1000, allow_null=True, required=False)
    rir = serializers.IntegerField(min_value=0, max_value=10, allow_null=True, required=False)
    rest_seconds = serializers.IntegerField(min_value=0, max_value=3600, allow_null=True, required=False)
    completed = serializers.BooleanField(default=False)


class SessionExerciseWriteSerializer(serializers.Serializer):
    # Built-in slug or custom-exercise UUID, exactly as the app stores it.
    exercise_id = serializers.CharField(max_length=80)
    # Fallback snapshot when the exercise no longer exists (e.g. migrating old local history).
    exercise_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    order = serializers.IntegerField(min_value=0, max_value=MAX_EXERCISES_PER_SESSION)
    sets = WorkoutSetSerializer(many=True, max_length=MAX_SETS_PER_EXERCISE)

    def validate_sets(self, value):
        numbers = [s["set_number"] for s in value]
        if len(numbers) != len(set(numbers)):
            raise serializers.ValidationError("Números de serie repetidos.")
        return value


class SessionWriteSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    routine = serializers.UUIDField(allow_null=True, required=False)
    routine_version_number = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    training_day_ref = serializers.CharField(max_length=64, required=False, allow_blank=True)
    original_scheduled_date = serializers.DateField()
    scheduled_date = serializers.DateField()
    timezone = serializers.CharField(max_length=64, required=False, allow_blank=True)
    started_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField(allow_null=True, required=False)
    status = serializers.ChoiceField(choices=SessionStatus.choices)
    unit = serializers.ChoiceField(choices=WeightUnit.choices, required=False, allow_blank=True)
    exercises = SessionExerciseWriteSerializer(many=True, max_length=MAX_EXERCISES_PER_SESSION, required=False)

    def validate_timezone(self, value):
        if value and value not in _TIMEZONES:
            raise serializers.ValidationError("Zona horaria desconocida (usá un nombre IANA, ej. America/Argentina/Buenos_Aires).")
        return value

    def validate_exercises(self, value):
        orders = [e["order"] for e in value]
        if len(orders) != len(set(orders)):
            raise serializers.ValidationError("Orden de ejercicios repetido.")
        return value

    def validate(self, attrs):
        started = attrs.get("started_at", getattr(self.instance, "started_at", None))
        finished = attrs.get("finished_at", getattr(self.instance, "finished_at", None))
        status_ = attrs.get("status", getattr(self.instance, "status", None))
        if started and started > timezone.now() + timedelta(days=1):
            raise serializers.ValidationError({"started_at": ["No puede estar en el futuro."]})
        if status_ == SessionStatus.ACTIVE and finished is not None:
            raise serializers.ValidationError({"finished_at": ["Una sesión en curso no tiene hora de fin."]})
        if status_ in (SessionStatus.COMPLETED, SessionStatus.PARTIAL) and finished is None:
            raise serializers.ValidationError({"finished_at": ["Una sesión finalizada necesita hora de fin."]})
        if started and finished and finished < started:
            raise serializers.ValidationError({"finished_at": ["No puede ser anterior al inicio."]})
        return attrs


class SessionUpdateSerializer(SessionWriteSerializer):
    base_version = serializers.IntegerField(min_value=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("id", "routine", "routine_version_number"):
            self.fields.pop(name)
        for name, field in self.fields.items():
            if name != "base_version":
                field.required = False


class WorkoutSessionSerializer(serializers.ModelSerializer):
    routine_version_number = serializers.SerializerMethodField()
    duration_seconds = serializers.SerializerMethodField()
    exercises = serializers.SerializerMethodField()

    class Meta:
        model = WorkoutSession
        fields = [
            "id", "routine", "routine_version_number", "training_day_ref", "original_scheduled_date",
            "scheduled_date", "timezone", "started_at", "finished_at", "status", "unit", "duration_seconds",
            "exercises", "version", "created_at", "updated_at", "deleted_at",
        ]
        read_only_fields = fields

    def get_routine_version_number(self, session) -> int | None:
        return session.routine_version.number if session.routine_version_id else None

    def get_duration_seconds(self, session) -> int | None:
        # null while unfinished: no invented duration.
        if not session.finished_at:
            return None
        return int((session.finished_at - session.started_at).total_seconds())

    def get_exercises(self, session) -> list[dict]:
        return [
            {
                "exercise_id": es.exercise_ref,
                "exercise_name": es.exercise_name,
                "primary_muscle": es.primary_muscle,
                "equipment": es.equipment,
                "order": es.order,
                "planned": {
                    "sets": es.planned_sets,
                    "reps_min": es.planned_reps_min,
                    "reps_max": es.planned_reps_max,
                    "weight": float(es.planned_weight) if es.planned_weight is not None else None,
                },
                "sets": [
                    {
                        "id": str(s.id),
                        "set_number": s.set_number,
                        "weight": float(s.weight) if s.weight is not None else None,
                        "reps": s.reps,
                        "rir": s.rir,
                        "rest_seconds": s.rest_seconds,
                        "completed": s.completed,
                    }
                    for s in sorted(es.sets.all(), key=lambda s: s.set_number)
                ],
            }
            for es in sorted(session.exercises.all(), key=lambda e: e.order)
        ]


# ── Measurements ─────────────────────────────────────────────────────────────

class MeasurementSerializer(serializers.ModelSerializer):
    value = serializers.FloatField()

    class Meta:
        model = BodyMeasurement
        fields = ["id", "date", "kind", "value", "unit", "version", "created_at", "updated_at", "deleted_at"]
        read_only_fields = fields


class MeasurementWriteSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    date = serializers.DateField()
    kind = serializers.ChoiceField(choices=MeasurementKind.choices, default=MeasurementKind.BODY_WEIGHT)
    value = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=Decimal("0.01"), max_value=1000)
    unit = serializers.ChoiceField(choices=MeasurementUnit.choices)

    def validate_date(self, value):
        if value > timezone.localdate() + timedelta(days=1):
            raise serializers.ValidationError("No puede ser una fecha futura.")
        return value


class MeasurementUpdateSerializer(serializers.Serializer):
    value = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=Decimal("0.01"), max_value=1000, required=False)
    unit = serializers.ChoiceField(choices=MeasurementUnit.choices, required=False)
    base_version = serializers.IntegerField(min_value=1)


# ── Preferences ──────────────────────────────────────────────────────────────

class PreferencesSerializer(serializers.ModelSerializer):
    target_body_weight = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=Decimal("0.01"), max_value=1000, allow_null=True, required=False, coerce_to_string=False)
    increment_barbell = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False, coerce_to_string=False)
    increment_dumbbell = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False, coerce_to_string=False)
    increment_machine = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False, coerce_to_string=False)
    days_per_week = serializers.IntegerField(min_value=1, max_value=7, required=False)

    class Meta:
        model = TrainingPreferences
        fields = [
            "level", "goal", "unit", "target_body_weight", "days_per_week", "theme", "increment_barbell",
            "increment_dumbbell", "increment_machine", "onboarding_completed_at", "version", "updated_at",
        ]
        read_only_fields = ["version", "updated_at"]
