"""Prometheus metrics for the extract endpoint.

Labels are deliberately low-cardinality: never add filenames, request ids or user identifiers.
Helpers are the only API the rest of the code uses, so the instrumentation stays out of the
extraction wrapper.
"""

from prometheus_client import Counter, Gauge, Histogram

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
JOBS_WAITING = Gauge(
    "mes_jobs_waiting",
    "Asynchronous jobs accepted but not yet picked up for execution.",
    multiprocess_mode="livesum",
)
JOBS_WAITING_BYTES = Gauge(
    "mes_jobs_waiting_bytes",
    "Size of the uploaded files of the jobs waiting to be picked up, in bytes.",
    multiprocess_mode="livesum",
)


def set_jobs_waiting(count: int, size_bytes: int) -> None:
    JOBS_WAITING.set(count)
    JOBS_WAITING_BYTES.set(size_bytes)


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


def _classify(statuses, has_errors: bool) -> str:
    succeeded = [s for s in statuses if s == ToolExecutionStatus.SUCCESS.value]
    if not succeeded:
        return "failed"
    if len(succeeded) == len(statuses) and not has_errors:
        return "success"
    return "partial"


def classify_outcome(result) -> str:
    """success: every tool succeeded; failed: none did; partial: anything in between."""
    return _classify([r.status.value for r in result.results], bool(result.errors))


def classify_serialized_outcome(payload: dict) -> str:
    """`classify_outcome` for a serialised result (the `mes-result` document)."""
    statuses = [r["status"] for r in payload.get("results", [])]
    return _classify(statuses, bool(payload.get("errors")))
