import logging
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
from django.urls import reverse

from apps.api.jobs import JobStatus, JobStoreFull, get_job_service
from apps.api.openapi import (
    BatchExtractAutoSchema,
    BatchJobStatusAutoSchema,
    BatchJobSubmitAutoSchema,
    ExtractAutoSchema,
    JobResultAutoSchema,
    JobStatusAutoSchema,
    JobSubmitAutoSchema,
    LimitsAutoSchema,
    LocalRulesAutoSchema,
    ToolsAutoSchema,
)
from apps.api.serializers import (
    BatchExtractionRequestSerializer,
    ExtractionRequestSerializer,
    PayloadTooLarge,
    check_declared_length,
    serialize_extraction_result,
    serialize_job,
    serialize_job_batch,
)
from apps.api.uploads import save_upload
from apps.extraction.exceptions import ToolNotConfigured
from apps.extraction.services.orchestrator import ExtractionOrchestrator
from apps.extraction.tool_config import list_tool_configs
from apps.extraction.tool_rules import load_tool_rules
from apps.metrics import metrics


logger = logging.getLogger(__name__)


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

            with tempfile.TemporaryDirectory(dir=settings.MES_JOB_UPLOAD_DIR) as tmp_dir:
                path = save_upload(upload, tmp_dir)
                result = ExtractionOrchestrator().extract(
                    path,
                    puid=data.get("puid") or None,
                    mime_type=data.get("mime_type") or None,
                    request_id=request.mes_request_id,
                    filename=upload.name,
                )

            metrics.record_tool_results(result.results)
            outcome = metrics.classify_outcome(result)
            return Response(
                serialize_extraction_result(
                    result,
                    request.mes_request_id,
                    upload.name,
                    object_identifier=data.get("object_identifier") or None,
                    object_identifier_type=data.get("object_identifier_type") or None,
                )
            )
        finally:
            metrics.record_request(outcome, time.perf_counter() - started)


class BatchExtractMetadataView(RequestIdMixin, APIView):
    """POST /api/v1/extract/batch/ — extract metadata from several uploaded files, one after another.

    Every file goes through the same extraction as `/extract/`, so its entry equals what that route
    returns for it. A file that cannot be processed gets an `errors` entry in its place; the others
    are unaffected, so the response is `200` whenever the request itself was acceptable.
    """

    schema = BatchExtractAutoSchema()

    def post(self, request):
        try:
            check_declared_length(request.META.get("CONTENT_LENGTH"))
            serializer = BatchExtractionRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
        except (ValidationError, PayloadTooLarge):
            metrics.record_batch("rejected")
            raise
        data = serializer.validated_data
        uploads = data["files"]

        entries = []
        with tempfile.TemporaryDirectory(dir=settings.MES_JOB_UPLOAD_DIR) as work_dir:
            for index, upload in enumerate(uploads):
                entries.append(
                    self._extract_one(
                        request,
                        work_dir,
                        index,
                        upload,
                        puid=data.get("puid") or None,
                        mime_type=data.get("mime_type") or None,
                    )
                )
        metrics.record_batch(metrics.classify_batch(entries), len(entries))
        return Response({"results": entries})

    @staticmethod
    def _extract_one(request, work_dir, index, upload, *, puid, mime_type) -> dict:
        started = time.perf_counter()
        outcome = "error"
        entry = {"index": index, "filename": upload.name}
        request_id = f"{request.mes_request_id}-{index}"
        extra = {"request_id": request_id}
        try:
            metrics.record_upload(upload.size)
            # One directory per file, so equal filenames can never share a path.
            file_dir = os.path.join(work_dir, str(index))
            os.mkdir(file_dir)
            path = save_upload(upload, file_dir)
            result = ExtractionOrchestrator().extract(
                path,
                puid=puid,
                mime_type=mime_type,
                request_id=request_id,
                filename=upload.name,
            )
            metrics.record_tool_results(result.results)
            outcome = metrics.classify_outcome(result)
            entry["result"] = serialize_extraction_result(result, request_id, upload.name)
        except ToolNotConfigured as exc:
            logger.error("tool not configured: %s", exc, extra=extra)
            entry["errors"] = [str(exc)]
        except Exception:
            logger.exception("Unhandled error while extracting a file of a batch", extra=extra)
            entry["errors"] = ["An unexpected error occurred while processing the file."]
        finally:
            metrics.record_request(outcome, time.perf_counter() - started)
        return entry


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
            path = save_upload(upload, work_dir)
            record = get_job_service().submit(
                file_path=path,
                work_dir=work_dir,
                filename=upload.name,
                puid=data.get("puid") or None,
                mime_type=data.get("mime_type") or None,
                object_identifier=data.get("object_identifier") or None,
                object_identifier_type=data.get("object_identifier_type") or None,
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


class BatchJobSubmitView(AsyncEnabledMixin, RequestIdMixin, APIView):
    """POST /api/v1/jobs/batch/ — queue one ordinary job per uploaded file, all or none."""

    schema = BatchJobSubmitAutoSchema()

    def post(self, request):
        try:
            check_declared_length(request.META.get("CONTENT_LENGTH"))
            serializer = BatchExtractionRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
        except (ValidationError, PayloadTooLarge):
            metrics.record_batch("rejected")
            raise
        data = serializer.validated_data

        # One work dir per file: each job owns its upload and removes it when it finishes.
        work_dirs = []
        try:
            items = []
            for upload in data["files"]:
                work_dir = tempfile.mkdtemp(dir=settings.MES_JOB_UPLOAD_DIR)
                work_dirs.append(work_dir)
                items.append(
                    {
                        "file_path": save_upload(upload, work_dir),
                        "work_dir": work_dir,
                        "filename": upload.name,
                        "size_bytes": upload.size,
                    }
                )
            batch, records = get_job_service().submit_batch(
                items,
                puid=data.get("puid") or None,
                mime_type=data.get("mime_type") or None,
                request_id=request.mes_request_id,
            )
        except BaseException as exc:
            # Nothing was queued (the service raises before it takes ownership of any upload).
            for work_dir in work_dirs:
                shutil.rmtree(work_dir, ignore_errors=True)
            metrics.record_batch("rejected")
            if isinstance(exc, JobStoreFull):
                raise JobsAtCapacity() from exc
            raise
        for upload in data["files"]:
            metrics.record_upload(upload.size)
        metrics.record_batch("accepted", len(records))
        return Response(
            serialize_job_batch(batch, records),
            status=status.HTTP_202_ACCEPTED,
            headers={"Location": reverse("api:job-batch-status", args=[batch.batch_id])},
        )


class BatchJobStatusView(AsyncEnabledMixin, RequestIdMixin, APIView):
    """GET /api/v1/job-batches/{batch_id}/ — the batch with the current status of each file's job."""

    schema = BatchJobStatusAutoSchema()

    def get(self, request, batch_id):
        found = get_job_service().get_batch(batch_id)
        if found is None:
            raise NotFound("No such batch; it may have expired.")
        return Response(serialize_job_batch(*found))


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


class LocalRulesView(APIView):
    """GET /api/v1/local-rules/ — the local format rules that link PUIDs/extensions to tools."""

    schema = LocalRulesAutoSchema()

    def get(self, request):
        rules = [
            {
                "name": rule.name,
                "puids": list(rule.puids),
                "extensions": list(rule.extensions),
                "tools": list(rule.tools),
                "match": rule.match,
            }
            for rule in load_tool_rules()
        ]
        return Response({"rules": rules})


class LimitsView(APIView):
    """GET /api/v1/limits/ — deployment limits, read by the UI."""

    schema = LimitsAutoSchema()

    def get(self, request):
        return Response(
            {
                "max_upload_bytes": settings.MES_MAX_UPLOAD_BYTES,
                "max_files_per_request": settings.MES_MAX_FILES_PER_REQUEST,
                "async_enabled": settings.MES_ASYNC_ENABLED,
            }
        )
