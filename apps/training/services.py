"""
Business rules for the training API. Views stay thin: they authenticate,
parse input and call these functions inside a transaction.

Ownership is never taken from the client: every function receives the
authenticated user and only ever touches rows owned by (or visible to) them.
"""
import uuid
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import exceptions, status

from .builtin import builtin_exercise_id
from .models import (
    BodyMeasurement,
    Exercise,
    ExerciseSession,
    Routine,
    RoutineDateOverride,
    RoutineVersion,
    WorkoutSession,
    WorkoutSet,
)
from .plan_schema import validate_plan_content


class Conflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class VersionConflict(Conflict):
    """The client edited an outdated copy. Carries the server's current state so it can merge."""

    default_code = "version_conflict"

    def __init__(self, current: dict):
        super().__init__("Este registro cambió en otro dispositivo. Revisá la versión actual.")
        # Exposed as error.current by the API exception handler.
        self.current = current


def id_conflict():
    # A client-generated UUID already used by another account (or another
    # kind of record). Never reveal whose it is.
    return Conflict("Ese identificador ya está en uso.", code="id_conflict")


def check_base_version(instance, base_version, represent) -> None:
    """Optimistic concurrency: refuse an update based on a stale version."""
    if base_version is None:
        raise exceptions.ValidationError({"base_version": ["Es obligatorio para modificar."]})
    if instance.version != base_version:
        raise VersionConflict(represent(instance))


# ── Exercises ────────────────────────────────────────────────────────────────

def visible_exercises(user):
    """Built-ins, the user's own exercises and published ones."""
    return Exercise.objects.filter(Q(is_builtin=True) | Q(owner=user) | Q(visibility="public"))


def resolve_exercise(ref: str, user, *, allow_deleted: bool = False) -> Exercise | None:
    """
    The app refers to exercises by built-in slug ("chest-bench-press") or by
    UUID (custom exercises). Returns None when the reference is not visible to
    `user` — callers decide whether that is an error.
    """
    qs = visible_exercises(user)
    if not allow_deleted:
        qs = qs.filter(deleted_at__isnull=True)
    try:
        exercise_id = uuid.UUID(str(ref))
    except ValueError:
        return qs.filter(slug=ref).first()
    return qs.filter(id=exercise_id).first()


# ── Routines ─────────────────────────────────────────────────────────────────

def normalize_plan(content: dict, planning_mode: str, user) -> dict:
    """
    Validates the plan and fills every planned exercise's `exercise_name`
    snapshot from the referenced exercise, which must exist and be visible.
    """
    from django.core.exceptions import ValidationError as DjangoValidationError

    try:
        validate_plan_content(content, planning_mode)
    except DjangoValidationError as exc:
        raise exceptions.ValidationError({"plan": exc.messages}) from exc
    unknown = []
    for week in content["weeks"]:
        for day in week["days"]:
            for planned in day["exercises"]:
                exercise = resolve_exercise(planned["exercise_id"], user)
                if exercise is None:
                    unknown.append(planned["exercise_id"])
                else:
                    planned["exercise_name"] = exercise.name
    if unknown:
        raise exceptions.ValidationError({"plan": [f"Ejercicios inexistentes o no accesibles: {sorted(set(unknown))}"]})
    return content


def _replace_date_overrides(routine: Routine, overrides: list[dict]) -> None:
    routine.date_overrides.all().delete()
    RoutineDateOverride.objects.bulk_create(
        RoutineDateOverride(routine=routine, from_date=o["from_date"], to_date=o["to_date"]) for o in overrides
    )


def _deactivate_other_routines(user, keep_id) -> None:
    Routine.objects.filter(owner=user, is_active=True).exclude(id=keep_id).update(
        is_active=False, updated_at=timezone.now()
    )


@transaction.atomic
def create_routine(user, data: dict) -> tuple[Routine, bool]:
    """Idempotent on the client id: retrying returns the existing routine (created=False)."""
    routine_id = data.pop("id", None) or uuid.uuid4()
    existing = Routine.objects.filter(id=routine_id).first()
    if existing is not None:
        if existing.owner_id != user.pk:
            raise id_conflict()
        return existing, False

    plan = normalize_plan(data.pop("plan"), data["planning_mode"], user)
    overrides = data.pop("date_overrides", [])
    if data.get("is_active"):
        _deactivate_other_routines(user, routine_id)
    routine = Routine.objects.create(id=routine_id, owner=user, **data)
    RoutineVersion.objects.create(routine=routine, number=1, content=plan, created_by=user, origin="owner_edit")
    _replace_date_overrides(routine, overrides)
    return routine, True


@transaction.atomic
def update_routine(user, routine_id, data: dict, represent) -> Routine:
    """
    Partial update with optimistic concurrency. A changed plan creates a new
    immutable RoutineVersion: sessions keep pointing at the version they used.
    """
    routine = Routine.objects.select_for_update().filter(owner=user, id=routine_id, deleted_at__isnull=True).first()
    if routine is None:
        raise exceptions.NotFound()
    check_base_version(routine, data.pop("base_version", None), represent)

    plan = data.pop("plan", None)
    if plan is not None:
        mode = data.get("planning_mode", routine.planning_mode)
        plan = normalize_plan(plan, mode, user)
        latest = routine.latest_version
        if latest is None or latest.content != plan:
            next_number = (routine.versions.aggregate(n=Max("number"))["n"] or 0) + 1
            RoutineVersion.objects.create(routine=routine, number=next_number, content=plan, created_by=user, origin="owner_edit")
    elif "planning_mode" in data and data["planning_mode"] != routine.planning_mode:
        # The current plan must still be valid under the new mode.
        normalize_plan(routine.latest_version.content, data["planning_mode"], user)

    if "date_overrides" in data:
        _replace_date_overrides(routine, data.pop("date_overrides"))
    if data.get("is_active"):
        _deactivate_other_routines(user, routine.id)
        data.setdefault("archived_at", None)
    for field, value in data.items():
        setattr(routine, field, value)
    routine.version += 1
    routine.save()
    return routine


@transaction.atomic
def delete_routine(user, routine_id, base_version, represent) -> None:
    """Tombstone: the routine disappears from lists; its versions and the sessions that used them remain."""
    routine = Routine.objects.select_for_update().filter(owner=user, id=routine_id, deleted_at__isnull=True).first()
    if routine is None:
        raise exceptions.NotFound()
    if base_version is not None and routine.version != base_version:
        raise VersionConflict(represent(routine))
    routine.deleted_at = timezone.now()
    routine.is_active = False
    routine.version += 1
    routine.save()


# ── Workout sessions ─────────────────────────────────────────────────────────

@dataclass
class _ResolvedExercise:
    exercise: Exercise | None
    ref: str


def _write_session_exercises(session: WorkoutSession, exercises: list[dict], user) -> None:
    """(Re)writes the nested exercises and sets, freezing an exercise snapshot for each."""
    session.exercises.all().delete()
    plan_by_ref = _planned_targets(session)
    for item in exercises:
        ref = item["exercise_id"]
        # Historic sessions may reference an exercise deleted since: keep the snapshot.
        exercise = resolve_exercise(ref, user, allow_deleted=True)
        if exercise is None and not item.get("exercise_name"):
            raise exceptions.ValidationError({"exercises": [f"Ejercicio inexistente o no accesible: {ref}"]})
        planned = plan_by_ref.get(ref, {})
        es = ExerciseSession.objects.create(
            session=session,
            exercise=exercise,
            exercise_ref=ref,
            order=item["order"],
            exercise_name=exercise.name if exercise else item["exercise_name"],
            primary_muscle=exercise.primary_muscle if exercise else "",
            equipment=exercise.equipment if exercise else "",
            planned_sets=planned.get("sets"),
            planned_reps_min=planned.get("reps_min"),
            planned_reps_max=planned.get("reps_max"),
            planned_weight=planned.get("target_weight"),
        )
        set_ids = [s["id"] for s in item["sets"] if s.get("id")]
        if set_ids and WorkoutSet.objects.filter(id__in=set_ids).exclude(exercise_session__session=session).exists():
            raise id_conflict()
        WorkoutSet.objects.bulk_create(
            WorkoutSet(
                id=s.get("id") or uuid.uuid4(),
                exercise_session=es,
                set_number=s["set_number"],
                weight=s.get("weight"),
                reps=s.get("reps"),
                rir=s.get("rir"),
                rest_seconds=s.get("rest_seconds"),
                completed=s.get("completed", False),
            )
            for s in item["sets"]
        )


def _planned_targets(session: WorkoutSession) -> dict:
    """Planned sets/reps/weight per exercise, from the exact version the session used."""
    version = session.routine_version
    if version is None:
        return {}
    for week in version.content["weeks"]:
        for day in week["days"]:
            if day["id"] == session.training_day_ref:
                return {p["exercise_id"]: p for p in day["exercises"]}
    return {}


def _resolve_routine_refs(user, data: dict) -> None:
    routine = data.get("routine")
    if routine is not None:
        routine = Routine.objects.filter(owner=user, id=routine).first()
        if routine is None:
            raise exceptions.ValidationError({"routine": ["Rutina inexistente."]})
    version_number = data.pop("routine_version_number", None)
    version = None
    if routine is not None:
        version = routine.versions.filter(number=version_number).first() if version_number else routine.latest_version
        if version_number and version is None:
            raise exceptions.ValidationError({"routine_version_number": ["Versión inexistente."]})
    data["routine"] = routine
    data["routine_version"] = version


@transaction.atomic
def create_session(user, data: dict) -> tuple[WorkoutSession, bool]:
    session_id = data.pop("id", None) or uuid.uuid4()
    existing = WorkoutSession.objects.filter(id=session_id).first()
    if existing is not None:
        if existing.owner_id != user.pk:
            raise id_conflict()
        return existing, False
    exercises = data.pop("exercises", [])
    _resolve_routine_refs(user, data)
    session = WorkoutSession.objects.create(id=session_id, owner=user, **data)
    _write_session_exercises(session, exercises, user)
    return session, True


@transaction.atomic
def update_session(user, session_id, data: dict, represent) -> WorkoutSession:
    """
    Saves progress of an active session or corrects a finished one (owner
    only, optimistic concurrency). The routine it was trained with is never
    re-pinned: editing the plan later cannot rewrite this history.
    """
    session = WorkoutSession.objects.select_for_update().filter(owner=user, id=session_id, deleted_at__isnull=True).first()
    if session is None:
        raise exceptions.NotFound()
    check_base_version(session, data.pop("base_version", None), represent)
    exercises = data.pop("exercises", None)
    for field in ("routine", "routine_version_number"):
        data.pop(field, None)
    for field, value in data.items():
        setattr(session, field, value)
    session.version += 1
    session.save()
    if exercises is not None:
        _write_session_exercises(session, exercises, user)
    return session


@transaction.atomic
def delete_session(user, session_id) -> None:
    session = WorkoutSession.objects.select_for_update().filter(owner=user, id=session_id, deleted_at__isnull=True).first()
    if session is None:
        raise exceptions.NotFound()
    session.deleted_at = timezone.now()
    session.version += 1
    session.save()


# ── Measurements ─────────────────────────────────────────────────────────────

@transaction.atomic
def upsert_measurement(user, data: dict) -> tuple[BodyMeasurement, bool]:
    """
    Same rule as the app: one value per (date, kind) — logging again on a date
    overwrites it. Idempotent on both the client id and the natural key, so a
    retried request never creates a duplicate.
    """
    measurement_id = data.pop("id", None)
    kind = data.setdefault("kind", "body_weight")
    if measurement_id:
        by_id = BodyMeasurement.objects.select_for_update().filter(id=measurement_id).first()
        if by_id is not None:
            if by_id.owner_id != user.pk:
                raise id_conflict()
            if by_id.date != data["date"] or by_id.kind != kind:
                # Same id, different day: that is an edit, not a retry. Use PATCH.
                raise id_conflict()
            if by_id.deleted_at is None and (by_id.value, by_id.unit) != (data["value"], data["unit"]):
                by_id.value, by_id.unit = data["value"], data["unit"]
                by_id.version += 1
                by_id.save()
            return by_id, False
    current = (
        BodyMeasurement.objects.select_for_update()
        .filter(owner=user, date=data["date"], kind=kind, deleted_at__isnull=True)
        .first()
    )
    if current is not None:
        if (current.value, current.unit) != (data["value"], data["unit"]):
            current.value, current.unit = data["value"], data["unit"]
            current.version += 1
            current.save()
        return current, False
    return BodyMeasurement.objects.create(id=measurement_id or uuid.uuid4(), owner=user, **data), True


__all__ = [
    "Conflict", "VersionConflict", "builtin_exercise_id", "check_base_version", "create_routine", "create_session",
    "delete_routine", "delete_session", "normalize_plan", "resolve_exercise", "update_routine", "update_session",
    "upsert_measurement", "visible_exercises",
]
