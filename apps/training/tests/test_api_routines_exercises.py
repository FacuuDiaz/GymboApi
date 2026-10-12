"""Exercises and routines API: CRUD, versions, idempotency, concurrency and authorization."""
import uuid

import pytest

from apps.training.models import Exercise, Routine
from apps.training.tests.factories import plan

pytestmark = pytest.mark.django_db

EXERCISES = "/api/v1/exercises/"
ROUTINES = "/api/v1/routines/"


def routine_payload(**overrides):
    payload = {"name": "Hipertrofia", "goal": "hypertrophy", "planning_mode": "repeat", "start_date": "2026-10-05", "plan": plan()}
    payload.update(overrides)
    return payload


def custom_exercise(owner, **kwargs):
    fields = dict(name="Remo Pendlay", primary_muscle="back", equipment="barbell", category="compound", modality="free_weight")
    fields.update(kwargs)
    return Exercise.objects.create(owner=owner, **fields)


# ── Exercises ───────────────────────────────────────────────────────────────

def test_exercise_list_has_builtins_and_my_own_but_not_others_private(auth_client, make_user):
    client, me, _ = auth_client()
    mine = custom_exercise(me, name="Mi press")
    custom_exercise(make_user(), name="Privado ajeno")
    body = client.get(EXERCISES).json()
    names = {e["name"] for e in body["results"]}
    assert len(body["results"]) == 63
    assert "Mi press" in names and "Privado ajeno" not in names
    own = next(e for e in body["results"] if e["id"] == str(mine.id))
    assert own["is_own"] is True and own["is_builtin"] is False
    assert [e["name"] for e in client.get(EXERCISES, {"mine": "1"}).json()["results"]] == ["Mi press"]
    assert all("press" in e["name"].lower() for e in client.get(EXERCISES, {"q": "press"}).json()["results"])


def test_create_exercise_is_idempotent_on_client_id(auth_client):
    client, me, _ = auth_client()
    exercise_id = str(uuid.uuid4())
    payload = {"id": exercise_id, "name": "Hip thrust máquina", "primary_muscle": "glutes", "equipment": "machine",
               "category": "compound", "modality": "machine", "secondary_muscles": ["hamstrings", "hamstrings"]}
    first = client.post(EXERCISES, payload, format="json")
    retry = client.post(EXERCISES, payload, format="json")
    assert (first.status_code, retry.status_code) == (201, 200)
    assert first.json()["id"] == retry.json()["id"] == exercise_id
    assert first.json()["secondary_muscles"] == ["hamstrings"]
    assert Exercise.objects.filter(owner=me).count() == 1


def test_cannot_create_with_an_id_owned_by_someone_else(auth_client, make_user):
    theirs = custom_exercise(make_user())
    client, _, _ = auth_client()
    payload = {"id": str(theirs.id), "name": "X", "primary_muscle": "back", "equipment": "barbell", "category": "compound", "modality": "free_weight"}
    response = client.post(EXERCISES, payload, format="json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "id_conflict"
    theirs.refresh_from_db()
    assert theirs.name == "Remo Pendlay"


def test_update_my_exercise_with_optimistic_concurrency(auth_client):
    client, me, _ = auth_client()
    exercise = custom_exercise(me)
    ok = client.patch(f"{EXERCISES}{exercise.id}/", {"name": "Remo con barra", "base_version": 1}, format="json")
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = client.patch(f"{EXERCISES}{exercise.id}/", {"name": "Otro", "base_version": 1}, format="json")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    assert stale.json()["error"]["current"]["name"] == "Remo con barra"
    assert client.patch(f"{EXERCISES}{exercise.id}/", {"name": "Sin versión"}, format="json").status_code == 400


def test_builtins_are_read_only_and_others_exercises_invisible(auth_client, make_user):
    client, _, _ = auth_client()
    bench = Exercise.objects.get(slug="chest-bench-press")
    theirs = custom_exercise(make_user())
    assert client.patch(f"{EXERCISES}{bench.id}/", {"name": "Hack", "base_version": 1}, format="json").status_code == 403
    assert client.delete(f"{EXERCISES}{bench.id}/").status_code == 403
    for method in ("get", "delete"):
        assert getattr(client, method)(f"{EXERCISES}{theirs.id}/").status_code == 404
    assert client.patch(f"{EXERCISES}{theirs.id}/", {"name": "Hack", "base_version": 1}, format="json").status_code == 404


def test_deleting_an_exercise_is_a_tombstone_visible_to_sync(auth_client):
    client, me, _ = auth_client()
    exercise = custom_exercise(me)
    before = client.get(EXERCISES, {"mine": "1"}).json()["results"][0]["updated_at"]
    assert client.delete(f"{EXERCISES}{exercise.id}/").status_code == 204
    assert client.get(EXERCISES, {"mine": "1"}).json()["results"] == []
    changes = client.get(EXERCISES, {"mine": "1", "updated_since": before}).json()["results"]
    assert changes[0]["id"] == str(exercise.id) and changes[0]["deleted_at"] is not None


# ── Routines ────────────────────────────────────────────────────────────────

def test_create_routine_snapshots_exercise_names(auth_client):
    client, _, _ = auth_client()
    response = client.post(ROUTINES, routine_payload(), format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert (body["plan_version"], body["version"], body["visibility"]) == (1, 1, "private")
    assert body["plan"]["weeks"][0]["days"][0]["exercises"][0]["exercise_name"] == "Press banca"


def test_routine_plan_may_use_my_custom_exercises_but_not_others_private_ones(auth_client, make_user):
    client, me, _ = auth_client()
    mine = custom_exercise(me, name="Mi remo")
    theirs = custom_exercise(make_user())

    def payload_with(exercise_id):
        ex = {"id": "pe1", "exercise_id": exercise_id, "order": 0, "sets": 3, "reps_min": 8, "reps_max": 10}
        return routine_payload(plan=plan(exercises=[ex]))

    assert client.post(ROUTINES, payload_with(str(mine.id)), format="json").status_code == 201
    rejected = client.post(ROUTINES, payload_with(str(theirs.id)), format="json")
    assert rejected.status_code == 400 and "plan" in rejected.json()["error"]["fields"]
    assert client.post(ROUTINES, payload_with("no-existe"), format="json").status_code == 400


@pytest.mark.parametrize("bad", [
    {"plan": {"weeks": []}},
    {"plan": plan(weeks=2)},  # repeat routine with two weeks
    {"name": "   "},
    {"duration_weeks": 0},
    {"goal": "cardio"},
    {"date_overrides": [{"from_date": "2026-10-06", "to_date": "2026-10-07"}, {"from_date": "2026-10-06", "to_date": "2026-10-08"}]},
])
def test_routine_validation(auth_client, bad):
    client, _, _ = auth_client()
    assert client.post(ROUTINES, routine_payload(**bad), format="json").status_code == 400


def test_create_routine_is_idempotent_and_ids_are_not_stealable(auth_client):
    client, me, _ = auth_client()
    payload = routine_payload(id=str(uuid.uuid4()))
    assert client.post(ROUTINES, payload, format="json").status_code == 201
    assert client.post(ROUTINES, payload, format="json").status_code == 200
    assert Routine.objects.filter(owner=me).count() == 1
    other_client, _, _ = auth_client()
    assert other_client.post(ROUTINES, payload, format="json").status_code == 409


def test_editing_the_plan_creates_a_new_version_and_keeps_the_old_one(auth_client):
    client, _, _ = auth_client()
    routine = client.post(ROUTINES, routine_payload(), format="json").json()
    url = f"{ROUTINES}{routine['id']}/"

    renamed = client.patch(url, {"name": "Fuerza", "base_version": 1}, format="json").json()
    assert (renamed["name"], renamed["plan_version"], renamed["version"]) == ("Fuerza", 1, 2)

    new_plan = plan(exercises=[{"id": "pe9", "exercise_id": "quads-back-squat", "order": 0, "sets": 5, "reps_min": 5, "reps_max": 5, "target_weight": 100}])
    updated = client.patch(url, {"plan": new_plan, "base_version": 2}, format="json").json()
    assert updated["plan_version"] == 2

    same_plan_again = client.patch(url, {"plan": new_plan, "base_version": 3}, format="json").json()
    assert same_plan_again["plan_version"] == 2  # identical plan: no new version

    versions = client.get(f"{url}versions/").json()
    assert [v["number"] for v in versions] == [1, 2]
    assert versions[0]["content"]["weeks"][0]["days"][0]["exercises"][0]["exercise_id"] == "chest-bench-press"


def test_routine_update_rejects_stale_versions_with_the_current_state(auth_client):
    client, _, _ = auth_client()
    routine = client.post(ROUTINES, routine_payload(), format="json").json()
    url = f"{ROUTINES}{routine['id']}/"
    client.patch(url, {"name": "Desde el celular", "base_version": 1}, format="json")
    conflict = client.patch(url, {"name": "Desde la tablet", "base_version": 1}, format="json")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["current"]["name"] == "Desde el celular"
    assert client.delete(f"{url}?base_version=1").status_code == 409


def test_activating_a_routine_deactivates_the_others(auth_client):
    client, me, _ = auth_client()
    first = client.post(ROUTINES, routine_payload(is_active=True), format="json").json()
    second = client.post(ROUTINES, routine_payload(name="Otra"), format="json").json()
    client.patch(f"{ROUTINES}{second['id']}/", {"is_active": True, "base_version": 1}, format="json")
    assert list(Routine.objects.filter(owner=me, is_active=True).values_list("id", flat=True)) == [uuid.UUID(second["id"])]
    third = client.post(ROUTINES, routine_payload(name="Tercera", is_active=True), format="json").json()
    assert Routine.objects.get(id=third["id"]).is_active and not Routine.objects.get(id=first["id"]).is_active


def test_delete_routine_is_a_tombstone(auth_client):
    client, _, _ = auth_client()
    routine = client.post(ROUTINES, routine_payload(), format="json").json()
    assert client.delete(f"{ROUTINES}{routine['id']}/").status_code == 204
    assert client.get(f"{ROUTINES}{routine['id']}/").status_code == 404
    assert client.get(ROUTINES).json()["results"] == []
    assert Routine.objects.get(id=routine["id"]).deleted_at is not None


def test_nobody_else_can_read_change_or_delete_my_routine(auth_client):
    owner_client, _, _ = auth_client()
    routine = owner_client.post(ROUTINES, routine_payload(), format="json").json()
    url = f"{ROUTINES}{routine['id']}/"
    intruder, _, _ = auth_client()
    assert intruder.get(url).status_code == 404
    assert intruder.get(f"{url}versions/").status_code == 404
    assert intruder.patch(url, {"name": "Hackeada", "base_version": 1}, format="json").status_code == 404
    assert intruder.delete(url).status_code == 404
    assert intruder.get(ROUTINES).json()["results"] == []
    assert owner_client.get(url).json()["name"] == "Hipertrofia"


@pytest.mark.parametrize("path", [EXERCISES, ROUTINES, "/api/v1/workouts/", "/api/v1/measurements/", "/api/v1/users/me/preferences/", "/api/v1/progress/weekly/"])
def test_training_endpoints_require_authentication(api, path):
    assert api.get(path).status_code == 401
