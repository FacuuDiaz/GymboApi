from io import StringIO

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_models_and_migrations_are_in_sync():
    out = StringIO()
    call_command("makemigrations", "--check", "--dry-run", stdout=out)
