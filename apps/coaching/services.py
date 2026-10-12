"""
Coaching rules. Every function receives the authenticated user and checks that
they play the right role in the relationship — roles and permissions sent by
the client are never trusted.
"""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import exceptions

from apps.training.models import Exercise, Routine, RoutineVersion
from apps.training.services import Conflict, resolve_exercise

from .models import CoachingRelationship, RoutineAssignment

Status = CoachingRelationship.Status
PLAN_FIELDS = ("plan", "name", "goal", "duration_weeks", "planning_mode", "start_date")


# ── Relationships ────────────────────────────────────────────────────────────

def expire_stale_invitations() -> None:
    CoachingRelationship.objects.filter(status=Status.PENDING, expires_at__lte=timezone.now()).update(
        status=Status.EXPIRED, updated_at=timezone.now()
    )


def relationships_of(user):
    expire_stale_invitations()
    return CoachingRelationship.objects.filter(Q(coach=user) | Q(student=user)).select_related("coach", "student", "group")


def _get(user, relationship_id, *, as_role: str | None = None) -> CoachingRelationship:
    rel = relationships_of(user).filter(id=relationship_id).first()
    if rel is None:
        raise exceptions.NotFound()
    if as_role == "coach" and rel.coach_id != user.pk:
        raise exceptions.PermissionDenied("Solo el entrenador puede hacer esto.")
    if as_role == "student" and rel.student_id != user.pk:
        raise exceptions.PermissionDenied("Solo el alumno puede hacer esto.")
    return rel


@transaction.atomic
def invite(coach, username: str, message: str = "") -> CoachingRelationship:
    """Invites a registered user by username. Nothing is shared until they accept."""
    expire_stale_invitations()
    student = get_user_model().objects.filter(username__iexact=username.strip(), is_active=True).first()
    if student is None:
        raise exceptions.ValidationError({"username": ["No existe una cuenta con ese nombre de usuario."]})
    if student.pk == coach.pk:
        raise exceptions.ValidationError({"username": ["No podés invitarte a vos mismo."]})
    try:
        with transaction.atomic():
            return CoachingRelationship.objects.create(coach=coach, student=student, message=message.strip())
    except IntegrityError:
        raise Conflict("Ya hay una invitación pendiente o una relación activa con esa persona.", code="relationship_exists")


@transaction.atomic
def accept(student, relationship_id, *, can_view_progress: bool, can_view_sessions: bool, can_view_measurements: bool):
    rel = _get(student, relationship_id, as_role="student")
    if rel.status != Status.PENDING:
        raise Conflict("Esta invitación ya no está pendiente.", code="not_pending")
    rel.status = Status.ACTIVE
    rel.responded_at = timezone.now()
    rel.can_view_progress, rel.can_view_sessions, rel.can_view_measurements = can_view_progress, can_view_sessions, can_view_measurements
    rel.save()
    return rel


@transaction.atomic
def reject(student, relationship_id):
    rel = _get(student, relationship_id, as_role="student")
    if rel.status != Status.PENDING:
        raise Conflict("Esta invitación ya no está pendiente.", code="not_pending")
    rel.status = Status.REJECTED
    rel.responded_at = timezone.now()
    rel.save()
    return rel


@transaction.atomic
def end_relationship(user, relationship_id):
    """
    Either side can end it (a coach can also cancel a pending invitation).
    Permissions stop immediately; assignments end and the student keeps their
    routines — now fully theirs — and their whole history.
    """
    rel = _get(user, relationship_id)
    if rel.status not in (Status.PENDING, Status.ACTIVE):
        raise Conflict("Esta relación ya terminó.", code="not_open")
    rel.status = Status.REVOKED
    rel.ended_at = timezone.now()
    rel.ended_by = user
    rel.can_view_progress = rel.can_view_sessions = rel.can_view_measurements = False
    rel.save()
    for assignment in rel.assignments.filter(status=RoutineAssignment.Status.ACTIVE):
        _end_assignment(assignment)
    return rel


@transaction.atomic
def update_permissions(student, relationship_id, changes: dict):
    rel = _get(student, relationship_id, as_role="student")
    if rel.status != Status.ACTIVE:
        raise Conflict("La relación no está activa.", code="not_active")
    for field in ("can_view_progress", "can_view_sessions", "can_view_measurements"):
        if field in changes:
            setattr(rel, field, changes[field])
    rel.save()
    return rel


@transaction.atomic
def set_group(coach, relationship_id, group):
    rel = _get(coach, relationship_id, as_role="coach")
    if group is not None and group.coach_id != coach.pk:
        raise exceptions.ValidationError({"group": ["Grupo inexistente."]})
    rel.group = group
    rel.save()
    return rel


def student_data_access(coach, relationship_id, permission: str) -> CoachingRelationship:
    """The coach may read a student's data only through an active relationship that grants it."""
    rel = _get(coach, relationship_id, as_role="coach")
    if rel.status != Status.ACTIVE or not getattr(rel, permission):
        raise exceptions.PermissionDenied("Tu alumno no autorizó ver esta información.", code="not_authorized")
    return rel


# ── Assignments ─────────────────────────────────────────────────────────────

def _exercise_for_student(ref: str, coach, student) -> str:
    """
    Built-in and published exercises are shared as is. A coach's private
    exercise is copied once into the student's account, so the student's app
    can show it and log sessions with it.
    """
    exercise = resolve_exercise(ref, coach)
    if exercise is None or exercise.is_builtin or exercise.visibility == "public" or exercise.owner_id != coach.pk:
        return ref
    copy = Exercise.objects.filter(owner=student, copied_from=exercise).first()
    if copy is None:
        copy = Exercise.objects.create(
            owner=student, copied_from=exercise, name=exercise.name, primary_muscle=exercise.primary_muscle,
            secondary_muscles=exercise.secondary_muscles, equipment=exercise.equipment, category=exercise.category,
            modality=exercise.modality, description=exercise.description, instructions=exercise.instructions,
            video_url=exercise.video_url,
        )
    elif copy.deleted_at is not None:
        copy.deleted_at = None
        copy.version += 1
        copy.save()
    return str(copy.id)


def _plan_for_student(content: dict, coach, student) -> dict:
    plan = {"weeks": []}
    for week in content["weeks"]:
        days = []
        for day in week["days"]:
            exercises = [{**p, "exercise_id": _exercise_for_student(p["exercise_id"], coach, student)} for p in day["exercises"]]
            days.append({**day, "exercises": exercises})
        plan["weeks"].append({**week, "days": days})
    return plan


def _apply_source(assignment: RoutineAssignment, source: Routine, *, coach) -> None:
    """Brings the student's routine up to the coach's latest version (header + plan)."""
    student_routine = assignment.student_routine
    student = student_routine.owner
    latest = source.latest_version
    plan = _plan_for_student(latest.content, coach, student)
    current = student_routine.latest_version
    if current is None or current.content != plan:
        number = (student_routine.versions.aggregate(n=Max("number"))["n"] or 0) + 1
        RoutineVersion.objects.create(routine=student_routine, number=number, content=plan, created_by=coach, origin="coach_update")
    for field in ("name", "goal", "duration_weeks", "planning_mode", "start_date"):
        setattr(student_routine, field, getattr(source, field))
    student_routine.version += 1
    student_routine.save()
    assignment.applied_version_number = latest.number
    assignment.save()


@transaction.atomic
def assign(coach, relationship_id, routine_id, *, can_edit_plan: bool = False) -> RoutineAssignment:
    rel = _get(coach, relationship_id, as_role="coach")
    if rel.status != Status.ACTIVE:
        raise Conflict("Solo podés asignar rutinas a alumnos que aceptaron tu invitación.", code="not_active")
    source = Routine.objects.filter(owner=coach, id=routine_id, deleted_at__isnull=True).first()
    if source is None:
        raise exceptions.ValidationError({"routine": ["Rutina inexistente. Si la creaste en el celular, esperá a que se sincronice."]})
    if rel.assignments.filter(source_routine=source, status=RoutineAssignment.Status.ACTIVE).exists():
        raise Conflict("Esa rutina ya está asignada a este alumno.", code="already_assigned")
    student = rel.student
    student_has_active = Routine.objects.filter(owner=student, is_active=True, deleted_at__isnull=True, archived_at__isnull=True).exists()
    student_routine = Routine.objects.create(
        owner=student, name=source.name, goal=source.goal, duration_weeks=source.duration_weeks,
        planning_mode=source.planning_mode, start_date=source.start_date, is_active=not student_has_active,
        attribution=f"Asignada por @{coach.username}",
    )
    assignment = RoutineAssignment(relationship=rel, source_routine=source, student_routine=student_routine,
                                   applied_version_number=0, can_edit_plan=can_edit_plan)
    assignment.save()
    _apply_source(assignment, source, coach=coach)
    return assignment


def propagate_routine_update(source: Routine) -> int:
    """Called whenever a routine changes: every active assignment of it follows automatically (D5)."""
    count = 0
    assignments = source.assignments_as_source.filter(
        status=RoutineAssignment.Status.ACTIVE, relationship__status=Status.ACTIVE
    ).select_related("student_routine__owner", "relationship__coach")
    for assignment in assignments:
        if assignment.student_routine.deleted_at is None:
            _apply_source(assignment, source, coach=assignment.relationship.coach)
            count += 1
    return count


def _end_assignment(assignment: RoutineAssignment) -> None:
    assignment.status = RoutineAssignment.Status.ENDED
    assignment.ended_at = timezone.now()
    assignment.save()
    # The routine is the student's own from now on: unlocked, nothing deleted.
    routine = assignment.student_routine
    routine.version += 1
    routine.save()


@transaction.atomic
def end_assignment(user, assignment_id) -> RoutineAssignment:
    assignment = (
        RoutineAssignment.objects.select_related("relationship")
        .filter(Q(relationship__coach=user) | Q(relationship__student=user), id=assignment_id)
        .first()
    )
    if assignment is None:
        raise exceptions.NotFound()
    if assignment.status == RoutineAssignment.Status.ACTIVE:
        _end_assignment(assignment)
    return assignment


@transaction.atomic
def set_assignment_edit_permission(coach, assignment_id, can_edit_plan: bool) -> RoutineAssignment:
    assignment = RoutineAssignment.objects.filter(relationship__coach=coach, id=assignment_id).first()
    if assignment is None:
        raise exceptions.NotFound()
    assignment.can_edit_plan = can_edit_plan
    assignment.save()
    routine = assignment.student_routine
    routine.version += 1  # so the student's devices pull the new lock state
    routine.save()
    return assignment


def active_assignment_of(routine: Routine) -> RoutineAssignment | None:
    assignment = getattr(routine, "assignment", None) if routine.pk else None
    if assignment is None or assignment.status != RoutineAssignment.Status.ACTIVE:
        return None
    return assignment


def check_student_edit(user, routine: Routine, changed_fields) -> None:
    """A student without edit permission can train with the plan and schedule it, but not change it."""
    assignment = active_assignment_of(routine)
    if assignment is None or assignment.can_edit_plan or routine.owner_id != user.pk:
        return
    locked = sorted(set(changed_fields) & set(PLAN_FIELDS))
    if locked:
        raise exceptions.PermissionDenied(
            "Esta rutina la administra tu entrenador. Pedile permiso para editarla.", code="plan_locked"
        )


def check_student_delete(user, routine: Routine) -> None:
    if active_assignment_of(routine) is not None and routine.owner_id == user.pk:
        raise exceptions.PermissionDenied(
            "Esta rutina está asignada por tu entrenador. Podés archivarla o pedirle que finalice la asignación.",
            code="assignment_active",
        )
