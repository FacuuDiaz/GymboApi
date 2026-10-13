"""Stars on published routines, like GitHub stars: once per person, never your own, listed in "my stars"."""
import pytest

from apps.sharing.models import RoutineStar
from apps.training.tests.factories import plan

pytestmark = pytest.mark.django_db
R = "/api/v1/routines/"
C = "/api/v1/community/routines/"


@pytest.fixture
def people(auth_client, make_user):
    return {name: auth_client(make_user(username=name))[0] for name in ("autora", "fan", "otra")}


def published(client, name="Piernas"):
    routine = client.post(R, {"name": name, "goal": "strength", "planning_mode": "repeat", "start_date": "2026-10-05", "plan": plan()}, format="json").json()
    client.post(f"{R}{routine['id']}/publish/")
    return routine


def test_star_once_and_see_it_in_counts_and_my_stars(people, api):
    routine = published(people["autora"])
    fan = people["fan"]
    first = fan.post(f"{C}{routine['id']}/star/")
    assert (first.status_code, first.json()) == (201, {"starred": True, "stars_count": 1})
    again = fan.post(f"{C}{routine['id']}/star/")
    assert (again.status_code, again.json()) == (200, {"starred": True, "stars_count": 1})  # already starred: still one star
    assert RoutineStar.objects.count() == 1
    people["otra"].post(f"{C}{routine['id']}/star/")

    seen_by_fan = fan.get(f"{C}{routine['id']}/").json()
    assert (seen_by_fan["stars_count"], seen_by_fan["starred"]) == (2, True)
    assert people["autora"].get(f"{C}{routine['id']}/").json()["starred"] is False
    assert api.get(C).json()["results"][0]["stars_count"] == 2  # guests see the count, never "starred"

    mine = fan.get("/api/v1/users/me/stars/").json()["results"]
    assert [(r["id"], r["name"], r["author"]) for r in mine] == [(routine["id"], "Piernas", "autora")]
    assert mine[0]["starred_at"]


def test_no_stars_on_your_own_routine(people):
    routine = published(people["autora"])
    response = people["autora"].post(f"{C}{routine['id']}/star/")
    assert response.status_code == 400
    assert not RoutineStar.objects.exists()


def test_only_published_routines_can_be_starred(people):
    private = people["autora"].post(R, {"name": "Privada", "goal": "strength", "planning_mode": "repeat", "start_date": "2026-10-05", "plan": plan()}, format="json").json()
    assert people["fan"].post(f"{C}{private['id']}/star/").status_code == 404


def test_unstar_is_idempotent_and_unpublished_routines_leave_my_stars(people):
    autora, fan = people["autora"], people["fan"]
    routine = published(autora)
    fan.post(f"{C}{routine['id']}/star/")
    autora.post(f"{R}{routine['id']}/unpublish/")
    assert fan.get("/api/v1/users/me/stars/").json()["results"] == []
    autora.post(f"{R}{routine['id']}/publish/")  # the star is still there when it comes back
    assert fan.get("/api/v1/users/me/stars/").json()["results"][0]["id"] == routine["id"]
    assert fan.delete(f"{C}{routine['id']}/star/").status_code == 204
    assert fan.delete(f"{C}{routine['id']}/star/").status_code == 204
    assert fan.get(f"{C}{routine['id']}/").json()["stars_count"] == 0


def test_catalog_can_be_sorted_by_stars(people, api):
    autora = people["autora"]
    popular, quiet = published(autora, "Popular"), published(autora, "Tranquila")
    for name in ("fan", "otra"):
        people[name].post(f"{C}{popular['id']}/star/")
    by_recent = [r["name"] for r in api.get(C).json()["results"]]
    by_stars = [r["name"] for r in api.get(C, {"ordering": "stars"}).json()["results"]]
    assert by_recent == ["Tranquila", "Popular"] and by_stars == ["Popular", "Tranquila"]
    assert quiet["id"]


def test_starring_needs_an_account(api, people):
    routine = published(people["autora"])
    assert api.post(f"{C}{routine['id']}/star/").status_code == 401
    assert api.get("/api/v1/users/me/stars/").status_code == 401


def test_deleting_an_account_removes_its_stars_and_the_stars_on_its_routines(people):
    autora, fan = people["autora"], people["fan"]
    routine = published(autora)
    fan.post(f"{C}{routine['id']}/star/")
    other = published(fan, "De fan")
    autora.post(f"{C}{other['id']}/star/")
    assert autora.delete("/api/v1/users/me/", {"password": "una-clave-segura-123"}, format="json").status_code == 204
    assert not RoutineStar.objects.exists()
