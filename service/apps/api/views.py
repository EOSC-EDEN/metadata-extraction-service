import os
import shutil
import tempfile
import time
import uuid

from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from apps.api.jobs import JobStatus, JobStoreFull, get_job_service
from apps.api.openapi import (
    ExtractAutoSchema,
    JobResultAutoSchema,
    JobStatusAutoSchema,
    JobSubmitAutoSchema,
    LimitsAutoSchema,
    ToolsAutoSchema,
)
from apps.api.serializers import (
    ExtractionRequestSerializer,
    PayloadTooLarge,
    check_declared_length,
    serialize_extraction_result,
    serialize_job,
)
from apps.extraction.services.orchestrator import ExtractionOrchestrator
from apps.extraction.tool_config import list_tool_configs
from apps.metrics import metrics


class AsyncDisabled(NotFound):
    default_detail = "Asynchronous processing is disabled on this service."


class AsyncEnabledMixin:
    """Jobs routes answer 404 when `MES_ASYNC_ENABLED` is off, before any other work."""

    def initial(self, request, *args, **kwargs):
        if not settings.MES_ASYNC_ENABLED:
            raise AsyncDisabled()
        super().initial(request, *args, **kwargs)


class RequestIdMixin:
    def initial(self, request, *args, **kwargs):
        # Assigned up front so error responses and logs share the success path's id.
        request.mes_request_id = str(uuid.uuid4())
        super().initial(request, *args, **kwargs)


class ExtractMetadataView(RequestIdMixin, APIView):
    """POST /api/v1/extract/ — extract metadata from a single uploaded file."""

    schema = ExtractAutoSchema()

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
            with tempfile.TemporaryDirectory(dir=settings.MES_JOB_UPLOAD_DIR) as tmp_dir:
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


class JobsAtCapacity(APIException):
    status_code = 503
    default_detail = "The service cannot accept more asynchronous jobs right now. Try again later."
    default_code = "jobs_at_capacity"


class JobNotFinished(APIException):
    status_code = 409
    default_detail = "The job has not finished, so it has no result yet."
    default_code = "job_not_finished"


class JobSubmitView(AsyncEnabledMixin, RequestIdMixin, APIView):
    """POST /api/v1/jobs/ — queue an extraction; same multipart body as `/extract/`."""

    schema = JobSubmitAutoSchema()

    def post(self, request):
        check_declared_length(request.META.get("CONTENT_LENGTH"))
        serializer = ExtractionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        upload = data["file"]
        metrics.record_upload(upload.size)

        # Kept until the job finishes (the job removes it); removed here if it is never queued.
        work_dir = tempfile.mkdtemp(dir=settings.MES_JOB_UPLOAD_DIR)
        try:
            path = os.path.join(work_dir, f"upload{os.path.splitext(upload.name)[1]}")
            with open(path, "wb") as handle:
                for chunk in upload.chunks():
                    handle.write(chunk)
            record = get_job_service().submit(
                file_path=path,
                work_dir=work_dir,
                filename=upload.name,
                puid=data.get("puid") or None,
                mime_type=data.get("mime_type") or None,
                request_id=request.mes_request_id,
                size_bytes=upload.size,
            )
        except BaseException as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            if isinstance(exc, JobStoreFull):
                raise JobsAtCapacity() from exc
            raise
        return Response(
            serialize_job(record),
            status=status.HTTP_202_ACCEPTED,
            headers={"Location": f"{request.path.rstrip('/')}/{record.job_id}/"},
        )


def _get_job_or_404(job_id):
    record = get_job_service().get(job_id)
    if record is None:
        raise NotFound("No such job; it may have expired.")
    return record


class JobStatusView(AsyncEnabledMixin, RequestIdMixin, APIView):
    """GET /api/v1/jobs/{job_id}/ — the job envelope."""

    schema = JobStatusAutoSchema()

    def get(self, request, job_id):
        return Response(serialize_job(_get_job_or_404(job_id)))


class JobResultView(AsyncEnabledMixin, RequestIdMixin, APIView):
    """GET /api/v1/jobs/{job_id}/result/ — the `mes-result` document of a succeeded job."""

    schema = JobResultAutoSchema()

    def get(self, request, job_id):
        record = _get_job_or_404(job_id)
        if record.status is JobStatus.SUCCEEDED:
            return Response(record.result)
        if record.status is JobStatus.FAILED:
            raise JobNotFinished("The job failed, so it has no result: " + " ".join(record.errors))
        raise JobNotFinished()


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
        return Response(
            {
                "max_upload_bytes": settings.MES_MAX_UPLOAD_BYTES,
                "async_enabled": settings.MES_ASYNC_ENABLED,
            }
        )
