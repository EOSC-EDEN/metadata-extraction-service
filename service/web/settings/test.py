"""Settings for running the automated test suite: fast and deterministic."""

import os
import tempfile

from .base import *  # noqa: F401,F403

DEBUG = False

ALLOWED_HOSTS = ["testserver"]

# Skip the expensive default hasher; test passwords don't need real security.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# The container default (/var/lib/mes/uploads) is not writable on a developer machine.
MES_JOB_UPLOAD_DIR = os.environ.get(
    "MES_JOB_UPLOAD_DIR", os.path.join(tempfile.gettempdir(), "mes-uploads")
)
