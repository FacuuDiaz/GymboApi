"""Data export: own data only, no secrets, documented format, nothing stored on the server."""
import io
import json
import zipfile
from datetime import date

import pytest
from django.core.cache import cache
from rest_framework.settings import api_settings

from apps.accounts.models import TrainingPreferences
from apps.exports import format as fmt
from apps.training.models import BodyMeasurement, Exercise, ExerciseSession, WorkoutSet
from apps.training.tests.factories import make_routine, make_session, make_version

pytestmark = pytest.mark.django_db
URL = "/api/v1/exports/"


def training_data(user, *, unit="kg", day=date(2026, 10, 5)):
    routine = make_routine(user, name="Fuerza, básica")
    version = make_version(routine)
    session = make_session(user, routine=routine, routine_version=version, scheduled_date=day, original_scheduled_date=day, unit=unit)
    es = ExerciseSession.objects.create(session=session, exercise_ref="chest-bench-press", exercise_name="Press banca", primary_muscle="chest", order=0)
    WorkoutSet.objects.create(exercise_session=es, set_number=1, weight=82.5, reps=8, rir=2, rest_seconds=90, completed=True)
    WorkoutSet.objects.create(exercise_session=es, set_number=2, weight=None, reps=None, completed=False)
    BodyMeasurement.objects.create(owner=user, date=day, value=80.25, unit="kg")
    Exercise.objects.create(owner=user, name="Mi remo", primary_muscle="back", equipment="cable", category="compound", modality="cable")
    return routine, session


def get(client, **params):
    return client.get(URL, params)


def test_export_needs_an_account(api):
    assert get(api).status_code == 401


def test_json_has_only_my_data_and_no_secrets(auth_client, make_user):
    client, me, tokens = auth_client()
    training_data(me)
    other = make_user(username="ajena")
    other_routine, _ = training_data(other)
    response = get(client)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response["Content-Disposition"].startswith(f'attachment; filename="gymbo-{me.username}-')
    text = response.content.decode()
    doc = json.loads(text)
    assert (doc["format"], doc["version"], doc["source"]) == ("gymbo-export", 1, "account")
    assert doc["account"]["username"] == me.username
    assert [r["name"] for r in doc["routines"]] == ["Fuerza, básica"]
    assert doc["routines"][0]["versions"][0]["number"] == 1
    assert len(doc["sessions"]) == 1 and len(doc["measurements"]) == 1 and len(doc["exercises"]) == 1
    # Nothing from the other account, no credentials of any kind.
    assert str(other_routine.id) not in text and "ajena" not in text
    for secret in (me.password, tokens["access"], tokens["refresh"], "password", "pbkdf2", "argon2"):
        assert secret not in text


def test_session_details_and_weekly_progress_in_the_preferred_unit(auth_client):
    client, me, _ = auth_client()
    TrainingPreferences.objects.update_or_create(user=me, defaults={"unit": "kg"})
    training_data(me, unit="lb")
    doc = get(client).json()
    session = doc["sessions"][0]
    assert session["unit"] == "lb" and session["duration_seconds"] == 55 * 60
    assert session["exercises"][0]["sets"] == [
        {"set_number": 1, "weight": 82.5, "reps": 8, "rir": 2, "rest_seconds": 90, "completed": True},
        {"set_number": 2, "weight": None, "reps": None, "rir": None, "rest_seconds": None, "completed": False},
    ]
    week = doc["weekly_progress"][0]
    assert (week["week_start"], week["sessions"], week["completed_sets"], week["unit"]) == ("2026-10-05", 1, 1, "kg")
    assert week["volume"] == pytest.approx(82.5 / 2.20462262185 * 8, abs=0.01)


def test_deleted_records_are_not_exported(auth_client):
    client, me, _ = auth_client()
    routine, session = training_data(me)
    client.delete(f"/api/v1/workouts/{session.id}/")
    doc = get(client).json()
    assert doc["sessions"] == [] and doc["weekly_progress"] == []


def test_csv_table(auth_client):
    client, me, _ = auth_client()
    training_data(me)
    response = get(client, type="csv")
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"].endswith('-series.csv"')
    text = response.content.decode()
    lines = text.lstrip("﻿").split("\r\n")
    assert text.startswith("﻿")
    assert lines[0] == "session_id,date,routine_name,exercise_order,exercise_id,exercise_name,set_number,weight,unit,reps,rir,rest_seconds,completed"
    assert lines[1].endswith(',2026-10-05,"Fuerza, básica",0,chest-bench-press,Press banca,1,82.5,kg,8,2,90,true')
    assert lines[2].endswith(",2,,kg,,,,false")  # not recorded ≠ 0
    measurements = get(client, type="csv", table="mediciones").content.decode()
    assert measurements == "﻿date,kind,value,unit\r\n2026-10-05,body_weight,80.25,kg\r\n"


def test_zip_has_every_table_and_a_readme(auth_client):
    client, me, _ = auth_client()
    training_data(me)
    response = get(client, type="zip")
    assert response["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert names == {"datos.json", "LEEME.txt", *(f"{t}.csv" for t in fmt.TABLES)}
    doc = json.loads(archive.read("datos.json"))
    assert archive.read("series.csv").decode() == fmt.tables(doc)["series"]
    assert "UNIDADES" in archive.read("LEEME.txt").decode()


@pytest.mark.parametrize("params", [{"type": "pdf"}, {"type": "csv", "table": "usuarios"}])
def test_invalid_requests(auth_client, params):
    client, _, _ = auth_client()
    assert get(client, **params).status_code == 400


def test_exports_are_rate_limited(auth_client, monkeypatch):
    client, _, _ = auth_client()
    cache.clear()
    from rest_framework.throttling import ScopedRateThrottle

    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {**api_settings.DEFAULT_THROTTLE_RATES, "export": "2/hour"})
    assert [get(client).status_code for _ in range(3)] == [200, 200, 429]
    cache.clear()


@pytest.mark.parametrize("value,text", [(None, ""), (82.5, "82.5"), (100.0, "100"), (0, "0"), (1 / 3, "0.33"), (2.005, "2")])
def test_number_format(value, text):
    assert fmt.number(value) == text


def test_csv_quoting():
    assert fmt.to_csv(["a", "b"], [['x,y', 'di "hola"'], [True, None]]) == '﻿a,b\r\n"x,y","di ""hola"""\r\ntrue,\r\n'
