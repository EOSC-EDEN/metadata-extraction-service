"""Production settings. Requires DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS."""

import os

from .base import *  # noqa: F401,F403

DEBUG = False

if "DJANGO_SECRET_KEY" not in os.environ:
    raise RuntimeError("DJANGO_SECRET_KEY must be set when using web.settings.prod.")

ALLOWED_HOSTS = [
    host for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if host
]
