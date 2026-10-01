"""Production settings. Requires DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS."""

import os

from .base import *  # noqa: F401,F403

DEBUG = False

ALLOWED_HOSTS = [
    host for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if host
]
