"""Coaches and students: invitations, privacy, assignments with automatic updates, permissions and revocation."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.coaching.models import CoachingRelationship
from apps.training.models import Exercise, Routine
from apps.training.tests.factories import plan

pytestmark = pytest.mark.django_db

REL = "/api/v1/coaching/relationships/"
ASSIGN = "/api/v1/coaching/assignments/"
GROUPS = "/api/v1/coaching/groups/"
ROUTINES = "/api/v1/routines/"


@pytest.fixture
def people(auth_client, make_user):
    coach_client, coach, _ = auth_client(make_user(username="profe"))
    student_client, student, _ = auth_client(make_user(username="alumna"))
    other_client, other, _ = auth_client(make_user(username="intrusa"))
    return {"coach": (coach_client, coach), "student": (student_client, student), "other": (other_client, other)}


def invite(people):
    coach_client, _ = people["coach"]
    return coach_client.post(f"{REL}invite/", {"username": "Alumna", "message": "¡Entrenemos!"}, format="json")


def active_relationship(people, **perms):
    rel = invite(people).json()
    people["student"][0].post(f"{REL}{rel['id']}/accept/", {"progress": True, "sessions": True, "measurements": False, **perms}, format="json")
    return rel["id"]


def coach_routine(people, exercises=None, **extra):
    payload = {"name": "Fuerza 5x5", "goal": "strength", "planning_mode": "repeat", "start_date": "2026-10-05", "plan": plan(exercises=exercises), **extra}
    return people["coach"][0].post(ROUTINES, payload, format="json").json()


# ── Invitations ─────────────────────────────────────────────────────────────

def test_invitation_flow_shows_each_side_its_role(people):
    response = invite(people)
    assert response.status_code == 201
    rel = response.json()
    assert (rel["role"], rel["status"], rel["student"]["username"]) == ("coach", "pending", "alumna")
    assert rel["permissions"] == {"progress": False, "sessions": False, "measurements": False}
    seen_by_student = people["student"][0].get(REL).json()["results"][0]
    assert (seen_by_student["role"], seen_by_student["coach"]["username"], seen_by_student["message"]) == ("student", "profe", "¡Entrenemos!")
    assert "email" not in seen_by_student["coach"] and "email" not in rel["student"]


@pytest.mark.parametrize("username", ["profe", "nadie-con-ese-nombre"])
def test_cannot_invite_yourself_or_unknown_users(people, username):
    assert people["coach"][0].post(f"{REL}invite/", {"username": username}, format="json").status_code == 400


def test_one_open_relationship_per_pair_but_reinvite_after_rejection(people):
    rel = invite(people).json()
    assert invite(people).status_code == 409
    people["student"][0].post(f"{REL}{rel['id']}/reject/")
    assert invite(people).status_code == 201


def test_only_the_invited_student_can_accept_or_reject(people):
    rel = invite(people).json()
    assert people["coach"][0].post(f"{REL}{rel['id']}/accept/", {}, format="json").status_code == 403
    assert people["other"][0].post(f"{REL}{rel['id']}/accept/", {}, format="json").status_code == 404
    assert people["other"][0].get(f"{REL}{rel['id']}/").status_code == 404
    accepted = people["student"][0].post(f"{REL}{rel['id']}/accept/", {"progress": True}, format="json")
    assert accepted.status_code == 200
    assert accepted.json()["permissions"] == {"progress": True, "sessions": False, "measurements": False}
    assert people["student"][0].post(f"{REL}{rel['id']}/accept/", {}, format="json").status_code == 409


def test_invitations_expire(people):
    rel = invite(people).json()
    CoachingRelationship.objects.filter(id=rel["id"]).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert people["student"][0].get(f"{REL}{rel['id']}/").json()["status"] == "expired"
    assert people["student"][0].post(f"{REL}{rel['id']}/accept/", {}, format="json").status_code == 409
    assert invite(people).status_code == 201  # an expired invitation does not block a new one


def test_coach_can_cancel_a_pending_invitation(people):
    rel = invite(people).json()
    assert people["coach"][0].post(f"{REL}{rel['id']}/end/").json()["status"] == "revoked"


# ── Privacy ─────────────────────────────────────────────────────────────────

def test_nothing_is_visible_before_the_student_accepts(people):
    rel = invite(people).json()
    for path in ("progress/weekly/", "sessions/", "measurements/"):
        assert people["coach"][0].get(f"{REL}{rel['id']}/{path}").status_code == 403


def test_each_permission_opens_only_its_own_data(people):
    rel_id = active_relationship(people, progress=True, sessions=False, measurements=False)
    coach_client = people["coach"][0]
    assert coach_client.get(f"{REL}{rel_id}/progress/weekly/").status_code == 200
    assert coach_client.get(f"{REL}{rel_id}/sessions/").status_code == 403
    response = coach_client.get(f"{REL}{rel_id}/measurements/")
    assert response.status_code == 403 and response.json()["error"]["code"] == "not_authorized"


def test_only_the_student_controls_what_is_shared(people):
    rel_id = active_relationship(people, progress=True, sessions=True)
    assert people["coach"][0].patch(f"{REL}{rel_id}/", {"permissions": {"measurements": True}}, format="json").status_code == 403
    updated = people["student"][0].patch(f"{REL}{rel_id}/", {"permissions": {"sessions": False, "measurements": True}}, format="json")
    assert updated.json()["permissions"] == {"progress": True, "sessions": False, "measurements": True}
    assert people["coach"][0].get(f"{REL}{rel_id}/sessions/").status_code == 403
    assert people["coach"][0].get(f"{REL}{rel_id}/measurements/").status_code == 200


def test_coach_sees_authorized_sessions_but_strangers_see_nothing(people):
    rel_id = active_relationship(people)
    student_client = people["student"][0]
    student_client.post("/api/v1/workouts/", {
        "original_scheduled_date": "2026-10-05", "scheduled_date": "2026-10-05", "started_at": "2026-10-05T21:00:00Z",
        "finished_at": "2026-10-05T22:00:00Z", "status": "completed", "unit": "kg",
        "exercises": [{"exercise_id": "chest-bench-press", "order": 0, "sets": [{"set_number": 1, "weight": 60, "reps": 8, "completed": True}]}],
    }, format="json")
    sessions = people["coach"][0].get(f"{REL}{rel_id}/sessions/").json()["results"]
    assert len(sessions) == 1 and sessions[0]["exercises"][0]["sets"][0]["weight"] == 60.0
    weeks = people["coach"][0].get(f"{REL}{rel_id}/progress/weekly/", {"from": "2026-10-05", "to": "2026-10-05"}).json()["weeks"]
    assert weeks[0]["volume"] == 480.0
    assert people["other"][0].get(f"{REL}{rel_id}/sessions/").status_code == 404


# ── Groups ──────────────────────────────────────────────────────────────────

def test_coach_groups_are_private_to_the_coach(people):
    rel_id = active_relationship(people)
    group = people["coach"][0].post(GROUPS, {"name": "Martes 19 h"}, format="json").json()
    assert people["coach"][0].patch(f"{REL}{rel_id}/", {"group": group["id"]}, format="json").json()["group"]["name"] == "Martes 19 h"
    assert people["student"][0].get(f"{REL}{rel_id}/").json()["group"] is None
    foreign = people["other"][0].post(GROUPS, {"name": "Ajeno"}, format="json").json()
    assert people["coach"][0].patch(f"{REL}{rel_id}/", {"group": foreign["id"]}, format="json").status_code == 400
    assert people["coach"][0].post(GROUPS, {"name": "Martes 19 h"}, format="json").status_code == 409


# ── Assignments ─────────────────────────────────────────────────────────────

def test_assigning_gives_the_student_their_own_routine(people):
    rel_id = active_relationship(people)
    routine = coach_routine(people)
    response = people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"]}, format="json")
    assert response.status_code == 201
    assignment = response.json()
    student_routine = people["student"][0].get(f"{ROUTINES}{assignment['student_routine']}/").json()
    assert student_routine["name"] == "Fuerza 5x5"
    assert student_routine["attribution"] == "Asignada por @profe"
    assert student_routine["assignment"] == {"id": assignment["id"], "coach_username": "profe", "can_edit_plan": False}
    assert student_routine["is_active"] is True  # the student had no active routine
    assert student_routine["plan"]["weeks"][0]["days"][0]["exercises"][0]["exercise_name"] == "Press banca"
    assert people["student"][0].get(ASSIGN).json()["results"][0]["source_routine"] is None  # the coach's routine id stays private
    assert people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"]}, format="json").status_code == 409


def test_cannot_assign_without_an_active_relationship_or_with_foreign_routines(people):
    pending = invite(people).json()
    routine = coach_routine(people)
    assert people["coach"][0].post(ASSIGN, {"relationship": pending["id"], "routine": routine["id"]}, format="json").status_code == 409
    people["student"][0].post(f"{REL}{pending['id']}/accept/", {}, format="json")
    others_routine = people["other"][0].post(ROUTINES, {"name": "Ajena", "goal": "strength", "planning_mode": "repeat", "start_date": "2026-10-05", "plan": plan()}, format="json").json()
    assert people["coach"][0].post(ASSIGN, {"relationship": pending["id"], "routine": others_routine["id"]}, format="json").status_code == 400
    assert people["other"][0].post(ASSIGN, {"relationship": pending["id"], "routine": others_routine["id"]}, format="json").status_code == 404


def test_coach_private_exercises_are_copied_once_to_the_student(people):
    rel_id = active_relationship(people)
    coach = people["coach"][1]
    secret = Exercise.objects.create(owner=coach, name="Press Larsen", primary_muscle="chest", equipment="barbell", category="compound", modality="free_weight")
    exercises = [{"id": "p1", "exercise_id": str(secret.id), "order": 0, "sets": 3, "reps_min": 5, "reps_max": 5}]
    for name in ("Rutina A", "Rutina B"):
        routine = coach_routine(people, exercises=exercises, name=name)
        people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"]}, format="json")
    copies = Exercise.objects.filter(owner=people["student"][1], copied_from=secret)
    assert copies.count() == 1
    student_plans = [r["plan"]["weeks"][0]["days"][0]["exercises"][0] for r in people["student"][0].get(ROUTINES).json()["results"]]
    assert {p["exercise_id"] for p in student_plans} == {str(copies.get().id)}
    assert {p["exercise_name"] for p in student_plans} == {"Press Larsen"}


def test_coach_updates_reach_the_student_automatically_without_rewriting_history(people):
    rel_id = active_relationship(people)
    routine = coach_routine(people)
    assignment = people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"]}, format="json").json()
    student_client = people["student"][0]
    session = student_client.post("/api/v1/workouts/", {
        "routine": assignment["student_routine"], "training_day_ref": "d00", "original_scheduled_date": "2026-10-05", "scheduled_date": "2026-10-05",
        "started_at": "2026-10-05T21:00:00Z", "finished_at": "2026-10-05T22:00:00Z", "status": "completed", "unit": "kg",
        "exercises": [{"exercise_id": "chest-bench-press", "order": 0, "sets": [{"set_number": 1, "weight": 70, "reps": 8, "completed": True}]}],
    }, format="json").json()

    new_plan = plan(exercises=[{"id": "p9", "exercise_id": "quads-back-squat", "order": 0, "sets": 5, "reps_min": 5, "reps_max": 5, "target_weight": 100}])
    people["coach"][0].patch(f"{ROUTINES}{routine['id']}/", {"plan": new_plan, "name": "Fuerza 5x5 v2", "base_version": 1}, format="json")

    updated = student_client.get(f"{ROUTINES}{assignment['student_routine']}/").json()
    assert updated["name"] == "Fuerza 5x5 v2" and updated["plan_version"] == 2
    assert updated["plan"]["weeks"][0]["days"][0]["exercises"][0]["exercise_id"] == "quads-back-squat"
    versions = student_client.get(f"{ROUTINES}{assignment['student_routine']}/versions/").json()
    assert [v["origin"] for v in versions] == ["coach_update", "coach_update"]
    history = student_client.get(f"/api/v1/workouts/{session['id']}/").json()
    assert history["routine_version_number"] == 1 and history["exercises"][0]["planned"]["weight"] == 70.0


def test_student_without_permission_cannot_change_the_plan_but_can_use_it(people):
    rel_id = active_relationship(people)
    routine = coach_routine(people)
    assignment = people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"]}, format="json").json()
    student_client = people["student"][0]
    url = f"{ROUTINES}{assignment['student_routine']}/"
    current = student_client.get(url).json()

    locked = student_client.patch(url, {"plan": plan(exercises=[]), "base_version": current["version"]}, format="json")
    assert locked.status_code == 403 and locked.json()["error"]["code"] == "plan_locked"
    assert student_client.patch(url, {"name": "Mía", "base_version": current["version"]}, format="json").status_code == 403
    assert student_client.delete(url).status_code == 403

    # The app sends the whole routine: unchanged plan + scheduling changes are fine.
    full = {k: current[k] for k in ("name", "goal", "duration_weeks", "planning_mode", "start_date", "plan")}
    ok = student_client.patch(url, {**full, "is_active": False, "date_overrides": [{"from_date": "2026-10-06", "to_date": "2026-10-07"}], "base_version": current["version"]}, format="json")
    assert ok.status_code == 200, ok.json()


def test_with_permission_the_student_edits_their_copy_and_the_coach_still_wins(people):
    rel_id = active_relationship(people)
    routine = coach_routine(people)
    assignment = people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"], "can_edit_plan": True}, format="json").json()
    student_client = people["student"][0]
    url = f"{ROUTINES}{assignment['student_routine']}/"
    version = student_client.get(url).json()["version"]
    own_plan = plan(exercises=[{"id": "x", "exercise_id": "biceps-barbell-curl", "order": 0, "sets": 3, "reps_min": 10, "reps_max": 12}])
    assert student_client.patch(url, {"plan": own_plan, "base_version": version}, format="json").status_code == 200
    assert Routine.objects.get(id=routine["id"]).latest_version.content["weeks"][0]["days"][0]["exercises"][0]["exercise_id"] == "chest-bench-press"

    coach_plan = plan(exercises=[{"id": "y", "exercise_id": "back-barbell-row", "order": 0, "sets": 4, "reps_min": 8, "reps_max": 10}])
    people["coach"][0].patch(f"{ROUTINES}{routine['id']}/", {"plan": coach_plan, "base_version": 1}, format="json")
    after = student_client.get(url).json()
    assert after["plan"]["weeks"][0]["days"][0]["exercises"][0]["exercise_id"] == "back-barbell-row"
    versions = student_client.get(f"{url}versions/").json()
    assert [v["origin"] for v in versions] == ["coach_update", "owner_edit", "coach_update"]
    assert versions[1]["content"]["weeks"][0]["days"][0]["exercises"][0]["exercise_id"] == "biceps-barbell-curl"  # recoverable


def test_coach_can_grant_and_remove_edit_permission(people):
    rel_id = active_relationship(people)
    routine = coach_routine(people)
    assignment = people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"]}, format="json").json()
    assert people["student"][0].patch(f"{ASSIGN}{assignment['id']}/", {"can_edit_plan": True}, format="json").status_code == 404
    granted = people["coach"][0].patch(f"{ASSIGN}{assignment['id']}/", {"can_edit_plan": True}, format="json")
    assert granted.json()["can_edit_plan"] is True
    assert people["student"][0].get(f"{ROUTINES}{assignment['student_routine']}/").json()["assignment"]["can_edit_plan"] is True


def test_ending_the_relationship_keeps_everything_for_the_student(people):
    rel_id = active_relationship(people)
    routine = coach_routine(people)
    assignment = people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"]}, format="json").json()
    ended = people["student"][0].post(f"{REL}{rel_id}/end/")
    assert ended.json()["status"] == "revoked" and ended.json()["permissions"] == {"progress": False, "sessions": False, "measurements": False}
    assert people["coach"][0].get(f"{REL}{rel_id}/progress/weekly/").status_code == 403

    url = f"{ROUTINES}{assignment['student_routine']}/"
    mine = people["student"][0].get(url).json()
    assert mine["assignment"] is None  # unlocked: fully theirs now
    assert people["student"][0].patch(url, {"name": "Ahora es mía", "base_version": mine["version"]}, format="json").status_code == 200

    people["coach"][0].patch(f"{ROUTINES}{routine['id']}/", {"name": "Cambio posterior", "base_version": 1}, format="json")
    assert people["student"][0].get(url).json()["name"] == "Ahora es mía"  # no longer follows the coach


def test_student_can_end_a_single_assignment(people):
    rel_id = active_relationship(people)
    routine = coach_routine(people)
    assignment = people["coach"][0].post(ASSIGN, {"relationship": rel_id, "routine": routine["id"]}, format="json").json()
    assert people["other"][0].post(f"{ASSIGN}{assignment['id']}/end/").status_code == 404
    assert people["student"][0].post(f"{ASSIGN}{assignment['id']}/end/").json()["status"] == "ended"
    people["coach"][0].patch(f"{ROUTINES}{routine['id']}/", {"name": "No llega", "base_version": 1}, format="json")
    assert people["student"][0].get(f"{ROUTINES}{assignment['student_routine']}/").json()["name"] == "Fuerza 5x5"


@pytest.mark.parametrize("path", [REL, ASSIGN, GROUPS])
def test_coaching_requires_authentication(api, path):
    assert api.get(path).status_code == 401
