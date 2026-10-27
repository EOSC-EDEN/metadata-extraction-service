"""Serve the static single-page UI (a replica of the FFIS UI).

The page talks to the REST API only; the one server-side substitution is the optional
footer text (MES_FOOTER_TEXT) and the documentation link (MES_DOCS_URL). Served via
plain file responses so it works unchanged under DEBUG=False without
collectstatic.
"""

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.utils.html import escape
from django.views.decorators.http import require_GET

STATIC_DIR = Path(__file__).resolve().parent / "static" / "ui"


def _serve(filename, content_type):
    return FileResponse(open(STATIC_DIR / filename, "rb"), content_type=content_type)


@require_GET
def index(request):
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    text = settings.MES_FOOTER_TEXT.strip()
    note = f'<p class="footer-note">{escape(text)}</p>' if text else ""
    docs_url = settings.MES_DOCS_URL.strip()
    docs_link = (
        f'&middot; <a href="{escape(docs_url)}">Documentation</a>'
        if docs_url.lower().startswith(("http://", "https://"))
        else ""
    )
    html = html.replace("<!--DOCS_LINK-->", docs_link).replace("<!--FOOTER_TEXT-->", note)
    return HttpResponse(html, content_type="text/html; charset=utf-8")


@require_GET
def logo(request):
    return _serve("logo-eden.svg", "image/svg+xml")
