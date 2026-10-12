"""
Progress statistics derived from real sessions only — nothing is invented.

Rules mirror the mobile app (src/utils/oneRepMax.ts, src/utils/volume.ts) so a
number means the same thing on both sides:
- a set counts only if completed and it has both weight and reps;
- estimated 1RM = Epley, weight * (1 + reps/30), reps capped at 15, rounded to 0.1;
- only `completed` and `partial` sessions are "logged" sessions.

Missing vs zero: a week without logged sessions reports sessions=0 and every
metric as null (there is no data), never 0 volume.

Units: each session stores the unit its weights were entered in (blank =
recorded before units were stored; the owner's preferred unit is assumed).
Results are converted to the requested unit.

Dates: weeks are Monday-based and use the session's LOCAL scheduled_date, so a
late-night workout lands in the week the user experienced it.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .models import WorkoutSession

LB_PER_KG = Decimal("2.20462262185")
MAX_REPS_FOR_ESTIMATE = 15
LOGGED_STATUSES = ("completed", "partial")


def convert(weight: Decimal, from_unit: str, to_unit: str) -> Decimal:
    if from_unit == to_unit:
        return weight
    return weight * LB_PER_KG if to_unit == "lb" else weight / LB_PER_KG


def estimate_one_rep_max(weight: float, reps: int) -> float:
    if weight <= 0 or reps <= 0:
        return 0.0
    return round(weight * (1 + min(reps, MAX_REPS_FOR_ESTIMATE) / 30), 1)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _logged_sessions(user):
    return (
        WorkoutSession.objects.filter(owner=user, deleted_at__isnull=True, status__in=LOGGED_STATUSES)
        .prefetch_related("exercises__sets")
    )


def _counted_sets(exercise_session, session_unit: str, unit: str):
    """(weight in `unit`, reps) for every set that counts."""
    for s in exercise_session.sets.all():
        if s.completed and s.weight and s.reps:
            yield float(convert(s.weight, session_unit, unit)), s.reps


def weekly_summary(user, start: date, end: date, unit: str, default_unit: str) -> list[dict]:
    first, last = week_start(start), week_start(end)
    buckets: dict[date, dict] = {}
    week = first
    while week <= last:
        buckets[week] = {"sessions": 0, "completed_sets": 0, "volume": 0.0, "duration_seconds": 0, "_durations": 0}
        week += timedelta(days=7)

    for session in _logged_sessions(user).filter(scheduled_date__gte=first, scheduled_date__lte=end):
        bucket = buckets[week_start(session.scheduled_date)]
        bucket["sessions"] += 1
        if session.finished_at:
            bucket["duration_seconds"] += int((session.finished_at - session.started_at).total_seconds())
            bucket["_durations"] += 1
        session_unit = session.unit or default_unit
        for es in session.exercises.all():
            for weight, reps in _counted_sets(es, session_unit, unit):
                bucket["completed_sets"] += 1
                bucket["volume"] += weight * reps

    result = []
    for week, b in sorted(buckets.items()):
        has_data = b["sessions"] > 0
        result.append({
            "week_start": week.isoformat(),
            "sessions": b["sessions"],
            "completed_sets": b["completed_sets"] if has_data else None,
            "volume": round(b["volume"], 1) if has_data else None,
            "duration_seconds": b["duration_seconds"] if b["_durations"] else None,
        })
    return result


def exercise_history(user, exercise_ref: str, unit: str, default_unit: str) -> list[dict]:
    """One point per logged session that trained `exercise_ref` (oldest first). Sessions with no counted set are skipped."""
    points = defaultdict(lambda: {"max_weight": 0.0, "best_1rm": 0.0, "volume": 0.0, "completed_sets": 0})
    order = []
    sessions = _logged_sessions(user).filter(exercises__exercise_ref=exercise_ref).distinct().order_by("scheduled_date", "started_at")
    for session in sessions:
        session_unit = session.unit or default_unit
        for es in session.exercises.all():
            if es.exercise_ref != exercise_ref:
                continue
            for weight, reps in _counted_sets(es, session_unit, unit):
                p = points[session.id]
                if session.id not in order:
                    order.append(session.id)
                    p["date"] = session.scheduled_date.isoformat()
                    p["session_id"] = str(session.id)
                p["max_weight"] = max(p["max_weight"], weight)
                p["best_1rm"] = max(p["best_1rm"], estimate_one_rep_max(weight, reps))
                p["volume"] += weight * reps
                p["completed_sets"] += 1
    return [
        {**points[sid], "max_weight": round(points[sid]["max_weight"], 2), "volume": round(points[sid]["volume"], 1)}
        for sid in order
    ]
