"""Build-time only: lets `collectstatic` run inside `docker build` without secrets or a database."""
from .base import *  # noqa: F401,F403
from .production_storages import STORAGES  # noqa: F401
