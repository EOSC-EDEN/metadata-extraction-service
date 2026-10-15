"""Asynchronous extraction jobs (MES-REQ-3-010).

`POST /api/v1/jobs/` runs the same `ExtractionOrchestrator.extract` as the synchronous endpoint on a
bounded background thread pool, so a job's result is identical to the synchronous one by construction.

Job state lives behind the `JobStore` interface. `InMemoryJobStore` is the only implementation for now:
jobs are lost on restart and are visible only to the process that created them, so this mode needs a
single worker process until a shared store (Redis) is added.

Retention: a finished job's data stays available for `MES_JOB_TTL_SECONDS` after it finishes; a job that
has not finished is given the same time from its creation. A job whose age equals the TTL is already
expired. Expired jobs are purged on access and on submit, and their upload is deleted then, so a job
that never finishes (or times out) does not keep its data. A late result of a purged job is discarded.

Metrics: a job is counted in `mes_extract_requests_total` once a worker has run it (never while it
waits, and never if it expires before being picked up); `mes_jobs_waiting` and `mes_jobs_waiting_bytes`
report the jobs still waiting for a worker.
"""

import logging
import shutil
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.api.serializers import serialize_extraction_result
from apps.extraction.exceptions import ToolNotConfigured
from apps.extraction.services.orchestrator import ExtractionOrchestrator
from apps.metrics import metrics

logger = logging.getLogger(__name__)

_JOB_SETTINGS = ("MES_ASYNC_MAX_WORKERS", "MES_ASYNC_MAX_JOBS", "MES_JOB_TTL_SECONDS")
_UNEXPECTED_ERROR = "An unexpected error occurred while processing the job."


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobStoreFull(Exception):
    """The store holds `max_jobs` jobs; no new job can be accepted."""


@dataclass
class JobRecord:
    job_id: str
    request_id: str
    status: JobStatus
    created_at: datetime
    #: Directory holding the uploaded file, deleted when the job finishes or is purged.
    work_dir: str | None = None
    size_bytes: int = 0
    finished_at: datetime | None = None
    errors: list[str] = field(default_factory=list)
    #: The serialised extraction result (the `mes-result` document), once succeeded.
    result: dict | None = None


class JobStore(ABC):
    @abstractmethod
    def create(self, record: JobRecord) -> None:
        """Store a new job; raise `JobStoreFull` when at capacity."""

    @abstractmethod
    def get(self, job_id: str) -> JobRecord | None: ...

    @abstractmethod
    def update(self, record: JobRecord) -> bool:
        """Save a known job; return False (storing nothing) if it was purged meanwhile."""

    @abstractmethod
    def delete_expired(self, now: datetime, ttl_seconds: int) -> list[JobRecord]:
        """Delete jobs whose age is at least the TTL and return them.

        Age counts from finishing, or from creation for a job that has not finished.
        """

    @abstractmethod
    def waiting(self) -> tuple[int, int]:
        """The number of pending jobs and the total size of their uploads, in bytes."""


class InMemoryJobStore(JobStore):
    def __init__(self, max_jobs: int):
        self._max_jobs = max_jobs
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def create(self, record):
        with self._lock:
            if len(self._jobs) >= self._max_jobs:
                raise JobStoreFull(f"The job store is full ({self._max_jobs} jobs).")
            self._jobs[record.job_id] = replace(record)

    def get(self, job_id):
        with self._lock:
            record = self._jobs.get(job_id)
            return replace(record) if record else None

    def update(self, record):
        with self._lock:
            if record.job_id not in self._jobs:
                return False
            self._jobs[record.job_id] = replace(record)
            return True

    def delete_expired(self, now, ttl_seconds):
        cutoff = now - timedelta(seconds=ttl_seconds)
        with self._lock:
            expired = [
                record
                for record in self._jobs.values()
                if (record.finished_at or record.created_at) <= cutoff
            ]
            for record in expired:
                del self._jobs[record.job_id]
        return [replace(record) for record in expired]

    def waiting(self):
        with self._lock:
            pending = [r for r in self._jobs.values() if r.status is JobStatus.PENDING]
        return len(pending), sum(r.size_bytes for r in pending)


def _extract_and_serialize(*, file_path, filename, puid, mime_type, request_id) -> dict:
    result = ExtractionOrchestrator().extract(
        file_path, puid=puid, mime_type=mime_type, request_id=request_id
    )
    metrics.record_tool_results(result.results)
    return serialize_extraction_result(result, request_id, filename)


def _utcnow():
    return datetime.now(timezone.utc)


class JobService:
    def __init__(self, store, executor, ttl_seconds, clock=_utcnow, extract=_extract_and_serialize):
        self._store = store
        self._executor = executor
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._extract = extract

    def submit(
        self, *, file_path, work_dir, filename, puid, mime_type, request_id, size_bytes
    ) -> JobRecord:
        """Queue a job. The caller owns `work_dir` until this returns; afterwards the job does
        (it is removed when the job finishes). Raises `JobStoreFull` when at capacity."""
        self._purge_expired()
        record = JobRecord(
            job_id=str(uuid.uuid4()),
            request_id=request_id,
            status=JobStatus.PENDING,
            created_at=self._clock(),
            work_dir=work_dir,
            size_bytes=size_bytes,
        )
        self._store.create(record)
        self._publish_waiting()
        try:
            self._executor.submit(
                self._run, record.job_id, file_path, work_dir, filename, puid, mime_type, request_id
            )
        except Exception:
            self._finish(record.job_id, JobStatus.FAILED, errors=[_UNEXPECTED_ERROR])
            self._publish_waiting()
            raise
        return record

    def get(self, job_id) -> JobRecord | None:
        self._purge_expired()
        return self._store.get(job_id)

    def refresh_waiting_metrics(self):
        """Bring `mes_jobs_waiting*` up to date: purge expired jobs, then publish the gauges."""
        self._purge_expired()
        self._publish_waiting()

    def _purge_expired(self):
        expired = self._store.delete_expired(self._clock(), self._ttl_seconds)
        for record in expired:
            shutil.rmtree(record.work_dir, ignore_errors=True)
        if expired:
            self._publish_waiting()

    def _publish_waiting(self):
        # Gauges are best-effort: a metrics failure must not break job handling.
        try:
            metrics.set_jobs_waiting(*self._store.waiting())
        except Exception:
            logger.exception("could not publish the waiting-jobs gauges")

    def _run(self, job_id, file_path, work_dir, filename, puid, mime_type, request_id):
        extra = {"request_id": request_id}
        record = self._store.get(job_id)
        if record is None:  # expired while waiting: its upload is already gone
            shutil.rmtree(work_dir, ignore_errors=True)
            return
        record.status = JobStatus.RUNNING
        if not self._store.update(record):  # purged since the get: its upload is already gone
            shutil.rmtree(work_dir, ignore_errors=True)
            return
        self._publish_waiting()
        started = time.perf_counter()
        status, result, errors = JobStatus.SUCCEEDED, None, []
        try:
            result = self._extract(
                file_path=file_path,
                filename=filename,
                puid=puid,
                mime_type=mime_type,
                request_id=request_id,
            )
        except ToolNotConfigured as exc:
            logger.error("job %s: tool not configured: %s", job_id, exc, extra=extra)
            status, errors = JobStatus.FAILED, [str(exc)]
        except Exception:
            logger.exception("job %s failed", job_id, extra=extra)
            status, errors = JobStatus.FAILED, [_UNEXPECTED_ERROR]
        finally:
            # Gone before the job reports a final state.
            shutil.rmtree(work_dir, ignore_errors=True)
        try:
            outcome = metrics.classify_serialized_outcome(result) if result is not None else "error"
            metrics.record_request(outcome, time.perf_counter() - started)
        except Exception:
            logger.exception("job %s: could not record metrics", job_id, extra=extra)
        self._finish(job_id, status, result=result, errors=errors)

    def _finish(self, job_id, status, result=None, errors=()):
        record = self._store.get(job_id)
        if record is None:  # purged while running: discard the late result
            return
        record.status = status
        record.result = result
        record.errors = list(errors)
        record.finished_at = self._clock()
        self._store.update(record)


def validate_jobs_config() -> None:
    """The job settings must be positive integers; checked at startup."""
    for name in _JOB_SETTINGS:
        value = getattr(settings, name, None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ImproperlyConfigured(f"settings.{name} must be an integer >= 1, got {value!r}.")


@lru_cache(maxsize=None)
def get_job_service() -> JobService:
    """The process-wide service, built once from the settings.

    Warmed up at startup (`ApiConfig.ready`) so concurrent first requests cannot build two.
    Tests call `get_job_service.cache_clear()` to rebuild it from overridden settings.
    """
    return JobService(
        store=InMemoryJobStore(max_jobs=settings.MES_ASYNC_MAX_JOBS),
        executor=ThreadPoolExecutor(
            max_workers=settings.MES_ASYNC_MAX_WORKERS, thread_name_prefix="mes-job"
        ),
        ttl_seconds=settings.MES_JOB_TTL_SECONDS,
    )
