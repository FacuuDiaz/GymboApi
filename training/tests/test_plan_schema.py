import copy

import pytest
from django.core.exceptions import ValidationError

from training.plan_schema import validate_plan_content
from training.tests.factories import plan


def test_valid_plan_passes():
    validate_plan_content(plan(), "repeat")
    validate_plan_content(plan(weeks=4), "individual")


def test_repeat_routine_needs_exactly_one_week():
    with pytest.raises(ValidationError):
        validate_plan_content(plan(weeks=2), "repeat")


@pytest.mark.parametrize("content", [None, [], {}, {"weeks": []}, {"weeks": [], "extra": 1}])
def test_malformed_root_is_rejected(content):
    with pytest.raises(ValidationError):
        validate_plan_content(content, "repeat")


def _mutate(path_fn):
    content = copy.deepcopy(plan())
    path_fn(content)
    return content


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["weeks"][0]["days"][0].update(weekday=7),
        lambda c: c["weeks"][0]["days"][1].update(weekday=0),  # duplicated weekday
        lambda c: c["weeks"][0]["days"][0].update(scheduled_time="25:00"),
        lambda c: c["weeks"][0]["days"][0].update(is_rest_day="no"),
        lambda c: c["weeks"][0]["days"][0].update(unexpected=True),
        lambda c: c["weeks"][0]["days"][0]["exercises"][0].update(sets=0),
        lambda c: c["weeks"][0]["days"][0]["exercises"][0].update(sets=True),  # bool is not an int
        lambda c: c["weeks"][0]["days"][0]["exercises"][0].update(reps_min=12, reps_max=8),
        lambda c: c["weeks"][0]["days"][0]["exercises"][0].update(target_weight=-5),
        lambda c: c["weeks"][0]["days"][0]["exercises"][0].update(exercise_id=""),
        lambda c: c["weeks"][0]["days"][0]["exercises"][0].pop("id"),
    ],
)
def test_invalid_details_are_rejected(mutation):
    with pytest.raises(ValidationError):
        validate_plan_content(_mutate(mutation), "repeat")


def test_null_target_weight_is_allowed():
    content = _mutate(lambda c: c["weeks"][0]["days"][0]["exercises"][0].update(target_weight=None))
    validate_plan_content(content, "repeat")
