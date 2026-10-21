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
    "apps.metrics",
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

def _env_flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


# Adds --skip-wellformed-check to File Scraper: metadata only, much lighter.
MES_FILE_SCRAPER_SKIP_WELLFORMED = _env_flag("MES_FILE_SCRAPER_SKIP_WELLFORMED")

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
    # Opt-in tools: disabled unless MES_<TOOL>_ENABLED is set, since they are not
    # installed in the default image (see the Dockerfile INSTALL_* build args).
    "ffprobe": {
        "adapter": "apps.extraction.adapters.ffprobe.FFprobeAdapter",
        "kind": "subprocess",
        "invocation_command": os.environ.get("MES_FFPROBE_COMMAND", "ffprobe"),
        "extra_args": [],
        "timeout_seconds": 30,
        "enabled": _env_flag("MES_FFPROBE_ENABLED"),
        "mandatory": False,
    },
    "imagemagick": {
        "adapter": "apps.extraction.adapters.imagemagick.ImageMagickAdapter",
        "kind": "subprocess",
        "invocation_command": os.environ.get("MES_IMAGEMAGICK_COMMAND", "identify"),
        "extra_args": [],
        "timeout_seconds": 30,
        "enabled": _env_flag("MES_IMAGEMAGICK_ENABLED"),
        "mandatory": False,
    },
    "metaflac": {
        "adapter": "apps.extraction.adapters.metaflac.MetaflacAdapter",
        "kind": "subprocess",
        "invocation_command": os.environ.get("MES_METAFLAC_COMMAND", "metaflac"),
        "extra_args": [],
        "timeout_seconds": 30,
        "enabled": _env_flag("MES_METAFLAC_ENABLED"),
        "mandatory": False,
    },
    "file_scraper": {
        "adapter": "apps.extraction.adapters.file_scraper.FileScraperAdapter",
        "kind": "subprocess",
        "invocation_command": os.environ.get("MES_FILE_SCRAPER_COMMAND", "scraper"),
        "extra_args": [],
        # Slow: JHove, veraPDF and LibreOffice can take a long time on large files. The
        # gunicorn worker timeout (120 s) still applies to synchronous requests.
        "timeout_seconds": 120,
        "enabled": _env_flag("MES_FILE_SCRAPER_ENABLED"),
        "mandatory": False,
    },
    "tika": {
        "adapter": "apps.extraction.adapters.tika.TikaAdapter",
        "kind": "http",
        # Base URL of a running Tika server (https://tika.apache.org/).
        "invocation_command": os.environ.get("MES_TIKA_URL", "http://localhost:9998"),
        "timeout_seconds": 60,
        "enabled": _env_flag("MES_TIKA_ENABLED"),
        "mandatory": False,
    },
}

# Tool run when no format mapping selects one (MES-REQ-1-001).
MES_DEFAULT_TOOL = os.environ.get("MES_DEFAULT_TOOL", "exiftool")

# YAML file linking PUIDs and/or file extensions to extraction tools (see config/tool_rules.yaml).
# A relative path is resolved against the project directory; a missing file means no rules.
MES_TOOL_RULES_FILE = os.environ.get("MES_TOOL_RULES_FILE", "config/tool_rules.yaml")

# Bound on concurrent tool invocations within ONE extraction request (a positive
# integer, validated at startup). Worst-case simultaneous tool processes are
# gunicorn workers x this value, so size it against the container's CPU/memory,
# not against the number of configured tools.
MES_MAX_CONCURRENT_TOOLS = int(os.environ.get("MES_MAX_CONCURRENT_TOOLS", "4"))

# Largest accepted upload, in bytes (a positive integer, validated at startup).
# Larger files are rejected with HTTP 413 before any tool runs.
MES_MAX_UPLOAD_BYTES = int(os.environ.get("MES_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))

# Most files accepted by one batch request (POST /api/v1/extract/batch/), a positive integer
# validated at startup. MES_MAX_UPLOAD_BYTES applies to the whole batch body, not per file.
MES_MAX_FILES_PER_REQUEST = int(os.environ.get("MES_MAX_FILES_PER_REQUEST", "10"))

# Asynchronous extraction (POST /api/v1/jobs/) can be switched off: the jobs endpoints then
# answer 404 and the UI hides its "Process asynchronously" option.
MES_ASYNC_ENABLED = _env_flag("MES_ASYNC_ENABLED", "true")

# The three settings below are positive integers, validated at startup.
# Background worker threads, the most jobs held at once (new ones get HTTP 503), and how long
# a job's data stays available (from its finish, or from its creation if it never finishes).
MES_ASYNC_MAX_WORKERS = int(os.environ.get("MES_ASYNC_MAX_WORKERS", "2"))
MES_ASYNC_MAX_JOBS = int(os.environ.get("MES_ASYNC_MAX_JOBS", "100"))
MES_JOB_TTL_SECONDS = int(os.environ.get("MES_JOB_TTL_SECONDS", "86400"))

# Where job state lives: "memory" (the default; one gunicorn worker process only, jobs are lost on
# restart) or "redis" (shared by all workers, survives restarts; needs MES_REDIS_URL).
MES_JOB_STORE = os.environ.get("MES_JOB_STORE", "memory")
MES_REDIS_URL = os.environ.get("MES_REDIS_URL", "")

# Directory for uploads while they are being processed (job uploads, and the temporary files of
# the synchronous endpoint). Mount a volume here in containers; created at startup if missing.
MES_JOB_UPLOAD_DIR = os.environ.get("MES_JOB_UPLOAD_DIR", "/var/lib/mes/uploads")

# Uploads above this size are spooled to disk instead of held in memory, and
# non-file form fields are capped well below the file limit.
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024

# File Format Identification Service (FFIS, https://github.com/EOSC-EDEN/eosc-ffis).
# Called only when a request supplies neither a PUID nor a MIME type.
MES_FFIS_ENABLED = os.environ.get("MES_FFIS_ENABLED", "false").lower() in ("1", "true", "yes")
MES_FFIS_BASE_URL = os.environ.get("MES_FFIS_BASE_URL", "")
MES_FFIS_TIMEOUT_SECONDS = int(os.environ.get("MES_FFIS_TIMEOUT_SECONDS", "30"))
# FFIS's OpenAPI schema doesn't enumerate Identifier.scheme values; confirm
# these against a live FFIS response (compared case-insensitively).
MES_FFIS_PUID_SCHEMES = ("pronom", "puid")
MES_FFIS_MIME_SCHEMES = ("mime", "mimetype", "mime-type", "media-type")

# Errors and warnings gathered during extraction are logged with the request's
# request_id (MES-REQ-5-001) as well as returned to the caller.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "mes": {
            "format": "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s",
            "defaults": {"request_id": "-"},
        },
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "mes"}},
    "loggers": {"apps": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}

REST_FRAMEWORK = {
    # JSON is first, so it is the default when no Accept header is sent.
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "apps.api.renderers.MESXMLRenderer",
    ],
    "EXCEPTION_HANDLER": "apps.api.exceptions.exception_handler",
}
