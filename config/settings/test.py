"""
Automated tests. pytest-django creates a separate `test_<POSTGRES_DB>`
database and destroys it afterwards, so tests never touch development data.
"""
from .base import *  # noqa: F401,F403
from .base import REST_FRAMEWORK

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

# Generous defaults so ordinary tests are never throttled; throttle tests
# override these explicitly.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {key: "10000/min" for key in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]},
}
GOOGLE_OAUTH_CLIENT_IDS = ["test-android.apps.googleusercontent.com", "test-ios.apps.googleusercontent.com"]
