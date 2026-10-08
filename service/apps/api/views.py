import os
import tempfile
import uuid

from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from apps.api.openapi import ExtractAutoSchema, ToolsAutoSchema
from apps.api.serializers import ExtractionRequestSerializer, serialize_extraction_result
from apps.extraction.services.orchestrator import ExtractionOrchestrator
from apps.extraction.tool_config import list_tool_configs


class ExtractMetadataView(APIView):
    """POST /api/v1/extract/ — extract metadata from a single uploaded file."""

    schema = ExtractAutoSchema()

    def post(self, request):
        serializer = ExtractionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        upload = data["file"]

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
            )

        return Response(serialize_extraction_result(result, str(uuid.uuid4()), upload.name))


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
