"""Settings for running the automated test suite: fast and deterministic."""

from .base import *  # noqa: F401,F403

DEBUG = False

ALLOWED_HOSTS = ["testserver"]

# Skip the expensive default hasher; test passwords don't need real security.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
