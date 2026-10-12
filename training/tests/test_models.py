import json
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.utils import timezone

from training.builtin import BUILTIN_DATA_PATH, builtin_exercise_id, load_builtin_exercises
from training.models import BodyMeasurement, Exercise, ExerciseSession, RoutineVersion, WorkoutSet
from training.tests.factories import make_routine, make_session, make_version, plan

pytestmark = pytest.mark.django_db


# ── Built-in exercise library ───────────────────────────────────────────────

def test_builtin_library_is_loaded_by_migration_with_deterministic_ids():
    rows = json.loads(BUILTIN_DATA_PATH.read_text())
    assert Exercise.objects.filter(is_builtin=True).count() == len(rows) == 62
    bench = Exercise.objects.get(slug="chest-bench-press")
    assert bench.id == builtin_exercise_id("chest-bench-press")
    assert (bench.name, bench.owner, bench.visibility) == ("Press banca", None, "public")


def test_builtin_loader_is_idempotent():
    before = Exercise.objects.count()
    created, updated = load_builtin_exercises(Exercise)
    assert (created, updated, Exercise.objects.count()) == (0, 62, before)


def test_user_exercise_cannot_look_like_a_builtin(make_user):
    with pytest.raises(IntegrityError), transaction.atomic():
        Exercise.objects.create(owner=make_user(), is_builtin=False, slug="fake", name="X", primary_muscle="chest",
                                equipment="barbell", category="compound", modality="free_weight")


# ── Routines and immutable versions ─────────────────────────────────────────

def test_only_one_active_routine_per_owner(make_user):
    ana, beto = make_user(), make_user()
    make_routine(ana, is_active=True)
    make_routine(beto, is_active=True)  # another person: fine
    make_routine(ana, is_active=True, archived_at=timezone.now())  # archived: fine
    make_routine(ana, is_active=True, deleted_at=timezone.now())  # tombstoned: fine
    with pytest.raises(IntegrityError), transaction.atomic():
        make_routine(ana, is_active=True)


def test_routine_version_is_immutable(make_user):
    version = make_version(make_routine(make_user()))
    version.content = plan(exercises=[])
    with pytest.raises(ValidationError):
        version.save()
    version.refresh_from_db()
    assert version.content["weeks"][0]["days"][0]["exercises"][0]["exercise_id"] == "chest-bench-press"


def test_routine_version_validates_content_on_create(make_user):
    with pytest.raises(ValidationError):
        make_version(make_routine(make_user(), planning_mode="repeat"), content=plan(weeks=2))


def test_routine_version_numbers_are_unique_per_routine(make_user):
    routine = make_routine(make_user())
    make_version(routine, number=1)
    with pytest.raises(ValidationError):
        make_version(routine, number=1)


def test_new_version_does_not_alter_sessions_trained_with_the_old_one(make_user):
    owner = make_user()
    routine = make_routine(owner)
    v1 = make_version(routine, 1)
    session = make_session(owner, routine=routine, routine_version=v1)
    make_version(routine, 2, content=plan(exercises=[]))
    session.refresh_from_db()
    assert session.routine_version == v1
    assert routine.latest_version.number == 2
    assert len(v1.content["weeks"][0]["days"][0]["exercises"]) == 1


def test_version_with_history_cannot_be_deleted_alone(make_user):
    owner = make_user()
    v1 = make_version(make_routine(owner))
    make_session(owner, routine_version=v1)
    with pytest.raises(RestrictedError):
        v1.delete()


def test_deleting_an_account_removes_its_history_without_being_blocked(make_user):
    owner = make_user()
    routine = make_routine(owner)
    make_session(owner, routine=routine, routine_version=make_version(routine))
    owner.delete()
    assert not RoutineVersion.objects.filter(routine=routine).exists()


# ── Sessions, snapshots and sets ────────────────────────────────────────────

def test_session_cannot_finish_before_it_started(make_user):
    owner = make_user()
    with pytest.raises(IntegrityError), transaction.atomic():
        s = make_session(owner)
        make_session(owner, finished_at=s.started_at - timedelta(minutes=1))


def test_active_session_has_no_finish_time(make_user):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_session(make_user(), status="active")


def test_exercise_snapshot_survives_exercise_deletion(make_user):
    author = make_user()
    custom = Exercise.objects.create(owner=author, name="Remo Pendlay", primary_muscle="back", equipment="barbell",
                                     category="compound", modality="free_weight")
    es = ExerciseSession.objects.create(session=make_session(author), exercise=custom, exercise_ref=str(custom.id),
                                        order=0, exercise_name="Remo Pendlay", primary_muscle="back", equipment="barbell")
    custom_ref = str(custom.id)
    custom.delete()
    es.refresh_from_db()
    assert es.exercise is None
    assert (es.exercise_name, es.exercise_ref) == ("Remo Pendlay", custom_ref)


def test_set_numbers_are_unique_and_values_bounded(make_user):
    es = ExerciseSession.objects.create(session=make_session(make_user()), exercise_ref="chest-bench-press", order=0)
    WorkoutSet.objects.create(exercise_session=es, set_number=1, weight=None, reps=None)  # not recorded ≠ 0
    WorkoutSet.objects.create(exercise_session=es, set_number=2, weight=0, reps=12, completed=True)  # bodyweight
    for bad in ({"set_number": 1}, {"set_number": 3, "weight": -1}, {"set_number": 3, "rir": 11},
                {"set_number": 3, "rest_seconds": 3601}, {"set_number": 0}):
        with pytest.raises(IntegrityError), transaction.atomic():
            WorkoutSet.objects.create(exercise_session=es, **bad)


# ── Measurements ────────────────────────────────────────────────────────────

def test_one_measurement_per_day_and_kind_so_retries_never_duplicate(make_user):
    owner = make_user()
    day = timezone.localdate()
    first = BodyMeasurement.objects.create(owner=owner, date=day, value=80.5, unit="kg")
    with pytest.raises(IntegrityError), transaction.atomic():
        BodyMeasurement.objects.create(owner=owner, date=day, value=81, unit="kg")
    BodyMeasurement.objects.create(owner=make_user(), date=day, value=60, unit="kg")  # another person
    first.deleted_at = timezone.now()
    first.save()
    BodyMeasurement.objects.create(owner=owner, date=day, value=81, unit="kg")  # after tombstone


def test_measurement_value_must_be_positive(make_user):
    with pytest.raises(IntegrityError), transaction.atomic():
        BodyMeasurement.objects.create(owner=make_user(), date=timezone.localdate(), value=0, unit="kg")
