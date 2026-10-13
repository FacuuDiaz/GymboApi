"""Sharing with people and by link, community catalog, independent copies and reports."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.sharing.models import ShareLink
from apps.training.models import Exercise, Routine
from apps.training.tests.factories import plan

pytestmark = pytest.mark.django_db
R = "/api/v1/routines/"


@pytest.fixture
def users(auth_client, make_user):
    def client(name):
        c, u, _ = auth_client(make_user(username=name))
        return c, u
    return {name: client(name) for name in ("autora", "amigo", "extrano", "otro")}


def create_routine(client, **extra):
    payload = {"name": "Push Pull Legs", "goal": "hypertrophy", "planning_mode": "repeat", "start_date": "2026-10-05", "plan": plan(), **extra}
    return client.post(R, payload, format="json").json()


def share(users, routine, role="viewer", to="amigo"):
    return users["autora"][0].post(f"{R}{routine['id']}/shares/", {"username": to, "role": role}, format="json")


def accepted_share(users, routine, role):
    s = share(users, routine, role).json()
    users["amigo"][0].post(f"/api/v1/routine-shares/{s['id']}/accept/")
    return s


# ── Sharing with people ─────────────────────────────────────────────────────

def test_share_needs_acceptance_and_exposes_only_the_routine(users):
    routine = create_routine(users["autora"][0])
    s = share(users, routine).json()
    assert s["status"] == "pending"
    amigo = users["amigo"][0]
    assert amigo.get(f"/api/v1/shared/routines/{routine['id']}/").status_code == 404  # not before accepting
    received = amigo.get("/api/v1/routine-shares/", {"direction": "received"}).json()["results"]
    assert [r["routine_name"] for r in received] == ["Push Pull Legs"]
    amigo.post(f"/api/v1/routine-shares/{s['id']}/accept/")
    seen = amigo.get(f"/api/v1/shared/routines/{routine['id']}/").json()
    assert (seen["author"], seen["role"]) == ("autora", "viewer")
    assert "owner" not in seen and "email" not in str(seen)


@pytest.mark.parametrize("username,code", [("autora", 400), ("nadie", 400)])
def test_cannot_share_with_self_or_unknown(users, username, code):
    routine = create_routine(users["autora"][0])
    assert share(users, routine, to=username).status_code == code


def test_only_the_owner_manages_sharing(users):
    routine = create_routine(users["autora"][0])
    assert share(users, routine).status_code == 201
    assert share(users, routine).status_code == 409
    intruder = users["extrano"][0]
    assert intruder.post(f"{R}{routine['id']}/shares/", {"username": "otro"}, format="json").status_code == 404
    assert intruder.get(f"{R}{routine['id']}/shares/").status_code == 404
    assert intruder.post(f"{R}{routine['id']}/links/", {}, format="json").status_code == 404
    assert intruder.get(f"/api/v1/shared/routines/{routine['id']}/").status_code == 404


def test_viewer_cannot_copy_or_edit(users):
    routine = create_routine(users["autora"][0])
    accepted_share(users, routine, "viewer")
    amigo = users["amigo"][0]
    assert amigo.post(f"/api/v1/shared/routines/{routine['id']}/copy/").json()["error"]["code"] == "insufficient_role"
    assert amigo.patch(f"/api/v1/shared/routines/{routine['id']}/", {"name": "x", "base_version": 1}, format="json").status_code == 403


def test_copies_are_independent_and_carry_attribution(users):
    autora_client, autora = users["autora"]
    routine = create_routine(autora_client)
    autora_client.post("/api/v1/workouts/", {"routine": routine["id"], "original_scheduled_date": "2026-10-05", "scheduled_date": "2026-10-05", "started_at": "2026-10-05T21:00:00Z", "finished_at": "2026-10-05T22:00:00Z", "status": "completed", "exercises": []}, format="json")
    accepted_share(users, routine, "copier")
    amigo_client, amigo = users["amigo"]
    copy = amigo_client.post(f"/api/v1/shared/routines/{routine['id']}/copy/")
    assert copy.status_code == 201
    copy = copy.json()
    assert copy["attribution"] == "Basada en «Push Pull Legs» de @autora"
    assert copy["is_active"] is False and copy["plan"]["weeks"] == routine["plan"]["weeks"]
    assert Routine.objects.get(id=copy["id"]).owner == amigo
    assert amigo_client.get("/api/v1/workouts/").json()["results"] == []  # no sessions of the author come along

    amigo_client.patch(f"{R}{copy['id']}/", {"name": "Mía", "plan": plan(exercises=[]), "base_version": 1}, format="json")
    original = autora_client.get(f"{R}{routine['id']}/").json()
    assert original["name"] == "Push Pull Legs" and len(original["plan"]["weeks"][0]["days"][0]["exercises"]) == 1
    autora_client.patch(f"{R}{routine['id']}/", {"name": "Cambio de la autora", "base_version": 1}, format="json")
    assert amigo_client.get(f"{R}{copy['id']}/").json()["name"] == "Mía"


def test_editor_edits_the_owners_plan_but_nothing_else(users):
    autora_client, autora = users["autora"]
    routine = create_routine(autora_client, is_active=True)
    accepted_share(users, routine, "editor")
    amigo_client, amigo = users["amigo"]
    url = f"/api/v1/shared/routines/{routine['id']}/"
    new_plan = plan(exercises=[{"id": "x", "exercise_id": "back-barbell-row", "order": 0, "sets": 4, "reps_min": 8, "reps_max": 10}])
    edited = amigo_client.patch(url, {"plan": new_plan, "name": "Mejorada", "is_active": False, "base_version": 1}, format="json")
    assert edited.status_code == 200
    mine = autora_client.get(f"{R}{routine['id']}/").json()
    assert (mine["name"], mine["is_active"], mine["plan_version"]) == ("Mejorada", True, 2)  # is_active was ignored
    version = Routine.objects.get(id=routine["id"]).latest_version
    assert (version.origin, version.created_by) == ("shared_edit", amigo)
    assert amigo_client.patch(url, {"name": "Vieja", "base_version": 1}, format="json").status_code == 409

    private = Exercise.objects.create(owner=amigo, name="Secreto del editor", primary_muscle="back", equipment="cable", category="isolation", modality="machine")
    bad = plan(exercises=[{"id": "y", "exercise_id": str(private.id), "order": 0, "sets": 3, "reps_min": 10, "reps_max": 12}])
    assert amigo_client.patch(url, {"plan": bad, "base_version": mine["version"]}, format="json").status_code == 400  # the owner could not see it


def test_revoking_removes_access_immediately(users):
    routine = create_routine(users["autora"][0])
    s = accepted_share(users, routine, "copier")
    assert users["autora"][0].delete(f"/api/v1/routine-shares/{s['id']}/").status_code == 204
    assert users["amigo"][0].get(f"/api/v1/shared/routines/{routine['id']}/").status_code == 404
    assert users["amigo"][0].post(f"/api/v1/shared/routines/{routine['id']}/copy/").status_code == 404


def test_owner_can_change_a_role(users):
    routine = create_routine(users["autora"][0])
    s = accepted_share(users, routine, "viewer")
    assert users["amigo"][0].patch(f"/api/v1/routine-shares/{s['id']}/", {"role": "editor"}, format="json").status_code == 404
    users["autora"][0].patch(f"/api/v1/routine-shares/{s['id']}/", {"role": "copier"}, format="json")
    assert users["amigo"][0].post(f"/api/v1/shared/routines/{routine['id']}/copy/").status_code == 201


# ── Links ───────────────────────────────────────────────────────────────────

def test_link_token_is_shown_once_and_only_its_hash_is_stored(users):
    routine = create_routine(users["autora"][0])
    created = users["autora"][0].post(f"{R}{routine['id']}/links/", {"role": "copier", "expires_in_days": 7}, format="json").json()
    token = created["token"]
    assert len(token) >= 40 and created["token_hint"] == token[:6]
    listed = users["autora"][0].get(f"{R}{routine['id']}/links/").json()
    assert "token" not in listed[0]
    stored = ShareLink.objects.get(id=created["id"])
    assert token not in str(stored.__dict__)


def test_anyone_with_a_link_can_view_but_copying_needs_an_account_and_permission(users, api):
    autora_client = users["autora"][0]
    routine = create_routine(autora_client)
    viewer_token = autora_client.post(f"{R}{routine['id']}/links/", {"role": "viewer"}, format="json").json()["token"]
    copier_token = autora_client.post(f"{R}{routine['id']}/links/", {"role": "copier"}, format="json").json()["token"]

    seen = api.post("/api/v1/shared-links/resolve/", {"token": viewer_token}, format="json")
    assert seen.status_code == 200 and seen.json()["author"] == "autora" and seen.json()["role"] == "viewer"
    assert api.get("/api/v1/shared-links/resolve/").status_code == 405  # tokens never go in URLs
    assert api.post("/api/v1/shared-links/copy/", {"token": copier_token}, format="json").status_code == 401
    assert users["amigo"][0].post("/api/v1/shared-links/copy/", {"token": viewer_token}, format="json").status_code == 403
    assert users["amigo"][0].post("/api/v1/shared-links/copy/", {"token": copier_token}, format="json").status_code == 201
    assert ShareLink.objects.get(token_hint=viewer_token[:6]).uses == 1


def test_revoked_expired_unknown_and_deleted_links_all_fail_the_same_way(users, api):
    autora_client = users["autora"][0]
    routine = create_routine(autora_client)
    make = lambda: autora_client.post(f"{R}{routine['id']}/links/", {}, format="json").json()  # noqa: E731
    revoked, expired, alive = make(), make(), make()
    autora_client.delete(f"/api/v1/share-links/{revoked['id']}/")
    ShareLink.objects.filter(id=expired["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
    for token in (revoked["token"], expired["token"], "no-es-un-token"):
        response = api.post("/api/v1/shared-links/resolve/", {"token": token}, format="json")
        assert (response.status_code, response.json()["error"]["code"]) == (404, "link_invalid")
    autora_client.delete(f"{R}{routine['id']}/")
    assert api.post("/api/v1/shared-links/resolve/", {"token": alive["token"]}, format="json").status_code == 404
    assert users["extrano"][0].delete(f"/api/v1/share-links/{alive['id']}/").status_code == 404


def test_links_cannot_grant_editing(users):
    routine = create_routine(users["autora"][0])
    assert users["autora"][0].post(f"{R}{routine['id']}/links/", {"role": "editor"}, format="json").status_code == 400


# ── Community ───────────────────────────────────────────────────────────────

def test_publishing_lists_the_routine_publicly_with_only_the_authors_username(users, api):
    autora_client = users["autora"][0]
    routine = create_routine(autora_client)
    assert api.get("/api/v1/community/routines/").json()["results"] == []
    autora_client.post(f"{R}{routine['id']}/publish/")
    listed = api.get("/api/v1/community/routines/").json()["results"]
    assert [r["name"] for r in listed] == ["Push Pull Legs"]
    assert set(listed[0]) == {"id", "name", "goal", "duration_weeks", "planning_mode", "author", "attribution", "plan", "plan_version", "published_at", "copies_count", "stars_count", "starred", "updated_at"}
    assert listed[0]["author"] == "autora"
    assert api.get("/api/v1/community/routines/", {"q": "push"}).json()["results"] and not api.get("/api/v1/community/routines/", {"goal": "strength"}).json()["results"]
    autora_client.post(f"{R}{routine['id']}/unpublish/")
    assert api.get("/api/v1/community/routines/").json()["results"] == []


def test_copying_from_the_catalog_needs_an_account_and_counts(users, api):
    autora_client = users["autora"][0]
    routine = create_routine(autora_client)
    autora_client.post(f"{R}{routine['id']}/publish/")
    url = f"/api/v1/community/routines/{routine['id']}/"
    assert api.post(f"{url}copy/").status_code == 401
    assert users["amigo"][0].post(f"{url}copy/").status_code == 201
    assert users["otro"][0].post(f"{url}copy/").status_code == 201
    assert api.get(url).json()["copies_count"] == 2


def test_copied_routines_survive_the_author_deleting_their_exercise(users):
    autora_client, autora = users["autora"]
    mine = Exercise.objects.create(owner=autora, name="Remo invertido", primary_muscle="back", equipment="bodyweight", category="compound", modality="bodyweight")
    routine = create_routine(autora_client, plan=plan(exercises=[{"id": "p", "exercise_id": str(mine.id), "order": 0, "sets": 3, "reps_min": 8, "reps_max": 12}]))
    autora_client.post(f"{R}{routine['id']}/publish/")
    amigo_client, amigo = users["amigo"]
    copy = amigo_client.post(f"/api/v1/community/routines/{routine['id']}/copy/").json()
    copied_exercise = Exercise.objects.get(owner=amigo, copied_from=mine)
    assert copy["plan"]["weeks"][0]["days"][0]["exercises"][0]["exercise_id"] == str(copied_exercise.id)
    autora_client.delete(f"/api/v1/exercises/{mine.id}/")
    assert amigo_client.get(f"/api/v1/exercises/{copied_exercise.id}/").status_code == 200
    again = amigo_client.patch(f"{R}{copy['id']}/", {"name": "Sigo editándola", "plan": copy["plan"], "base_version": 1}, format="json")
    assert again.status_code == 200


def test_community_exercises_publish_copy_and_attribution_rules(users, api):
    autora_client, autora = users["autora"]
    exercise = autora_client.post("/api/v1/exercises/", {"name": "Press Landmine", "primary_muscle": "shoulders", "equipment": "barbell", "category": "compound", "modality": "free_weight"}, format="json").json()
    assert api.get("/api/v1/community/exercises/").json()["results"] == []
    autora_client.post(f"/api/v1/exercises/{exercise['id']}/publish/")
    listed = api.get("/api/v1/community/exercises/").json()["results"]
    assert [(e["name"], e["author"]) for e in listed] == [("Press Landmine", "autora")]
    amigo_client = users["amigo"][0]
    copy = amigo_client.post(f"/api/v1/community/exercises/{exercise['id']}/copy/").json()
    assert copy["is_own"] is True and copy["id"] != exercise["id"]
    assert amigo_client.post(f"/api/v1/exercises/{copy['id']}/publish/").json()["error"]["code"] == "not_author"
    autora_client.patch(f"/api/v1/exercises/{exercise['id']}/", {"name": "Cambiado", "base_version": 2}, format="json")
    assert amigo_client.get(f"/api/v1/exercises/{copy['id']}/").json()["name"] == "Press Landmine"


def test_a_student_cannot_redistribute_a_routine_their_coach_assigned(users):
    coach_client = users["autora"][0]
    student_client = users["amigo"][0]
    rel = coach_client.post("/api/v1/coaching/relationships/invite/", {"username": "amigo"}, format="json").json()
    student_client.post(f"/api/v1/coaching/relationships/{rel['id']}/accept/", {}, format="json")
    routine = create_routine(coach_client)
    assignment = coach_client.post("/api/v1/coaching/assignments/", {"relationship": rel["id"], "routine": routine["id"]}, format="json").json()
    student_routine = assignment["student_routine"]
    for path in ("publish/", "links/", "shares/"):
        response = student_client.post(f"{R}{student_routine}/{path}", {"username": "otro"}, format="json")
        assert response.status_code == 403 and response.json()["error"]["code"] == "assigned_routine"


# ── Reports and moderation ──────────────────────────────────────────────────

def test_reports_hide_content_after_three_people_and_block_republishing(users, api):
    autora_client = users["autora"][0]
    routine = create_routine(autora_client)
    autora_client.post(f"{R}{routine['id']}/publish/")
    report = {"kind": "routine", "object_id": routine["id"], "reason": "spam", "details": "Publicidad"}
    assert autora_client.post("/api/v1/reports/", report, format="json").status_code == 400  # own content
    assert users["amigo"][0].post("/api/v1/reports/", report, format="json").status_code == 201
    assert users["amigo"][0].post("/api/v1/reports/", report, format="json").status_code == 409
    users["extrano"][0].post("/api/v1/reports/", report, format="json")
    assert api.get("/api/v1/community/routines/").json()["results"] != []  # two reports: still listed
    users["otro"][0].post("/api/v1/reports/", report, format="json")
    assert api.get("/api/v1/community/routines/").json()["results"] == []  # three: hidden pending review
    assert autora_client.get(f"{R}{routine['id']}/").status_code == 200  # still the author's
    autora_client.post(f"{R}{routine['id']}/unpublish/")
    assert autora_client.post(f"{R}{routine['id']}/publish/").json()["error"]["code"] == "moderated"


def test_only_community_content_can_be_reported(users, api):
    private = create_routine(users["autora"][0])
    assert users["amigo"][0].post("/api/v1/reports/", {"kind": "routine", "object_id": private["id"], "reason": "spam"}, format="json").status_code == 404
    assert api.post("/api/v1/reports/", {"kind": "routine", "object_id": private["id"], "reason": "spam"}, format="json").status_code == 401
