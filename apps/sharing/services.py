"""
Sharing and community rules. Ownership and roles always come from the
database, never from the request.
"""
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone
from rest_framework import exceptions

from apps.coaching.services import active_assignment_of, propagate_routine_update
from apps.training.models import Exercise, Routine, RoutineVersion
from apps.training.services import Conflict, check_base_version, normalize_plan, plan_for_owner

from .models import ContentReport, RoutineShare, ShareLink, ShareRole, hash_token

ROLE_RANK = {ShareRole.VIEWER: 1, ShareRole.COPIER: 2, ShareRole.EDITOR: 3}
EDITOR_FIELDS = {"name", "goal", "duration_weeks", "planning_mode", "plan"}
AUTO_HIDE_REPORTS = 3


def _own_routine(owner, routine_id) -> Routine:
    routine = Routine.objects.filter(owner=owner, id=routine_id, deleted_at__isnull=True).first()
    if routine is None:
        raise exceptions.NotFound()
    return routine


def _ensure_redistributable(routine: Routine) -> None:
    # A routine a coach assigned is the coach's plan: the student can train with it,
    # copy it for themselves, but not hand it out or publish it.
    if active_assignment_of(routine) is not None:
        raise exceptions.PermissionDenied(
            "Esta rutina te la asignó tu entrenador: no podés compartirla ni publicarla.", code="assigned_routine"
        )


# ── Sharing with specific people ─────────────────────────────────────────────

@transaction.atomic
def share_with(owner, routine_id, username: str, role: str) -> RoutineShare:
    routine = _own_routine(owner, routine_id)
    _ensure_redistributable(routine)
    grantee = get_user_model().objects.filter(username__iexact=username.strip(), is_active=True).first()
    if grantee is None:
        raise exceptions.ValidationError({"username": ["No existe una cuenta con ese nombre de usuario."]})
    if grantee.pk == owner.pk:
        raise exceptions.ValidationError({"username": ["Es tu propia cuenta."]})
    try:
        with transaction.atomic():
            return RoutineShare.objects.create(routine=routine, grantee=grantee, role=role)
    except IntegrityError:
        raise Conflict("Ya compartiste esta rutina con esa persona.", code="share_exists")


def shares_of_routine(owner, routine_id):
    return _own_routine(owner, routine_id).shares.select_related("grantee").order_by("-created_at")


def _share_for_owner(owner, share_id) -> RoutineShare:
    share = RoutineShare.objects.select_related("routine").filter(id=share_id, routine__owner=owner).first()
    if share is None:
        raise exceptions.NotFound()
    return share


@transaction.atomic
def change_share_role(owner, share_id, role: str) -> RoutineShare:
    share = _share_for_owner(owner, share_id)
    if share.status not in (RoutineShare.Status.PENDING, RoutineShare.Status.ACCEPTED):
        raise Conflict("Este acceso ya no está vigente.", code="share_closed")
    share.role = role
    share.save()
    return share


@transaction.atomic
def revoke_share(owner, share_id) -> RoutineShare:
    share = _share_for_owner(owner, share_id)
    if share.status in (RoutineShare.Status.PENDING, RoutineShare.Status.ACCEPTED):
        share.status = RoutineShare.Status.REVOKED
        share.revoked_at = timezone.now()
        share.save()
    return share


def _received(user, share_id) -> RoutineShare:
    share = RoutineShare.objects.select_related("routine__owner").filter(id=share_id, grantee=user).first()
    if share is None:
        raise exceptions.NotFound()
    return share


@transaction.atomic
def respond_share(user, share_id, accept: bool) -> RoutineShare:
    share = _received(user, share_id)
    if share.status != RoutineShare.Status.PENDING:
        raise Conflict("Esta invitación ya no está pendiente.", code="not_pending")
    share.status = RoutineShare.Status.ACCEPTED if accept else RoutineShare.Status.DECLINED
    share.responded_at = timezone.now()
    share.save()
    return share


def shared_routine(user, routine_id, minimum_role: str) -> tuple[Routine, RoutineShare]:
    """A routine someone shared with `user`, if the accepted share grants at least `minimum_role`."""
    share = (
        RoutineShare.objects.select_related("routine__owner")
        .filter(grantee=user, routine_id=routine_id, status=RoutineShare.Status.ACCEPTED, routine__deleted_at__isnull=True)
        .first()
    )
    if share is None:
        raise exceptions.NotFound()
    if ROLE_RANK[share.role] < ROLE_RANK[minimum_role]:
        raise exceptions.PermissionDenied("Tu acceso a esta rutina no permite esto.", code="insufficient_role")
    return share.routine, share


@transaction.atomic
def edit_shared(user, routine_id, data: dict, represent) -> Routine:
    """An editor changes the owner's plan. Only the plan and its header; never sharing, activation or deletion."""
    routine, _ = shared_routine(user, routine_id, ShareRole.EDITOR)
    routine = Routine.objects.select_for_update().get(pk=routine.pk)
    extra = set(data) - EDITOR_FIELDS - {"base_version"}
    if extra:
        raise exceptions.PermissionDenied(f"Como editor no podés cambiar: {', '.join(sorted(extra))}.", code="field_not_editable")
    check_base_version(routine, data.pop("base_version", None), represent)
    plan = data.pop("plan", None)
    if plan is not None:
        # Exercises must make sense for the OWNER (an editor's private exercise would not).
        plan = normalize_plan(plan, data.get("planning_mode", routine.planning_mode), routine.owner)
        latest = routine.latest_version
        if latest is None or latest.content != plan:
            number = (routine.versions.aggregate(n=Max("number"))["n"] or 0) + 1
            RoutineVersion.objects.create(routine=routine, number=number, content=plan, created_by=user, origin="shared_edit")
    for field, value in data.items():
        setattr(routine, field, value)
    routine.version += 1
    routine.save()
    propagate_routine_update(routine)
    return routine


# ── Links ────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_link(owner, routine_id, role: str, expires_in_days: int | None) -> tuple[ShareLink, str]:
    """Returns the link and its token. The token is never stored nor shown again."""
    routine = _own_routine(owner, routine_id)
    _ensure_redistributable(routine)
    token = secrets.token_urlsafe(32)
    link = ShareLink.objects.create(
        routine=routine, token_hash=hash_token(token), token_hint=token[:6], role=role,
        expires_at=timezone.now() + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    return link, token


def links_of_routine(owner, routine_id):
    return _own_routine(owner, routine_id).links.order_by("-created_at")


@transaction.atomic
def revoke_link(owner, link_id) -> ShareLink:
    link = ShareLink.objects.filter(id=link_id, routine__owner=owner).first()
    if link is None:
        raise exceptions.NotFound()
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.save()
    return link


def resolve_link(token: str, *, count_use: bool = True) -> ShareLink:
    """Valid, unexpired, unrevoked link to a routine that still exists. Every failure looks the same."""
    link = ShareLink.objects.select_related("routine__owner").filter(token_hash=hash_token(token or "")).first()
    now = timezone.now()
    if (
        link is None
        or link.revoked_at is not None
        or (link.expires_at is not None and link.expires_at <= now)
        or link.routine.deleted_at is not None
    ):
        raise exceptions.NotFound("El enlace no existe, venció o fue revocado.", code="link_invalid")
    if count_use:
        ShareLink.objects.filter(pk=link.pk).update(uses=link.uses + 1, last_used_at=now)
    return link


# ── Copies ───────────────────────────────────────────────────────────────────

@transaction.atomic
def copy_routine(user, source: Routine) -> Routine:
    """
    An independent routine owned by `user`: same plan, attribution to the
    author, no sessions or measurements, and any non-built-in exercise copied
    into the user's account. Editing the copy never touches the original.
    """
    latest = source.latest_version
    if latest is None:
        raise exceptions.ValidationError({"routine": ["La rutina no tiene plan."]})
    author = source.owner.username if source.owner_id != user.pk else None
    attribution = f"Basada en «{source.name}» de @{author}"[:160] if author else source.attribution
    copy = Routine.objects.create(
        owner=user, name=source.name, goal=source.goal, duration_weeks=source.duration_weeks,
        planning_mode=source.planning_mode, start_date=timezone.localdate(), is_active=False,
        copied_from_version=latest, attribution=attribution,
    )
    RoutineVersion.objects.create(routine=copy, number=1, content=plan_for_owner(latest.content, user), created_by=user, origin="copy")
    return copy


# ── Community ────────────────────────────────────────────────────────────────

def community_routines():
    return (
        Routine.objects.filter(visibility="public", moderation_status="visible", deleted_at__isnull=True, published_at__isnull=False, owner__is_active=True)
        .select_related("owner")
        .annotate(copies_count=Count("versions__copies", filter=Q(versions__copies__deleted_at__isnull=True), distinct=True))
    )


def community_exercises():
    return Exercise.objects.filter(
        visibility="public", moderation_status="visible", deleted_at__isnull=True, is_builtin=False, published_at__isnull=False
    ).select_related("owner")


@transaction.atomic
def set_routine_published(owner, routine_id, published: bool) -> Routine:
    routine = _own_routine(owner, routine_id)
    if published:
        _ensure_redistributable(routine)
        if routine.moderation_status == "hidden":
            raise exceptions.PermissionDenied("Esta rutina fue ocultada por moderación.", code="moderated")
        routine.visibility, routine.published_at = "public", routine.published_at or timezone.now()
    else:
        routine.visibility, routine.published_at = "private", None
    routine.version += 1
    routine.save()
    return routine


@transaction.atomic
def set_exercise_published(owner, exercise_id, published: bool) -> Exercise:
    exercise = Exercise.objects.filter(owner=owner, id=exercise_id, deleted_at__isnull=True).first()
    if exercise is None:
        raise exceptions.NotFound()
    if exercise.copied_from_id is not None and published:
        raise exceptions.PermissionDenied("Es una copia de un ejercicio de otra persona: no podés publicarlo como propio.", code="not_author")
    if published and exercise.moderation_status == "hidden":
        raise exceptions.PermissionDenied("Este ejercicio fue ocultado por moderación.", code="moderated")
    exercise.visibility, exercise.published_at = ("public", exercise.published_at or timezone.now()) if published else ("private", None)
    exercise.version += 1
    exercise.save()
    return exercise


@transaction.atomic
def copy_exercise(user, exercise: Exercise) -> Exercise:
    from apps.training.services import exercise_ref_for

    return Exercise.objects.get(id=exercise_ref_for(user, str(exercise.id)))


@transaction.atomic
def report(user, kind: str, object_id, reason: str, details: str) -> ContentReport:
    """Only community content can be reported. Enough distinct reports hide it until staff review."""
    source = community_routines() if kind == ContentReport.Kind.ROUTINE else community_exercises()
    target = source.filter(id=object_id).first()
    if target is None:
        raise exceptions.NotFound()
    if target.owner_id == user.pk:
        raise exceptions.ValidationError({"object_id": ["No podés denunciar tu propio contenido."]})
    try:
        with transaction.atomic():
            created = ContentReport.objects.create(reporter=user, kind=kind, object_id=object_id, reason=reason, details=details.strip())
    except IntegrityError:
        raise Conflict("Ya denunciaste este contenido.", code="already_reported")
    open_reports = ContentReport.objects.filter(kind=kind, object_id=object_id, status=ContentReport.Status.OPEN).values("reporter").distinct().count()
    if open_reports >= AUTO_HIDE_REPORTS:
        type(target).objects.filter(pk=target.pk).update(moderation_status="hidden", updated_at=timezone.now())
    return created
