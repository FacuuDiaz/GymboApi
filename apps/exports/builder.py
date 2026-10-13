"""
Builds a registered user's export document from the database. Only the
requester's own, non-deleted records are read: other people's data (coaches,
students, people who shared with them) never enters the file.
"""
from django.db.models import Prefetch
from django.utils import timezone

from apps.accounts.models import TrainingPreferences
from apps.training.models import BodyMeasurement, Exercise, ExerciseSession, Routine, WorkoutSession

from . import format as fmt

PREFERENCE_FIELDS = (
    "level", "goal", "unit", "target_body_weight", "days_per_week", "theme", "increment_barbell", "increment_dumbbell",
    "increment_machine",
)


def _float(value):
    return float(value) if value is not None else None


def _preferences(user) -> dict:
    prefs = TrainingPreferences.objects.filter(user=user).first() or TrainingPreferences(user=user)
    data = {field: getattr(prefs, field) for field in PREFERENCE_FIELDS}
    for field in ("target_body_weight", "increment_barbell", "increment_dumbbell", "increment_machine"):
        data[field] = _float(data[field])
    data["onboarding_completed_at"] = fmt.timestamp(prefs.onboarding_completed_at)
    return data


def _account(user) -> dict:
    return {
        "username": user.username, "email": user.email, "first_name": user.first_name, "last_name": user.last_name,
        "phone": user.phone, "gender": user.gender, "birth_date": user.birth_date.isoformat() if user.birth_date else None,
        "date_joined": fmt.timestamp(user.date_joined),
        "linked_providers": sorted(user.social_identities.values_list("provider", flat=True)),
        "discoverable": user.discoverable,
        "is_coach": user.is_coach,
    }


def _stars(user) -> list[dict]:
    from apps.sharing.models import RoutineStar

    return [
        {"routine_id": str(s.routine_id), "routine_name": s.routine.name, "author": s.routine.owner.username if s.routine.owner_id else "", "starred_at": fmt.timestamp(s.created_at)}
        for s in RoutineStar.objects.filter(user=user).select_related("routine__owner").order_by("created_at", "routine_id")
    ]


def _exercises(user) -> list[dict]:
    return [
        {
            "id": str(e.id), "name": e.name, "primary_muscle": e.primary_muscle, "secondary_muscles": list(e.secondary_muscles),
            "equipment": e.equipment, "category": e.category, "modality": e.modality, "description": e.description,
            "instructions": e.instructions, "video_url": e.video_url,
        }
        for e in Exercise.objects.filter(owner=user, is_builtin=False, deleted_at__isnull=True).order_by("created_at", "id")
    ]


def _routines(user) -> list[dict]:
    routines = (
        Routine.objects.filter(owner=user, deleted_at__isnull=True)
        .prefetch_related("versions", "date_overrides")
        .order_by("created_at", "id")
    )
    out = []
    for r in routines:
        versions = sorted(r.versions.all(), key=lambda v: v.number)
        out.append({
            "id": str(r.id), "name": r.name, "goal": r.goal, "duration_weeks": r.duration_weeks, "planning_mode": r.planning_mode,
            "is_active": r.is_active, "start_date": r.start_date.isoformat(), "archived_at": fmt.timestamp(r.archived_at),
            "attribution": r.attribution,
            "date_overrides": [
                {"from_date": o.from_date.isoformat(), "to_date": o.to_date.isoformat()}
                for o in sorted(r.date_overrides.all(), key=lambda o: o.from_date)
            ],
            "plan": versions[-1].content if versions else {"weeks": []},
            "versions": [
                {"number": v.number, "origin": v.origin, "created_at": fmt.timestamp(v.created_at), "content": v.content}
                for v in versions
            ],
        })
    return out


def _sessions(user, default_unit: str) -> list[dict]:
    names = dict(Routine.objects.filter(owner=user).values_list("id", "name"))
    sessions = (
        WorkoutSession.objects.filter(owner=user, deleted_at__isnull=True)
        .prefetch_related(Prefetch("exercises", queryset=ExerciseSession.objects.prefetch_related("sets")))
        .order_by("scheduled_date", "started_at", "id")
    )
    out = []
    for s in sessions:
        out.append({
            "id": str(s.id), "routine_id": str(s.routine_id) if s.routine_id else None, "routine_name": names.get(s.routine_id, ""),
            "training_day_ref": s.training_day_ref, "original_scheduled_date": s.original_scheduled_date.isoformat(),
            "scheduled_date": s.scheduled_date.isoformat(), "timezone": s.timezone, "started_at": fmt.timestamp(s.started_at),
            "finished_at": fmt.timestamp(s.finished_at),
            "duration_seconds": int((s.finished_at - s.started_at).total_seconds()) if s.finished_at else None,
            "status": s.status, "unit": s.unit or default_unit,
            "exercises": [
                {
                    "order": e.order, "exercise_id": e.exercise_ref, "exercise_name": e.exercise_name, "primary_muscle": e.primary_muscle,
                    "sets": [
                        {
                            "set_number": x.set_number, "weight": _float(x.weight), "reps": x.reps, "rir": x.rir,
                            "rest_seconds": x.rest_seconds, "completed": x.completed,
                        }
                        for x in sorted(e.sets.all(), key=lambda x: x.set_number)
                    ],
                }
                for e in sorted(s.exercises.all(), key=lambda e: e.order)
            ],
        })
    return out


def build_document(user) -> dict:
    preferences = _preferences(user)
    sessions = _sessions(user, preferences["unit"])
    return {
        "format": fmt.FORMAT,
        "version": fmt.VERSION,
        "generated_at": fmt.timestamp(timezone.now()),
        "source": "account",
        "about": fmt.ABOUT,
        "account": _account(user),
        "preferences": preferences,
        "exercises": _exercises(user),
        "routines": _routines(user),
        "sessions": sessions,
        "measurements": [
            {"date": m.date.isoformat(), "kind": m.kind, "value": float(m.value), "unit": m.unit}
            for m in BodyMeasurement.objects.filter(owner=user, deleted_at__isnull=True).order_by("date", "kind")
        ],
        "weekly_progress": fmt.weekly_progress(sessions, preferences["unit"]),
        "stars": _stars(user),
    }
