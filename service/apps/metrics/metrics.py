"""Prometheus metrics for the extract endpoint.

Labels are deliberately low-cardinality: never add filenames, request ids or user identifiers.
Helpers are the only API the rest of the code uses, so the instrumentation stays out of the
extraction wrapper.
"""

from prometheus_client import Counter, Histogram

from apps.extraction.adapters.base import ToolExecutionStatus

# 1 KiB .. 10 GiB, so uploads from tiny images to large video files land in distinct buckets.
FILE_SIZE_BUCKETS = tuple(1024 * 4**n for n in range(0, 12))

EXTRACT_REQUESTS = Counter(
    "mes_extract_requests_total",
    "Metadata extraction requests by outcome.",
    ["status"],
)
EXTRACT_DURATION = Histogram(
    "mes_extract_request_duration_seconds",
    "Duration of metadata extraction requests.",
)
UPLOADED_FILE_BYTES = Histogram(
    "mes_uploaded_file_bytes",
    "Size of the files submitted for extraction, in bytes.",
    buckets=FILE_SIZE_BUCKETS,
)
UPLOADED_BYTES = Counter(
    "mes_uploaded_bytes_total",
    "Accumulated size of all files submitted for extraction, in bytes.",
)
TOOL_RUNS = Counter(
    "mes_tool_runs_total",
    "Extraction tool executions by tool and execution status.",
    ["tool", "status"],
)
TOOL_DURATION = Histogram(
    "mes_tool_duration_seconds",
    "Duration of extraction tool executions.",
    ["tool"],
)


def record_request(status: str, duration_seconds: float) -> None:
    EXTRACT_REQUESTS.labels(status=status).inc()
    EXTRACT_DURATION.observe(duration_seconds)


def record_upload(size_bytes: int) -> None:
    UPLOADED_FILE_BYTES.observe(size_bytes)
    UPLOADED_BYTES.inc(size_bytes)


def record_tool_results(results) -> None:
    for result in results:
        TOOL_RUNS.labels(tool=result.tool_name, status=result.status.value).inc()
        TOOL_DURATION.labels(tool=result.tool_name).observe(
            max((result.finished_at - result.started_at).total_seconds(), 0.0)
        )


def classify_outcome(result) -> str:
    """success: every tool succeeded; failed: none did; partial: anything in between."""
    succeeded = [r for r in result.results if r.status == ToolExecutionStatus.SUCCESS]
    if not succeeded:
        return "failed"
    if len(succeeded) == len(result.results) and not result.errors:
        return "success"
    return "partial"
