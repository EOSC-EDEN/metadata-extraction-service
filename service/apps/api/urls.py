from django.http import FileResponse
from django.urls import path
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView
from rest_framework.schemas import get_schema_view

from apps.api.openapi import SCHEMA_DIR
from apps.api.views import (
    BatchJobStatusView,
    BatchJobSubmitView,
    BatchExtractMetadataView,
    ExtractMetadataView,
    JobResultView,
    JobStatusView,
    JobSubmitView,
    LimitsView,
    LocalRulesView,
    ToolsView,
)

app_name = "api"


def _schema_file(filename, content_type):
    @require_GET
    def view(request):
        return FileResponse(open(SCHEMA_DIR / filename, "rb"), content_type=content_type)

    return view


urlpatterns = [
    path("extract/", ExtractMetadataView.as_view(), name="extract"),
    path("extract/batch/", BatchExtractMetadataView.as_view(), name="extract-batch"),
    path("jobs/", JobSubmitView.as_view(), name="job-submit"),
    # Before jobs/<job_id>/, or "batch" would be read as a job id.
    path("jobs/batch/", BatchJobSubmitView.as_view(), name="job-batch-submit"),
    path("job-batches/<str:batch_id>/", BatchJobStatusView.as_view(), name="job-batch-status"),
    path("jobs/<str:job_id>/", JobStatusView.as_view(), name="job-status"),
    path("jobs/<str:job_id>/result/", JobResultView.as_view(), name="job-result"),
    path("tools/", ToolsView.as_view(), name="tools"),
    path("limits/", LimitsView.as_view(), name="limits"),
    path("local-rules/", LocalRulesView.as_view(), name="local-rules"),
    path(
        "schema/",
        get_schema_view(
            title="Metadata Extraction Service",
            description="Extract technical metadata from digital files (EOSC EDEN CPP-009).",
            version="0.1.0",
            url="/api/v1/",
            patterns=[
                path("extract/", ExtractMetadataView.as_view()),
                path("extract/batch/", BatchExtractMetadataView.as_view()),
                path("jobs/", JobSubmitView.as_view()),
                path("jobs/batch/", BatchJobSubmitView.as_view()),
                path("job-batches/<str:batch_id>/", BatchJobStatusView.as_view()),
                path("jobs/<str:job_id>/", JobStatusView.as_view()),
                path("jobs/<str:job_id>/result/", JobResultView.as_view()),
                path("tools/", ToolsView.as_view()),
                path("limits/", LimitsView.as_view()),
                path("local-rules/", LocalRulesView.as_view()),
            ],
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
        "schema/job.json",
        _schema_file("mes-job.schema.json", "application/schema+json"),
        name="job-json-schema",
    ),
    path(
        "schema/job.xsd",
        _schema_file("mes-job.xsd", "application/xml"),
        name="job-xsd",
    ),
    path(
        "schema/batch.json",
        _schema_file("mes-batch.schema.json", "application/schema+json"),
        name="batch-json-schema",
    ),
    path(
        "schema/batch.xsd",
        _schema_file("mes-batch.xsd", "application/xml"),
        name="batch-xsd",
    ),
    path(
        "schema/job-batch.json",
        _schema_file("mes-job-batch.schema.json", "application/schema+json"),
        name="job-batch-json-schema",
    ),
    path(
        "schema/job-batch.xsd",
        _schema_file("mes-job-batch.xsd", "application/xml"),
        name="job-batch-xsd",
    ),
    path(
        "schema/result.xsd",
        _schema_file("mes-result.xsd", "application/xml"),
        name="result-xsd",
    ),
]
