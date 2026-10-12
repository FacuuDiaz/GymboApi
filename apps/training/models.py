"""
Training domain: what a person *plans* (Routine → RoutineVersion) and what they
*actually did* (WorkoutSession → ExerciseSession → WorkoutSet), plus body
measurements. Mirrors src/models/ in the mobile app.

History is protected structurally:
- A RoutineVersion is immutable; editing a routine creates a new version.
- A session points at the exact version it was trained with (RESTRICT, so a
  version with history cannot be deleted on its own) and freezes a snapshot of
  every exercise it used, so renaming or deleting an exercise or routine never
  changes how a past workout reads.
"""
import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from apps.common.models import SyncedModel

from .choices import (
    EquipmentType,
    ExerciseCategory,
    ExerciseModality,
    ExerciseVisibility,
    MeasurementKind,
    MeasurementUnit,
    MuscleGroup,
    PlanningMode,
    RoutineGoal,
    SessionStatus,
    VersionOrigin,
    Visibility,
    WeightUnit,
)
from .plan_schema import validate_plan_content


class Exercise(models.Model):
    """
    Built-in (is_builtin, no owner, stable `slug` shared with the app's
    library) or created by a user. A user's exercise is private unless
    published. Deleting is a tombstone; when an author's account is removed
    the owner becomes NULL so other people's references keep working.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="exercises"
    )
    is_builtin = models.BooleanField(default=False)
    slug = models.SlugField(max_length=80, unique=True, null=True, blank=True)
    name = models.CharField(max_length=80)
    primary_muscle = models.CharField(max_length=16, choices=MuscleGroup.choices)
    secondary_muscles = ArrayField(models.CharField(max_length=16, choices=MuscleGroup.choices), default=list, blank=True)
    equipment = models.CharField(max_length=16, choices=EquipmentType.choices)
    category = models.CharField(max_length=16, choices=ExerciseCategory.choices)
    modality = models.CharField(max_length=16, choices=ExerciseModality.choices)
    description = models.TextField(blank=True, max_length=2000)
    instructions = models.TextField(blank=True, max_length=4000)
    video_url = models.URLField(blank=True, max_length=500)
    visibility = models.CharField(max_length=8, choices=ExerciseVisibility.choices, default=ExerciseVisibility.PRIVATE)
    # Set on a user's copy of someone else's private exercise (received with an
    # assigned or copied routine), so it is copied once per user, not every time.
    copied_from = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="copies")
    version = models.PositiveIntegerField(default=1)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "copied_from"], name="exercise_one_copy_per_owner"),
            # Built-ins have a slug and no owner; user exercises have no slug.
            models.CheckConstraint(
                condition=Q(is_builtin=True, owner__isnull=True, slug__isnull=False) | Q(is_builtin=False, slug__isnull=True),
                name="exercise_builtin_shape",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "updated_at"], name="exercise_owner_updated_idx"),
            models.Index(fields=["visibility"], condition=Q(deleted_at__isnull=True), name="exercise_visible_idx"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        invalid = set(self.secondary_muscles) - set(MuscleGroup.values)
        if invalid:
            raise ValidationError({"secondary_muscles": f"Unknown muscle groups: {sorted(invalid)}"})


class Routine(SyncedModel):
    """The mutable head of a plan. Its exercises/days live in RoutineVersion.content."""

    name = models.CharField(max_length=80)
    goal = models.CharField(max_length=16, choices=RoutineGoal.choices)
    # NULL means the app's 'custom' (open-ended) duration.
    duration_weeks = models.PositiveSmallIntegerField(null=True, blank=True)
    planning_mode = models.CharField(max_length=16, choices=PlanningMode.choices)
    is_active = models.BooleanField(default=False)
    start_date = models.DateField()
    archived_at = models.DateTimeField(null=True, blank=True)
    visibility = models.CharField(max_length=8, choices=Visibility.choices, default=Visibility.PRIVATE)
    copied_from_version = models.ForeignKey(
        "RoutineVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="copies"
    )
    # Frozen at copy time ("Basada en la rutina de @ana"), so attribution survives
    # even if the original is deleted. Never includes private data of the author.
    attribution = models.CharField(max_length=160, blank=True)

    class Meta:
        constraints = [
            # Mirrors the app's invariant: at most one active routine per person.
            models.UniqueConstraint(
                fields=["owner"],
                condition=Q(is_active=True, deleted_at__isnull=True, archived_at__isnull=True),
                name="routine_one_active_per_owner",
            ),
            models.CheckConstraint(
                condition=Q(duration_weeks__isnull=True) | Q(duration_weeks__gte=1, duration_weeks__lte=104),
                name="routine_duration_range",
            ),
        ]
        indexes = [models.Index(fields=["owner", "updated_at"], name="routine_owner_updated_idx")]

    def __str__(self):
        return self.name

    @property
    def latest_version(self):
        return self.versions.order_by("-number").first()


class RoutineVersion(models.Model):
    """
    Immutable snapshot of a routine's plan. Stored as validated JSONB because it
    is always read and written as a whole document and never queried by inner
    field; sets and results (which statistics do query) are relational.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    routine = models.ForeignKey(Routine, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    content = models.JSONField()
    origin = models.CharField(max_length=16, choices=VersionOrigin.choices, default=VersionOrigin.OWNER_EDIT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["routine", "number"], name="routine_version_number_unique"),
            models.CheckConstraint(condition=Q(number__gte=1), name="routine_version_number_positive"),
        ]

    def clean(self):
        validate_plan_content(self.content, self.routine.planning_mode)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("RoutineVersion is immutable; create a new version instead.")
        self.full_clean()
        super().save(*args, **kwargs)


class RoutineDateOverride(models.Model):
    """A one-off move of a scheduled day (the app's Routine.dateOverrides)."""

    routine = models.ForeignKey(Routine, on_delete=models.CASCADE, related_name="date_overrides")
    from_date = models.DateField()
    to_date = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["routine", "from_date"], name="date_override_from_unique")]


class WorkoutSession(SyncedModel):
    routine = models.ForeignKey(Routine, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions")
    # NULL only for sessions migrated from a device before versioning existed;
    # their exercise snapshots still make them fully readable.
    routine_version = models.ForeignKey(
        RoutineVersion, on_delete=models.RESTRICT, null=True, blank=True, related_name="sessions"
    )
    training_day_ref = models.CharField(max_length=64, blank=True)
    # Local calendar dates as the user experienced them; never recomputed from UTC.
    original_scheduled_date = models.DateField()
    scheduled_date = models.DateField()
    timezone = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=SessionStatus.choices)
    # Unit of every weight in this session. Blank = recorded before units were
    # stored per session; interpret with the owner's preferred unit.
    unit = models.CharField(max_length=2, choices=WeightUnit.choices, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(finished_at__isnull=True) | Q(finished_at__gte=F("started_at")),
                name="session_finished_after_started",
            ),
            models.CheckConstraint(
                condition=~Q(status=SessionStatus.ACTIVE) | Q(finished_at__isnull=True),
                name="session_active_not_finished",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "scheduled_date"], name="session_owner_date_idx"),
            models.Index(fields=["owner", "updated_at"], name="session_owner_updated_idx"),
        ]


class ExerciseSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(WorkoutSession, on_delete=models.CASCADE, related_name="exercises")
    exercise = models.ForeignKey(Exercise, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # The id the app used (a built-in slug or a custom-exercise UUID), kept verbatim.
    exercise_ref = models.CharField(max_length=80)
    order = models.PositiveSmallIntegerField()
    # Snapshot at training time.
    exercise_name = models.CharField(max_length=80, blank=True)
    primary_muscle = models.CharField(max_length=16, choices=MuscleGroup.choices, blank=True)
    equipment = models.CharField(max_length=16, choices=EquipmentType.choices, blank=True)
    planned_sets = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_reps_min = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_reps_max = models.PositiveSmallIntegerField(null=True, blank=True)
    planned_weight = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "order"], name="exercise_session_order_unique")]


class WorkoutSet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exercise_session = models.ForeignKey(ExerciseSession, on_delete=models.CASCADE, related_name="sets")
    set_number = models.PositiveSmallIntegerField()
    # NULL = not recorded, which is different from 0 (e.g. a bodyweight set).
    weight = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    reps = models.PositiveSmallIntegerField(null=True, blank=True)
    rir = models.PositiveSmallIntegerField(null=True, blank=True)
    rest_seconds = models.PositiveIntegerField(null=True, blank=True)
    completed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exercise_session", "set_number"], name="workout_set_number_unique"),
            models.CheckConstraint(condition=Q(set_number__gte=1), name="workout_set_number_positive"),
            models.CheckConstraint(
                condition=Q(weight__isnull=True) | Q(weight__gte=0, weight__lte=2000), name="workout_set_weight_range"
            ),
            models.CheckConstraint(condition=Q(reps__isnull=True) | Q(reps__lte=1000), name="workout_set_reps_range"),
            models.CheckConstraint(condition=Q(rir__isnull=True) | Q(rir__lte=10), name="workout_set_rir_range"),
            models.CheckConstraint(
                condition=Q(rest_seconds__isnull=True) | Q(rest_seconds__lte=3600), name="workout_set_rest_range"
            ),
        ]


class BodyMeasurement(SyncedModel):
    """
    One value per (owner, date, kind) — the same rule the app enforces locally
    for body weight — so a retried upload can never create a duplicate.
    """

    date = models.DateField()
    kind = models.CharField(max_length=16, choices=MeasurementKind.choices, default=MeasurementKind.BODY_WEIGHT)
    value = models.DecimalField(max_digits=6, decimal_places=2)
    unit = models.CharField(max_length=4, choices=MeasurementUnit.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "date", "kind"],
                condition=Q(deleted_at__isnull=True),
                name="measurement_one_per_day_kind",
            ),
            models.CheckConstraint(condition=Q(value__gt=0), name="measurement_value_positive"),
        ]
        indexes = [models.Index(fields=["owner", "updated_at"], name="measurement_owner_updated_idx")]
