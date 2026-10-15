import os
import tempfile
import time
import uuid

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from apps.api.openapi import ExtractAutoSchema, LimitsAutoSchema, ToolsAutoSchema
from apps.api.serializers import (
    ExtractionRequestSerializer,
    PayloadTooLarge,
    check_declared_length,
    serialize_extraction_result,
)
from apps.extraction.services.orchestrator import ExtractionOrchestrator
from apps.extraction.tool_config import list_tool_configs
from apps.metrics import metrics


class ExtractMetadataView(APIView):
    """POST /api/v1/extract/ — extract metadata from a single uploaded file."""

    schema = ExtractAutoSchema()

    def initial(self, request, *args, **kwargs):
        # Assigned up front so error responses and logs share the success path's id.
        request.mes_request_id = str(uuid.uuid4())
        super().initial(request, *args, **kwargs)

    def post(self, request):
        started = time.perf_counter()
        outcome = "error"
        try:
            try:
                check_declared_length(request.META.get("CONTENT_LENGTH"))
                serializer = ExtractionRequestSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
            except (ValidationError, PayloadTooLarge):
                outcome = "rejected"
                raise
            data = serializer.validated_data
            upload = data["file"]
            metrics.record_upload(upload.size)

            # Keep the original suffix: some tools rely on the extension.
            suffix = os.path.splitext(upload.name)[1]
            with tempfile.TemporaryDirectory() as tmp_dir:
                path = os.path.join(tmp_dir, f"upload{suffix}")
                with open(path, "wb") as handle:
                    for chunk in upload.chunks():
                        handle.write(chunk)
                result = ExtractionOrchestrator().extract(
                    path,
                    puid=data.get("puid") or None,
                    mime_type=data.get("mime_type") or None,
                    request_id=request.mes_request_id,
                )

            metrics.record_tool_results(result.results)
            outcome = metrics.classify_outcome(result)
            return Response(
                serialize_extraction_result(result, request.mes_request_id, upload.name)
            )
        finally:
            metrics.record_request(outcome, time.perf_counter() - started)


class ToolsView(APIView):
    """GET /api/v1/tools/ — the tools configured for metadata extraction.

    Deliberately omits invocation commands, arguments and URLs so deployment
    details are not exposed.
    """

    schema = ToolsAutoSchema()

    def get(self, request):
        tools = [
            {
                "name": tool.name,
                "kind": tool.kind,
                "enabled": tool.enabled,
                "mandatory": tool.mandatory,
                "default": tool.name == settings.MES_DEFAULT_TOOL,
                "timeout_seconds": tool.timeout_seconds,
            }
            for tool in list_tool_configs()
        ]
        return Response({"tools": tools})


class LimitsView(APIView):
    """GET /api/v1/limits/ — deployment limits, read by the UI."""

    schema = LimitsAutoSchema()

    def get(self, request):
        return Response({"max_upload_bytes": settings.MES_MAX_UPLOAD_BYTES})
