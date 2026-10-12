"""Workout sessions, measurements, preferences and progress API."""
import uuid

import pytest

from apps.training.models import Exercise, WorkoutSession
from apps.training.tests.factories import plan

pytestmark = pytest.mark.django_db

ROUTINES = "/api/v1/routines/"
WORKOUTS = "/api/v1/workouts/"
MEASUREMENTS = "/api/v1/measurements/"


def make_api_routine(client):
    payload = {"name": "Hipertrofia", "goal": "hypertrophy", "planning_mode": "repeat", "start_date": "2026-10-05", "plan": plan()}
    return client.post(ROUTINES, payload, format="json").json()


def session_payload(routine=None, **overrides):
    payload = {
        "id": str(uuid.uuid4()),
        "routine": routine["id"] if routine else None,
        "training_day_ref": "d00",
        "original_scheduled_date": "2026-10-05",
        "scheduled_date": "2026-10-05",
        "timezone": "America/Argentina/Buenos_Aires",
        "started_at": "2026-10-05T21:00:00Z",
        "finished_at": "2026-10-05T22:00:00Z",
        "status": "completed",
        "unit": "kg",
        "exercises": [{
            "exercise_id": "chest-bench-press", "order": 0,
            "sets": [
                {"id": str(uuid.uuid4()), "set_number": 1, "weight": 70, "reps": 10, "completed": True, "rir": 2, "rest_seconds": 120},
                {"id": str(uuid.uuid4()), "set_number": 2, "weight": 70, "reps": 8, "completed": True},
                {"id": str(uuid.uuid4()), "set_number": 3, "weight": None, "reps": None, "completed": False},
            ],
        }],
    }
    payload.update(overrides)
    return payload


# ── Sessions ────────────────────────────────────────────────────────────────

def test_session_freezes_the_plan_and_exercise_it_was_trained_with(auth_client):
    client, _, _ = auth_client()
    routine = make_api_routine(client)
    response = client.post(WORKOUTS, session_payload(routine), format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert (body["routine_version_number"], body["duration_seconds"]) == (1, 3600)
    exercise = body["exercises"][0]
    assert exercise["exercise_name"] == "Press banca" and exercise["equipment"] == "barbell"
    assert exercise["planned"] == {"sets": 4, "reps_min": 8, "reps_max": 10, "weight": 70.0}
    assert [s["weight"] for s in exercise["sets"]] == [70.0, 70.0, None]  # not recorded stays null, never 0

    # The coach/owner edits the routine afterwards: the past session must not change.
    new_plan = plan(exercises=[{"id": "x", "exercise_id": "chest-bench-press", "order": 0, "sets": 5, "reps_min": 3, "reps_max": 5, "target_weight": 90}])
    client.patch(f"{ROUTINES}{routine['id']}/", {"plan": new_plan, "base_version": 1}, format="json")
    again = client.get(f"{WORKOUTS}{body['id']}/").json()
    assert again["routine_version_number"] == 1
    assert again["exercises"][0]["planned"]["weight"] == 70.0


def test_retrying_a_session_upload_never_duplicates_it(auth_client):
    client, me, _ = auth_client()
    payload = session_payload()
    assert client.post(WORKOUTS, payload, format="json").status_code == 201
    assert client.post(WORKOUTS, payload, format="json").status_code == 200
    assert WorkoutSession.objects.filter(owner=me).count() == 1


@pytest.mark.parametrize("override", [
    {"status": "completed", "finished_at": None},
    {"status": "active"},  # active with a finish time
    {"finished_at": "2026-10-05T20:00:00Z"},  # before it started
    {"timezone": "Marte/Base_Alfa"},
    {"started_at": "2099-01-01T10:00:00Z", "finished_at": "2099-01-01T11:00:00Z"},
    {"exercises": [{"exercise_id": "chest-bench-press", "order": 0, "sets": [{"set_number": 1}, {"set_number": 1}]}]},
    {"exercises": [{"exercise_id": "chest-bench-press", "order": 0, "sets": [{"set_number": 1, "rir": 11}]}]},
    {"exercises": [{"exercise_id": "chest-bench-press", "order": 0, "sets": [{"set_number": 1, "weight": -5}]}]},
    {"exercises": [{"exercise_id": "no-existe", "order": 0, "sets": []}]},
])
def test_session_validation(auth_client, override):
    client, _, _ = auth_client()
    assert client.post(WORKOUTS, session_payload(**override), format="json").status_code == 400


def test_history_with_a_deleted_exercise_keeps_its_snapshot(auth_client):
    client, me, _ = auth_client()
    gone = Exercise.objects.create(owner=me, name="Máquina vieja", primary_muscle="chest", equipment="machine", category="compound", modality="machine")
    gone_id = str(gone.id)
    client.delete(f"/api/v1/exercises/{gone_id}/")
    payload = session_payload(exercises=[{"exercise_id": gone_id, "order": 0, "sets": [{"set_number": 1, "weight": 40, "reps": 12, "completed": True}]}])
    body = client.post(WORKOUTS, payload, format="json").json()
    assert body["exercises"][0]["exercise_name"] == "Máquina vieja"
    # An exercise the server never knew still needs a name to be accepted (e.g. migrated local history).
    unknown = session_payload(exercises=[{"exercise_id": str(uuid.uuid4()), "exercise_name": "Ejercicio borrado", "order": 0, "sets": []}])
    assert client.post(WORKOUTS, unknown, format="json").status_code == 201


def test_active_session_progress_and_finishing(auth_client):
    client, _, _ = auth_client()
    payload = session_payload(status="active", finished_at=None)
    session = client.post(WORKOUTS, payload, format="json").json()
    url = f"{WORKOUTS}{session['id']}/"
    exercises = payload["exercises"]
    exercises[0]["sets"][2].update(weight=65, reps=8, completed=True)
    progressed = client.patch(url, {"exercises": exercises, "base_version": 1}, format="json")
    assert progressed.status_code == 200 and progressed.json()["exercises"][0]["sets"][2]["reps"] == 8
    assert client.patch(url, {"status": "completed", "base_version": 2}, format="json").status_code == 400  # needs finished_at
    finished = client.patch(url, {"status": "completed", "finished_at": "2026-10-05T22:10:00Z", "base_version": 2}, format="json")
    assert finished.status_code == 200 and finished.json()["duration_seconds"] == 4200
    assert client.patch(url, {"status": "partial", "base_version": 2}, format="json").status_code == 409


def test_sessions_cannot_reference_someone_elses_routine_or_sets(auth_client):
    owner_client, _, _ = auth_client()
    their_routine = make_api_routine(owner_client)
    their_session = owner_client.post(WORKOUTS, session_payload(their_routine), format="json").json()
    intruder, _, _ = auth_client()
    response = intruder.post(WORKOUTS, session_payload(their_routine), format="json")
    assert response.status_code == 400 and "routine" in response.json()["error"]["fields"]
    stolen_set = their_session["exercises"][0]["sets"][0]["id"]
    payload = session_payload(exercises=[{"exercise_id": "chest-bench-press", "order": 0, "sets": [{"id": stolen_set, "set_number": 1}]}])
    assert intruder.post(WORKOUTS, payload, format="json").status_code == 409


def test_nobody_else_can_see_or_touch_my_sessions(auth_client):
    owner_client, _, _ = auth_client()
    session = owner_client.post(WORKOUTS, session_payload(), format="json").json()
    url = f"{WORKOUTS}{session['id']}/"
    intruder, _, _ = auth_client()
    assert intruder.get(url).status_code == 404
    assert intruder.patch(url, {"status": "skipped", "base_version": 1}, format="json").status_code == 404
    assert intruder.delete(url).status_code == 404
    assert intruder.get(WORKOUTS).json()["results"] == []
    assert intruder.post(WORKOUTS, {**session_payload(), "id": session["id"]}, format="json").status_code == 409
    assert owner_client.get(url).json()["status"] == "completed"


def test_session_list_filters_by_local_date(auth_client):
    client, _, _ = auth_client()
    for day in ("2026-09-28", "2026-10-05", "2026-10-07"):
        client.post(WORKOUTS, session_payload(scheduled_date=day, original_scheduled_date=day), format="json")
    dates = [s["scheduled_date"] for s in client.get(WORKOUTS, {"from": "2026-10-01", "to": "2026-10-06"}).json()["results"]]
    assert dates == ["2026-10-05"]
    assert [s["scheduled_date"] for s in client.get(WORKOUTS).json()["results"]] == ["2026-10-07", "2026-10-05", "2026-09-28"]


def test_deleting_a_session_hides_it(auth_client):
    client, _, _ = auth_client()
    session = client.post(WORKOUTS, session_payload(), format="json").json()
    assert client.delete(f"{WORKOUTS}{session['id']}/").status_code == 204
    assert client.get(WORKOUTS).json()["results"] == []


# ── Measurements ────────────────────────────────────────────────────────────

def test_measurement_upsert_by_date_like_the_app(auth_client):
    client, _, _ = auth_client()
    first = client.post(MEASUREMENTS, {"id": str(uuid.uuid4()), "date": "2026-10-05", "value": 80.5, "unit": "kg"}, format="json")
    again = client.post(MEASUREMENTS, {"id": str(uuid.uuid4()), "date": "2026-10-05", "value": 80.1, "unit": "kg"}, format="json")
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"] and again.json()["value"] == 80.1
    assert len(client.get(MEASUREMENTS).json()["results"]) == 1


def test_measurement_retry_is_idempotent(auth_client):
    client, _, _ = auth_client()
    payload = {"id": str(uuid.uuid4()), "date": "2026-10-05", "value": 80.5, "unit": "kg"}
    assert client.post(MEASUREMENTS, payload, format="json").status_code == 201
    retry = client.post(MEASUREMENTS, payload, format="json")
    assert retry.status_code == 200 and retry.json()["version"] == 1
    assert client.post(MEASUREMENTS, {**payload, "date": "2026-10-06"}, format="json").status_code == 409


@pytest.mark.parametrize("bad", [{"value": 0}, {"value": -1}, {"unit": "stone"}, {"date": "2099-01-01"}, {"kind": "mood"}])
def test_measurement_validation(auth_client, bad):
    client, _, _ = auth_client()
    payload = {"date": "2026-10-05", "value": 80, "unit": "kg", **bad}
    assert client.post(MEASUREMENTS, payload, format="json").status_code == 400


def test_measurements_are_private(auth_client):
    owner_client, _, _ = auth_client()
    m = owner_client.post(MEASUREMENTS, {"date": "2026-10-05", "value": 80, "unit": "kg"}, format="json").json()
    intruder, _, _ = auth_client()
    assert intruder.get(f"{MEASUREMENTS}{m['id']}/").status_code == 404
    assert intruder.patch(f"{MEASUREMENTS}{m['id']}/", {"value": 1, "base_version": 1}, format="json").status_code == 404
    assert intruder.delete(f"{MEASUREMENTS}{m['id']}/").status_code == 404
    assert intruder.post(MEASUREMENTS, {"id": m["id"], "date": "2026-10-05", "value": 1, "unit": "kg"}, format="json").status_code == 409
    assert intruder.get(MEASUREMENTS).json()["results"] == []


def test_measurement_edit_and_delete(auth_client):
    client, _, _ = auth_client()
    m = client.post(MEASUREMENTS, {"date": "2026-10-05", "value": 80, "unit": "kg"}, format="json").json()
    url = f"{MEASUREMENTS}{m['id']}/"
    assert client.patch(url, {"value": 79.4, "base_version": 1}, format="json").json()["value"] == 79.4
    assert client.patch(url, {"value": 70, "base_version": 1}, format="json").status_code == 409
    assert client.delete(url).status_code == 204
    # After deleting, the same day can be logged again.
    assert client.post(MEASUREMENTS, {"date": "2026-10-05", "value": 78, "unit": "kg"}, format="json").status_code == 201


# ── Preferences ─────────────────────────────────────────────────────────────

def test_preferences_defaults_and_update(auth_client):
    client, _, _ = auth_client()
    prefs = client.get("/api/v1/users/me/preferences/").json()
    assert (prefs["unit"], prefs["days_per_week"], prefs["increment_barbell"]) == ("kg", 3, 2.5)
    updated = client.patch("/api/v1/users/me/preferences/", {"unit": "lb", "target_body_weight": 180, "theme": "light"}, format="json")
    assert updated.status_code == 200
    assert (updated.json()["unit"], updated.json()["theme"], updated.json()["version"]) == ("lb", "light", 2)
    assert client.patch("/api/v1/users/me/preferences/", {"days_per_week": 9}, format="json").status_code == 400


# ── Progress ────────────────────────────────────────────────────────────────

def test_weekly_progress_counts_real_data_and_reports_missing_weeks_as_null(auth_client, make_user):
    client, _, _ = auth_client()
    client.post(WORKOUTS, session_payload(), format="json")  # Mon 2026-10-05: 70x10 + 70x8 (+1 not done)
    client.post(WORKOUTS, session_payload(status="skipped", finished_at=None), format="json")  # not logged
    deleted = client.post(WORKOUTS, session_payload(), format="json").json()
    client.delete(f"{WORKOUTS}{deleted['id']}/")
    other_client, _, _ = auth_client()
    other_client.post(WORKOUTS, session_payload(), format="json")  # someone else's

    weeks = client.get("/api/v1/progress/weekly/", {"from": "2026-09-28", "to": "2026-10-11"}).json()["weeks"]
    assert weeks[0] == {"week_start": "2026-09-28", "sessions": 0, "completed_sets": None, "volume": None, "duration_seconds": None}
    assert weeks[1] == {"week_start": "2026-10-05", "sessions": 1, "completed_sets": 2, "volume": 1260.0, "duration_seconds": 3600}


def test_progress_converts_units(auth_client):
    client, _, _ = auth_client()
    client.post(WORKOUTS, session_payload(unit="lb"), format="json")  # 70 lb x 18 reps
    kg = client.get("/api/v1/progress/weekly/", {"from": "2026-10-05", "to": "2026-10-05", "unit": "kg"}).json()["weeks"][0]
    assert kg["volume"] == pytest.approx(1260 / 2.20462262185, abs=0.1)


def test_exercise_progress_matches_the_app_formulas(auth_client):
    client, _, _ = auth_client()
    heavy = session_payload(scheduled_date="2026-10-07", original_scheduled_date="2026-10-07",
                            started_at="2026-10-07T21:00:00Z", finished_at="2026-10-07T22:00:00Z")
    heavy["exercises"][0]["sets"] = [{"set_number": 1, "weight": 60, "reps": 20, "completed": True}]  # reps capped at 15
    client.post(WORKOUTS, session_payload(), format="json")
    client.post(WORKOUTS, heavy, format="json")
    points = client.get("/api/v1/progress/exercises/chest-bench-press/").json()["points"]
    assert [p["date"] for p in points] == ["2026-10-05", "2026-10-07"]
    assert points[0]["best_1rm"] == 93.3  # 70 * (1 + 10/30)
    assert points[1]["best_1rm"] == 90.0  # 60 * (1 + 15/30)
    assert points[0]["volume"] == 1260.0 and points[0]["completed_sets"] == 2


def test_progress_range_is_bounded(auth_client):
    client, _, _ = auth_client()
    assert client.get("/api/v1/progress/weekly/", {"from": "2020-01-01", "to": "2026-01-01"}).status_code == 400
    assert client.get("/api/v1/progress/weekly/", {"from": "2026-10-10", "to": "2026-10-01"}).status_code == 400
