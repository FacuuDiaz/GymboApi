from django.db import migrations

from training.builtin import load_builtin_exercises


def forwards(apps, schema_editor):
    load_builtin_exercises(apps.get_model("training", "Exercise"))


class Migration(migrations.Migration):
    dependencies = [("training", "0001_initial")]

    # Reverse is a no-op on purpose: removing reference data could orphan history.
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
