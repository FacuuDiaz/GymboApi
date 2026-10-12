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


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def auth_client(make_user):
    """An APIClient authenticated as a fresh user; returns (client, user, tokens)."""
    from rest_framework.test import APIClient

    from apps.accounts.tokens import issue_token_pair

    def make(user=None):
        user = user or make_user()
        tokens = issue_token_pair(user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        return client, user, tokens

    return make
