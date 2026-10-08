"""Serve the static single-page UI (a replica of the FFIS UI).

No server-side templating: the page talks to the REST API only. Served via
plain file responses so it works unchanged under DEBUG=False without
collectstatic.
"""

from pathlib import Path

from django.http import FileResponse
from django.views.decorators.http import require_GET

STATIC_DIR = Path(__file__).resolve().parent / "static" / "ui"


def _serve(filename, content_type):
    return FileResponse(open(STATIC_DIR / filename, "rb"), content_type=content_type)


@require_GET
def index(request):
    return _serve("index.html", "text/html; charset=utf-8")


@require_GET
def logo(request):
    return _serve("logo-eden.svg", "image/svg+xml")
