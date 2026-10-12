import itertools

import pytest

_counter = itertools.count()


@pytest.fixture
def make_user(db, django_user_model):
    def make(username=None, email=None, password="una-clave-segura-123", **extra):
        n = next(_counter)
        return django_user_model.objects.create_user(
            username=username or f"user{n}", email=email or f"user{n}@example.com", password=password, **extra
        )

    return make
