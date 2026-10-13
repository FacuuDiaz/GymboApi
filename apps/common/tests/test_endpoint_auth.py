"""
Every API endpoint requires an account unless it is listed here on purpose.
Walks the URL configuration, so a new endpoint that forgets its permission
class fails this test instead of shipping open.
"""
import re
import uuid

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

pytestmark = pytest.mark.django_db

# (path regex, methods anyone may call). Everything else must answer 401 to an anonymous caller.
PUBLIC = [
    (r"^/api/v1/auth/(register|login|refresh|logout|google)/$", {"POST"}),
    (r"^/api/v1/shared-links/resolve/$", {"POST"}),
    (r"^/api/v1/community/(routines|exercises)/(<id>/)?$", {"GET"}),
    (r"^/api/v1/(schema|docs)/$", {"GET"}),
]
METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")


def api_paths():
    def walk(patterns, prefix=""):
        for p in patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, route)
            elif isinstance(p, URLPattern):
                yield route
    for route in walk(get_resolver().url_patterns):
        if not route.startswith("api/v1/"):
            continue
        # Router regexes and path converters → a concrete path with a placeholder id.
        path = re.sub(r"<[^>]+>|\(\?P<[^>]+>[^)]+\)", "<id>", route)
        path = path.replace("^", "").replace("$", "").replace("\\.", ".")
        if "(?P<format>" in route or "format" in path or "." in path.split("/")[-1]:
            continue  # DRF's ".json" suffix variants
        yield "/" + path


def is_public(path, method):
    return any(re.match(rx, path) and method in methods for rx, methods in PUBLIC)


def test_every_private_endpoint_rejects_anonymous_callers(api):
    paths = sorted(set(api_paths()))
    assert len(paths) > 30  # the walk really found the API
    open_endpoints = []
    for path in paths:
        url = path.replace("<id>", str(uuid.uuid4()))
        for method in METHODS:
            if is_public(path, method):
                continue
            status = getattr(api, method.lower())(url, {}, format="json").status_code
            # 405 = the method does not exist on that route, which is fine.
            if status not in (401, 405):
                open_endpoints.append((method, path, status))
    assert open_endpoints == []
