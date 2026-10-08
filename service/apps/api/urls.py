from django.http import FileResponse
from django.urls import path
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView
from rest_framework.schemas import get_schema_view

from apps.api.openapi import SCHEMA_DIR
from apps.api.views import ExtractMetadataView

app_name = "api"


def _schema_file(filename, content_type):
    @require_GET
    def view(request):
        return FileResponse(open(SCHEMA_DIR / filename, "rb"), content_type=content_type)

    return view


urlpatterns = [
    path("extract/", ExtractMetadataView.as_view(), name="extract"),
    path(
        "schema/",
        get_schema_view(
            title="Metadata Extraction Service",
            description="Extract technical metadata from a single file (EOSC EDEN CPP-009).",
            version="0.1.0",
            url="/api/v1/",
            patterns=[path("extract/", ExtractMetadataView.as_view())],
        ),
        name="openapi-schema",
    ),
    path(
        "docs/",
        TemplateView.as_view(template_name="api/swagger-ui.html"),
        name="swagger-ui",
    ),
    path(
        "schema/result.json",
        _schema_file("mes-result.schema.json", "application/schema+json"),
        name="result-json-schema",
    ),
    path(
        "schema/result.xsd",
        _schema_file("mes-result.xsd", "application/xml"),
        name="result-xsd",
    ),
]
