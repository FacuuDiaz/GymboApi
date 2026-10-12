"""
Validation for `RoutineVersion.content`, the immutable snapshot of a plan.

Shape (snake_case mirror of the app's Routine.weeks):

    {"weeks": [{"id", "index", "days": [{"id", "weekday", "name", "is_rest_day",
        "scheduled_time"?, "exercises": [{"id", "exercise_id", "exercise_name"?,
        "order", "sets", "reps_min", "reps_max", "target_weight"?}]}]}]}

`exercise_name` is a snapshot so the plan stays readable even if the
referenced exercise is later edited or deleted.
"""
import re

from django.core.exceptions import ValidationError

MAX_WEEKS = 52
MAX_EXERCISES_PER_DAY = 50
MAX_NAME_LENGTH = 80
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

WEEK_KEYS = {"id", "index", "days"}
DAY_KEYS = {"id", "weekday", "name", "is_rest_day", "scheduled_time", "exercises"}
EXERCISE_KEYS = {"id", "exercise_id", "exercise_name", "order", "sets", "reps_min", "reps_max", "target_weight"}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _non_empty_str(value, max_length=64) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= max_length


def validate_plan_content(content, planning_mode: str) -> None:
    errors: list[str] = []

    def err(path: str, message: str) -> None:
        errors.append(f"{path}: {message}")

    if not isinstance(content, dict) or set(content) != {"weeks"}:
        raise ValidationError('content must be an object with exactly one key, "weeks".')
    weeks = content["weeks"]
    if not isinstance(weeks, list) or not weeks:
        raise ValidationError("weeks must be a non-empty list.")
    if len(weeks) > MAX_WEEKS:
        raise ValidationError(f"weeks cannot exceed {MAX_WEEKS}.")
    if planning_mode == "repeat" and len(weeks) != 1:
        raise ValidationError('A "repeat" routine must have exactly one week.')

    for wi, week in enumerate(weeks):
        wp = f"weeks[{wi}]"
        if not isinstance(week, dict):
            err(wp, "must be an object")
            continue
        if extra := set(week) - WEEK_KEYS:
            err(wp, f"unknown keys {sorted(extra)}")
        if not _non_empty_str(week.get("id")):
            err(f"{wp}.id", "required")
        if not _is_int(week.get("index")) or week["index"] < 0:
            err(f"{wp}.index", "must be a non-negative integer")
        days = week.get("days")
        if not isinstance(days, list):
            err(f"{wp}.days", "must be a list")
            continue
        seen_weekdays = set()
        for di, day in enumerate(days):
            dp = f"{wp}.days[{di}]"
            if not isinstance(day, dict):
                err(dp, "must be an object")
                continue
            if extra := set(day) - DAY_KEYS:
                err(dp, f"unknown keys {sorted(extra)}")
            if not _non_empty_str(day.get("id")):
                err(f"{dp}.id", "required")
            weekday = day.get("weekday")
            if not _is_int(weekday) or not 0 <= weekday <= 6:
                err(f"{dp}.weekday", "must be an integer 0 (Monday) to 6 (Sunday)")
            elif weekday in seen_weekdays:
                err(f"{dp}.weekday", "duplicated within the week")
            else:
                seen_weekdays.add(weekday)
            if not isinstance(day.get("name"), str) or len(day["name"]) > MAX_NAME_LENGTH:
                err(f"{dp}.name", f"must be a string of at most {MAX_NAME_LENGTH} characters")
            if not isinstance(day.get("is_rest_day"), bool):
                err(f"{dp}.is_rest_day", "must be a boolean")
            scheduled_time = day.get("scheduled_time")
            if scheduled_time is not None and (not isinstance(scheduled_time, str) or not TIME_RE.match(scheduled_time)):
                err(f"{dp}.scheduled_time", 'must be "HH:mm"')
            exercises = day.get("exercises")
            if not isinstance(exercises, list) or len(exercises) > MAX_EXERCISES_PER_DAY:
                err(f"{dp}.exercises", f"must be a list of at most {MAX_EXERCISES_PER_DAY} items")
                continue
            for ei, ex in enumerate(exercises):
                ep = f"{dp}.exercises[{ei}]"
                if not isinstance(ex, dict):
                    err(ep, "must be an object")
                    continue
                if extra := set(ex) - EXERCISE_KEYS:
                    err(ep, f"unknown keys {sorted(extra)}")
                if not _non_empty_str(ex.get("id")):
                    err(f"{ep}.id", "required")
                if not _non_empty_str(ex.get("exercise_id")):
                    err(f"{ep}.exercise_id", "required")
                name = ex.get("exercise_name")
                if name is not None and (not isinstance(name, str) or len(name) > MAX_NAME_LENGTH):
                    err(f"{ep}.exercise_name", f"must be a string of at most {MAX_NAME_LENGTH} characters")
                if not _is_int(ex.get("order")) or ex["order"] < 0:
                    err(f"{ep}.order", "must be a non-negative integer")
                if not _is_int(ex.get("sets")) or not 1 <= ex["sets"] <= 20:
                    err(f"{ep}.sets", "must be an integer between 1 and 20")
                reps_min, reps_max = ex.get("reps_min"), ex.get("reps_max")
                if not _is_int(reps_min) or not 1 <= reps_min <= 100:
                    err(f"{ep}.reps_min", "must be an integer between 1 and 100")
                elif not _is_int(reps_max) or not reps_min <= reps_max <= 100:
                    err(f"{ep}.reps_max", "must be an integer between reps_min and 100")
                target = ex.get("target_weight")
                if target is not None and (not _is_number(target) or not 0 <= target <= 2000):
                    err(f"{ep}.target_weight", "must be a number between 0 and 2000, or null")

    if errors:
        raise ValidationError(errors)
