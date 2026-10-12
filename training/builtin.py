import json
import uuid
from pathlib import Path

# Fixed namespace: built-in exercise ids are uuid5(BUILTIN_NAMESPACE, slug), so
# every server and every device derives the same id for "chest-bench-press".
BUILTIN_NAMESPACE = uuid.UUID("6f3c1f1e-8a1d-4b6e-9a57-0c9d5c2b7e41")

# Generated from the app's src/constants/exerciseLibrary.ts by
# scripts/export-exercise-library.js — do not edit by hand.
BUILTIN_DATA_PATH = Path(__file__).resolve().parent / "data" / "builtin_exercises.json"

BUILTIN_FIELDS = ("name", "primary_muscle", "secondary_muscles", "equipment", "category", "modality", "description", "video_url")


def builtin_exercise_id(slug: str) -> uuid.UUID:
    return uuid.uuid5(BUILTIN_NAMESPACE, slug)


def load_builtin_exercises(exercise_model) -> tuple[int, int]:
    """
    Insert or update built-in exercises from the generated JSON. Idempotent.
    Never deletes: a built-in dropped from the app library stays so old
    sessions referencing it still resolve. Takes the model class as an
    argument so data migrations can pass their historical model.
    """
    created = updated = 0
    for row in json.loads(BUILTIN_DATA_PATH.read_text(encoding="utf-8")):
        defaults = {field: row[field] for field in BUILTIN_FIELDS}
        defaults.update(is_builtin=True, owner=None, visibility="public")
        _, was_created = exercise_model.objects.update_or_create(
            id=builtin_exercise_id(row["slug"]), slug=row["slug"], defaults=defaults
        )
        created += was_created
        updated += not was_created
    return created, updated
