from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    # Idempotent: skips the table if it already exists.
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    """Creates the shared cache table (rate limiting) as part of `migrate`, so no extra manual step is needed."""

    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]
