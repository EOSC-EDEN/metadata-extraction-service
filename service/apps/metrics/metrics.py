"""Prometheus metrics for the extract endpoint.

Labels are deliberately low-cardinality: never add filenames, request ids or user identifiers.
Helpers are the only API the rest of the code uses, so the instrumentation stays out of the
extraction wrapper.
"""

import logging

from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from apps.extraction.services.orchestrator import classify_statuses

logger = logging.getLogger(__name__)

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


class JobsWaitingCollector(Collector):
    """`mes_jobs_waiting` and `mes_jobs_waiting_bytes`, read from the job store at scrape time.

    These are not per-process gauges on purpose: with a shared (Redis) store every worker would
    report the same global count, and Prometheus' multiprocess mode would add those up. Reading
    the store once per scrape gives the true number for any store and worker count. A store that
    cannot be read leaves the two metrics out; the rest of the scrape is unaffected.
    """

    def __init__(self, waiting):
        self._waiting = waiting

    def collect(self):
        try:
            count, size_bytes = self._waiting()
        except Exception:
            logger.exception("could not read the waiting jobs; omitting mes_jobs_waiting*")
            return
        yield GaugeMetricFamily(
            "mes_jobs_waiting",
            "Asynchronous jobs accepted but not yet picked up for execution.",
            value=count,
        )
        yield GaugeMetricFamily(
            "mes_jobs_waiting_bytes",
            "Size of the uploaded files of the jobs waiting to be picked up, in bytes.",
            value=size_bytes,
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
    return result.outcome


def classify_serialized_outcome(payload: dict) -> str:
    """`classify_outcome` for a serialised result (the `mes-result` document)."""
    statuses = [r["status"] for r in payload.get("results", [])]
    return classify_statuses(statuses, bool(payload.get("errors")))
