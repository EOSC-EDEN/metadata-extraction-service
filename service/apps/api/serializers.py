"""Request validation and response shaping for the extract endpoint.

The response is built from the orchestrator's DTO as plain dicts so every
renderer (JSON now, XML later) serialises the same structure (MES-REQ-2-005).
Tool `raw_output` is passed through as an unmodified string (MES-REQ-3-004).
"""

from datetime import datetime, timezone

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.exceptions import APIException

from apps.api import premis
from apps.extraction.adapters.base import ToolInvocationResult
from apps.extraction.services.orchestrator import AggregatedExtractionResult

SERVICE_VERSION = "0.1.0"


class PayloadTooLarge(APIException):
    status_code = 413
    default_detail = "The uploaded file is too large."
    default_code = "payload_too_large"


def validate_upload_limit_config() -> None:
    """`MES_MAX_UPLOAD_BYTES` must be a positive integer; checked at startup."""
    value = getattr(settings, "MES_MAX_UPLOAD_BYTES", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ImproperlyConfigured(
            f"settings.MES_MAX_UPLOAD_BYTES must be an integer >= 1, got {value!r}."
        )


def validate_files_per_request_config() -> None:
    """`MES_MAX_FILES_PER_REQUEST` must be a positive integer; checked at startup."""
    value = getattr(settings, "MES_MAX_FILES_PER_REQUEST", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ImproperlyConfigured(
            f"settings.MES_MAX_FILES_PER_REQUEST must be an integer >= 1, got {value!r}."
        )


def check_declared_length(content_length) -> None:
    """Reject a request whose declared body size cannot fit the upload limit.

    Runs before the multipart body is parsed, so an oversized upload is refused without
    Django spooling it to disk. The allowance covers the non-file form fields and multipart
    framing (`DATA_UPLOAD_MAX_MEMORY_SIZE`); the exact file size is still checked by the
    serializer. A missing or malformed header is left to the serializer.
    """
    try:
        declared = int(content_length)
    except (TypeError, ValueError):
        return
    limit = settings.MES_MAX_UPLOAD_BYTES
    if declared > limit + settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
        raise PayloadTooLarge(
            f"The request body is {declared} bytes, which exceeds the maximum upload of {limit} bytes."
        )


class ExtractionRequestSerializer(serializers.Serializer):
    file = serializers.FileField()
    puid = serializers.CharField(required=False, allow_blank=True, max_length=64)
    mime_type = serializers.CharField(required=False, allow_blank=True, max_length=255)
    object_identifier = serializers.CharField(required=False, allow_blank=True, max_length=255)
    object_identifier_type = serializers.CharField(required=False, allow_blank=True, max_length=64)

    def validate(self, attrs):
        # Blank counts as not supplied (multipart forms send empty fields), as for puid and mime_type.
        given = [bool(attrs.get(name)) for name in ("object_identifier", "object_identifier_type")]
        if any(given) and not all(given):
            raise serializers.ValidationError(
                "object_identifier and object_identifier_type must be given together."
            )
        return attrs

    def validate_file(self, value):
        if value.size == 0:
            raise serializers.ValidationError("The uploaded file is empty.")
        limit = settings.MES_MAX_UPLOAD_BYTES
        if value.size > limit:
            raise PayloadTooLarge(
                f"The uploaded file is {value.size} bytes, which exceeds the maximum of {limit} bytes."
            )
        return value


class BatchExtractionRequestSerializer(serializers.Serializer):
    """Several files in one request: the `files` field repeated once per file.

    `puid` and `mime_type` apply to every file. There is no object identifier, so each file's
    PREMIS event identifies its object by filename. An empty file makes the whole request invalid
    (as on the single-file routes), so it is refused before anything is extracted or queued.
    """

    files = serializers.ListField(child=serializers.FileField(allow_empty_file=True))
    puid = serializers.CharField(required=False, allow_blank=True, max_length=64)
    mime_type = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        # Blank counts as not supplied (multipart forms send empty fields), as on the single route.
        for name in ("object_identifier", "object_identifier_type"):
            if self.initial_data.get(name):
                raise serializers.ValidationError(
                    {name: "Not accepted on the batch route; each file is identified by its filename."}
                )
        return attrs

    def validate_files(self, value):
        if not value:
            raise serializers.ValidationError("At least one file is required.")
        empty = [upload.name for upload in value if upload.size == 0]
        if empty:
            raise serializers.ValidationError(f"The uploaded file is empty: {', '.join(empty)}.")
        max_files = settings.MES_MAX_FILES_PER_REQUEST
        if len(value) > max_files:
            raise serializers.ValidationError(
                f"{len(value)} files were uploaded, which exceeds the maximum of {max_files} per request."
            )
        total = sum(upload.size for upload in value)
        limit = settings.MES_MAX_UPLOAD_BYTES
        if total > limit:
            raise PayloadTooLarge(
                f"The uploaded files total {total} bytes, which exceeds the maximum of {limit} bytes."
            )
        return value


def serialize_job_batch(batch, jobs) -> dict:
    """The batch envelope of the `/api/v1/jobs/batch/` and `/api/v1/job-batches/` endpoints."""
    from apps.api.jobs import aggregate_batch_status

    return {
        "batch_id": batch.batch_id,
        "created_at": batch.created_at.isoformat(),
        "status": aggregate_batch_status(job.status for job in jobs),
        "jobs": [
            {
                "index": entry["index"],
                "filename": entry["filename"],
                "job_id": entry["job_id"],
                "status": job.status.value,
            }
            for entry, job in zip(batch.jobs, jobs)
        ],
    }


def serialize_job(record) -> dict:
    """The small job envelope returned by the `/api/v1/jobs/` endpoints."""
    return {
        "job_id": record.job_id,
        "status": record.status.value,
        "created_at": record.created_at.isoformat(),
        "errors": list(record.errors),
    }


def _iso(value):
    return value.isoformat() if value else None


def _error_fields(error_details) -> dict:
    """`errors`, `error_categories` and `error_details`, all derived from one list so they cannot drift."""
    return {
        "errors": [d.message for d in error_details],
        "error_categories": [d.category for d in error_details],
        "error_details": [{"message": d.message, "category": d.category} for d in error_details],
    }


def _serialize_tool_result(result: ToolInvocationResult) -> dict:
    return {
        "tool": result.tool_name,
        "version": result.tool_version,
        "status": result.status.value,
        "started_at": _iso(result.started_at),
        "finished_at": _iso(result.finished_at),
        "command": list(result.command) if result.command else None,
        "raw_output": result.raw_output,
        "raw_output_format": result.raw_output_format,
        **_error_fields(result.error_details),
        "warnings": list(result.warnings),
    }


def serialize_extraction_result(
    result: AggregatedExtractionResult,
    request_id: str,
    filename: str,
    object_identifier: str | None = None,
    object_identifier_type: str | None = None,
) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()  # computed once: provenance and event share it
    return {
        "request_id": request_id,
        "filename": filename,
        "tool_resolution": {
            "puid": result.puid,
            "mime_type": result.mime_type,
            "resolved_tools": list(result.resolved_tools),
            "selection_method": result.selection_method,
            "identification_url": result.identification_url,
            "matched_rules": list(result.matched_rules),
        },
        "results": [_serialize_tool_result(r) for r in result.results],
        "provenance": {
            "timestamp": timestamp,
            "service_version": SERVICE_VERSION,
            "tool_versions": {r.tool_name: r.tool_version for r in result.results},
            "event": premis.build_event(
                result,
                request_id=request_id,
                timestamp=timestamp,
                filename=filename,
                object_identifier=object_identifier,
                object_identifier_type=object_identifier_type,
            ),
            "agents": premis.build_agents(result, service_version=SERVICE_VERSION),
            "matched_rules": list(result.matched_rules),
            "ffis": result.ffis_provenance,
        },
        **_error_fields(result.error_details),
        "warnings": list(result.warnings),
    }
