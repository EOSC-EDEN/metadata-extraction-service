"""
Base Django settings for the web project, shared by every environment.

Environment-specific settings (DEBUG, ALLOWED_HOSTS, ...) live in
dev.py / prod.py / test.py, each of which starts with
``from .base import *``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.1/ref/settings/
"""

import os
from pathlib import Path

# service/ — three levels up from web/settings/base.py.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-gtpytwhz_$xk@d9(tdmw$w#$k*^cv+3=boz2ce+judw$*4=elb",
)


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.extraction",
    "apps.api",
    "apps.ui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "web.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/6.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Metadata Extraction Service
# Extraction tools are configured here (override per environment in
# dev/prod/test settings). Adding a new tool means adding an entry plus an
# adapter class, not changing orchestration code (MES-REQ-4-007).
#
# Per tool:
#   adapter             dotted path of the adapter class
#   kind                "subprocess" (CLI) or "http" (externally hosted, MES-REQ-1-005)
#   invocation_command  binary path for subprocess tools (optional: defaults to the
#                       adapter's own command), base URL for http tools (required)
#   extra_args          extra CLI args (subprocess tools only)
#   timeout_seconds     per-invocation timeout
#   enabled             disabled tools are never selected
#   mandatory           failure surfaces a top-level error (MES-REQ-3-006)

MES_TOOLS = {
    "exiftool": {
        "adapter": "apps.extraction.adapters.exiftool.ExifToolAdapter",
        "kind": "subprocess",
        "invocation_command": os.environ.get("MES_EXIFTOOL_COMMAND", "exiftool"),
        "extra_args": [],
        "timeout_seconds": 30,
        "enabled": True,
        "mandatory": False,
    },
    "mediainfo": {
        "adapter": "apps.extraction.adapters.mediainfo.MediaInfoAdapter",
        "kind": "subprocess",
        "invocation_command": os.environ.get("MES_MEDIAINFO_COMMAND", "mediainfo"),
        "extra_args": [],
        "timeout_seconds": 30,
        "enabled": True,
        "mandatory": False,
    },
}

# Tool run when no format mapping selects one (MES-REQ-1-001).
MES_DEFAULT_TOOL = os.environ.get("MES_DEFAULT_TOOL", "exiftool")

# Bound on concurrent tool invocations per extraction request.
MES_MAX_CONCURRENT_TOOLS = 4

# File Format Identification Service (FFIS, https://github.com/EOSC-EDEN/eosc-ffis).
# Called only when a request supplies neither a PUID nor a MIME type.
MES_FFIS_ENABLED = os.environ.get("MES_FFIS_ENABLED", "false").lower() in ("1", "true", "yes")
MES_FFIS_BASE_URL = os.environ.get("MES_FFIS_BASE_URL", "")
MES_FFIS_TIMEOUT_SECONDS = int(os.environ.get("MES_FFIS_TIMEOUT_SECONDS", "30"))
# FFIS's OpenAPI schema doesn't enumerate Identifier.scheme values; confirm
# these against a live FFIS response (compared case-insensitively).
MES_FFIS_PUID_SCHEMES = ("pronom", "puid")
MES_FFIS_MIME_SCHEMES = ("mime", "mimetype", "mime-type", "media-type")

REST_FRAMEWORK = {
    # JSON is first, so it is the default when no Accept header is sent.
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "apps.api.renderers.MESXMLRenderer",
    ],
    "EXCEPTION_HANDLER": "apps.api.exceptions.exception_handler",
}
