from datetime import date, datetime, timedelta, timezone

from training.models import Routine, RoutineVersion, WorkoutSession


def plan(weeks=1, exercises=None):
    exercises = exercises if exercises is not None else [
        {"id": "pe1", "exercise_id": "chest-bench-press", "exercise_name": "Press banca", "order": 0,
         "sets": 4, "reps_min": 8, "reps_max": 10, "target_weight": 70},
    ]
    return {
        "weeks": [
            {"id": f"w{w}", "index": w, "days": [
                {"id": f"d{w}0", "weekday": 0, "name": "Pecho", "is_rest_day": False, "scheduled_time": "18:30", "exercises": exercises},
                {"id": f"d{w}2", "weekday": 2, "name": "Descanso", "is_rest_day": True, "exercises": []},
            ]}
            for w in range(weeks)
        ]
    }


def make_routine(owner, **kwargs):
    fields = dict(name="Hipertrofia", goal="hypertrophy", planning_mode="repeat", start_date=date(2026, 9, 1))
    fields.update(kwargs)
    return Routine.objects.create(owner=owner, **fields)


def make_version(routine, number=1, content=None):
    return RoutineVersion.objects.create(routine=routine, number=number, content=content or plan())


def make_session(owner, **kwargs):
    started = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)
    fields = dict(
        original_scheduled_date=date(2026, 10, 1), scheduled_date=date(2026, 10, 1), started_at=started,
        finished_at=started + timedelta(minutes=55), status="completed", unit="kg", timezone="America/Argentina/Buenos_Aires",
    )
    fields.update(kwargs)
    return WorkoutSession.objects.create(owner=owner, **fields)
