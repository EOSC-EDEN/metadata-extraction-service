"""Local development settings."""

import os
import tempfile

from .base import *  # noqa: F401,F403

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# The container default (/var/lib/mes/uploads) is not writable on a developer machine.
MES_JOB_UPLOAD_DIR = os.environ.get(
    "MES_JOB_UPLOAD_DIR", os.path.join(tempfile.gettempdir(), "mes-uploads")
)
