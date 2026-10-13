"""People search is opt-in and shows almost nothing: username, coach label, published routines."""
import pytest
from django.core.cache import cache
from rest_framework.settings import api_settings

pytestmark = pytest.mark.django_db
S = "/api/v1/users/search/"


@pytest.fixture
def people(auth_client, make_user):
    def person(name, **flags):
        return auth_client(make_user(username=name, **flags))[0]
    return {
        "yo": person("yo_busco"),
        "visible": person("ana_entrena", discoverable=True),
        "coach": person("ana_coach", discoverable=True, is_coach=True),
        "oculta": person("ana_oculta"),
        "coach_oculto": person("ana_coach_oculto", is_coach=True),
    }


def names(response):
    return [p["username"] for p in response.json()["results"]]


def test_only_people_who_opted_in_appear(people):
    assert names(people["yo"].get(S, {"q": "ana"})) == ["ana_coach", "ana_entrena"]


def test_coach_filter_needs_both_labels(people):
    assert names(people["yo"].get(S, {"coach": "1"})) == ["ana_coach"]


def test_results_expose_only_the_minimum(people):
    result = people["yo"].get(S, {"q": "ana_entrena"}).json()["results"][0]
    assert set(result) == {"username", "is_coach", "routines_count"}
    assert result["routines_count"] == 0


def test_you_never_find_yourself(people):
    people["yo"].patch("/api/v1/users/me/", {"discoverable": True}, format="json")
    assert names(people["yo"].get(S, {"q": "yo"})) == []


def test_without_text_it_lists_everyone_who_opted_in(people):
    assert names(people["yo"].get(S)) == ["ana_coach", "ana_entrena"]
    assert names(people["yo"].get(S, {"q": ""})) == ["ana_coach", "ana_entrena"]


def test_opting_in_and_out_from_the_profile(people):
    oculta = people["oculta"]
    me = oculta.patch("/api/v1/users/me/", {"discoverable": True, "is_coach": True}, format="json").json()
    assert (me["discoverable"], me["is_coach"]) == (True, True)
    assert "ana_oculta" in names(people["yo"].get(S, {"q": "ana"}))
    oculta.patch("/api/v1/users/me/", {"discoverable": False}, format="json")
    assert "ana_oculta" not in names(people["yo"].get(S, {"q": "ana"}))


def test_search_needs_an_account_and_is_rate_limited(api, people, monkeypatch):
    assert api.get(S, {"q": "ana"}).status_code == 401
    from apps.accounts.views import PeopleSearchThrottle

    cache.clear()
    monkeypatch.setattr(PeopleSearchThrottle, "THROTTLE_RATES", {**api_settings.DEFAULT_THROTTLE_RATES, "search": "2/min"})
    assert [people["yo"].get(S, {"q": "ana"}).status_code for _ in range(3)] == [200, 200, 429]
    cache.clear()
