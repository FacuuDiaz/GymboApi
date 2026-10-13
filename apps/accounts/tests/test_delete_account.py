"""
Account deletion: everything of the person goes, sessions end at once, and
nobody else loses anything they built (copies, assigned routines, history).
"""
import pytest
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from apps.accounts.models import SocialIdentity, TrainingPreferences, User
from apps.coaching.models import CoachingRelationship, RoutineAssignment
from apps.sharing.models import ContentReport, RoutineShare, ShareLink
from apps.training.models import BodyMeasurement, Exercise, Routine, WorkoutSession
from apps.training.tests.factories import make_session, plan

pytestmark = pytest.mark.django_db
ME = "/api/v1/users/me/"
R = "/api/v1/routines/"
PASSWORD = "una-clave-segura-123"


@pytest.fixture
def people(auth_client, make_user):
    def person(name):
        client, user, tokens = auth_client(make_user(username=name))
        return client, user, tokens
    return {name: person(name) for name in ("ana", "ana2", "beto", "carla")}


def create_routine(client, name="Fuerza", exercises=None):
    payload = {"name": name, "goal": "strength", "planning_mode": "repeat", "start_date": "2026-10-05", "plan": plan(exercises=exercises)}
    return client.post(R, payload, format="json").json()


def delete_me(client, **body):
    return client.delete(ME, body or {"password": PASSWORD}, format="json")


def test_needs_the_password_and_changes_nothing_if_wrong(people):
    client, ana, _ = people["ana"]
    create_routine(client)
    response = delete_me(client, password="otra")
    assert response.status_code == 400
    assert User.objects.filter(pk=ana.pk).exists() and Routine.objects.filter(owner=ana).exists()


def test_deletes_everything_personal_and_ends_every_session(people, api):
    client, ana, tokens = people["ana"]
    create_routine(client)
    make_session(ana)
    BodyMeasurement.objects.create(owner=ana, date="2026-10-01", value=70, unit="kg")
    client.post("/api/v1/exercises/", {"name": "Mi press", "primary_muscle": "chest", "equipment": "barbell", "category": "compound", "modality": "free_weight"}, format="json")
    TrainingPreferences.objects.update_or_create(user=ana, defaults={"unit": "lb"})
    SocialIdentity.objects.create(user=ana, provider="google", subject="sub-ana")

    assert delete_me(client).status_code == 204

    assert not User.objects.filter(pk=ana.pk).exists()
    for model in (Routine, WorkoutSession, BodyMeasurement):
        assert not model.objects.filter(owner_id=ana.pk).exists()
    assert not Exercise.objects.filter(name="Mi press").exists()
    assert not TrainingPreferences.objects.filter(user_id=ana.pk).exists()
    assert not SocialIdentity.objects.filter(subject="sub-ana").exists()
    # The access token in hand and the refresh token are both dead.
    assert client.get(ME).status_code == 401
    assert api.post("/api/v1/auth/refresh/", {"refresh": tokens["refresh"]}, format="json").status_code == 401
    assert not OutstandingToken.objects.filter(token=tokens["refresh"]).exists()  # not even the token rows remain
    # The username can be registered again by someone new.
    register = {"username": "ana", "email": "nueva@example.com", "password": PASSWORD}
    assert api.post("/api/v1/auth/register/", register, format="json").status_code == 201


def test_google_only_account_confirms_with_the_username(people):
    client, ana, _ = people["ana"]
    ana.set_unusable_password()
    ana.save()
    assert delete_me(client, confirm_username="beto").status_code == 400
    assert delete_me(client, confirm_username="ANA").status_code == 204


def test_a_deleted_coach_leaves_students_their_routines_unlocked(people):
    coach_client, _, _ = people["ana"]
    student_client, student, _ = people["beto"]
    rel = coach_client.post("/api/v1/coaching/relationships/invite/", {"username": "beto"}, format="json").json()
    student_client.post(f"/api/v1/coaching/relationships/{rel['id']}/accept/", {}, format="json")
    routine = create_routine(coach_client)
    coach_client.post("/api/v1/coaching/assignments/", {"relationship": rel["id"], "routine": routine["id"]}, format="json")
    assigned = Routine.objects.get(owner=student)
    version_before = assigned.version

    assert delete_me(coach_client).status_code == 204

    assigned.refresh_from_db()
    assert assigned.deleted_at is None and assigned.version > version_before  # devices pull the unlocked state
    assert assigned.attribution == "Asignada por una cuenta eliminada"
    seen = student_client.get(f"{R}{assigned.id}/").json()
    assert seen["assignment"] is None and seen["plan"]["weeks"]
    assert not CoachingRelationship.objects.exists() and not RoutineAssignment.objects.exists()


def test_a_deleted_student_does_not_touch_the_coach(people):
    coach_client, coach, _ = people["ana"]
    student_client, _, _ = people["beto"]
    rel = coach_client.post("/api/v1/coaching/relationships/invite/", {"username": "beto"}, format="json").json()
    student_client.post(f"/api/v1/coaching/relationships/{rel['id']}/accept/", {}, format="json")
    routine = create_routine(coach_client)
    coach_client.post("/api/v1/coaching/assignments/", {"relationship": rel["id"], "routine": routine["id"]}, format="json")
    assert delete_me(student_client).status_code == 204
    assert coach_client.get(f"{R}{routine['id']}/").status_code == 200
    assert coach_client.get("/api/v1/coaching/relationships/").json()["results"] == []


def test_copies_survive_without_the_author_and_publications_disappear(people, api):
    author_client, _, _ = people["ana"]
    reader_client, reader, _ = people["beto"]
    namesake_client, namesake, _ = people["ana2"]
    routine = create_routine(author_client, name="Piernas")
    author_client.post(f"{R}{routine['id']}/publish/")
    author_client.post(f"{R}{routine['id']}/shares/", {"username": "carla", "role": "viewer"}, format="json")
    author_client.post(f"{R}{routine['id']}/links/", {"role": "copier"}, format="json")
    copy = reader_client.post(f"/api/v1/community/routines/{routine['id']}/copy/").json()
    # Someone whose username only starts the same must not be rewritten.
    namesake_copy = Routine.objects.create(owner=namesake, name="X", goal="strength", planning_mode="repeat", start_date="2026-10-05", attribution="Basada en «X» de @ana2")

    assert delete_me(author_client).status_code == 204

    kept = Routine.objects.get(pk=copy["id"])
    assert kept.attribution == "Basada en «Piernas» de una cuenta eliminada" and kept.copied_from_version is None
    assert reader_client.get(f"{R}{copy['id']}/").json()["plan"]["weeks"]
    namesake_copy.refresh_from_db()
    assert namesake_copy.attribution == "Basada en «X» de @ana2"
    assert api.get("/api/v1/community/routines/").json()["results"] == []
    assert not RoutineShare.objects.exists() and not ShareLink.objects.exists()


def test_published_exercises_someone_uses_stay_without_author(people, api):
    author_client, author, _ = people["ana"]
    other_client, _, _ = people["beto"]
    body = {"primary_muscle": "shoulders", "equipment": "barbell", "category": "compound", "modality": "free_weight"}
    used = author_client.post("/api/v1/exercises/", {"name": "Landmine", **body}, format="json").json()
    unused = author_client.post("/api/v1/exercises/", {"name": "Otro", **body}, format="json").json()
    for exercise in (used, unused):
        author_client.post(f"/api/v1/exercises/{exercise['id']}/publish/")
    # A plan pointing straight at the published exercise (allowed by the API).
    exercises = [{"id": "p1", "exercise_id": used["id"], "order": 0, "sets": 3, "reps_min": 8, "reps_max": 10}]
    theirs = create_routine(other_client, exercises=exercises)

    assert delete_me(author_client).status_code == 204

    kept = Exercise.objects.get(pk=used["id"])
    assert kept.owner is None and kept.published_at is None
    assert not Exercise.objects.filter(pk=unused["id"]).exists()
    assert api.get("/api/v1/community/exercises/").json()["results"] == []
    # Their routine still validates against the exercise.
    patch = {"plan": plan(exercises=exercises), "base_version": theirs["version"]}
    assert other_client.patch(f"{R}{theirs['id']}/", patch, format="json").status_code == 200


def test_reports_filed_stay_for_moderation_without_the_reporter(people):
    author_client, _, _ = people["beto"]
    reporter_client, _, _ = people["ana"]
    routine = create_routine(author_client)
    author_client.post(f"{R}{routine['id']}/publish/")
    reporter_client.post("/api/v1/reports/", {"kind": "routine", "object_id": routine["id"], "reason": "spam", "details": ""}, format="json")
    assert delete_me(reporter_client).status_code == 204
    report = ContentReport.objects.get()
    assert report.reporter is None
