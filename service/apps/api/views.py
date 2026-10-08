import os
import tempfile
import uuid

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.openapi import ExtractAutoSchema
from apps.api.serializers import ExtractionRequestSerializer, serialize_extraction_result
from apps.extraction.services.orchestrator import ExtractionOrchestrator


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
