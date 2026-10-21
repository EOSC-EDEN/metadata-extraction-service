"""Asynchronous extraction jobs (MES-REQ-3-010).

`POST /api/v1/jobs/` runs the same `ExtractionOrchestrator.extract` as the synchronous endpoint on a
bounded background thread pool, so a job's result is identical to the synchronous one by construction.

Job state lives in a job store (the contract is described on `InMemoryJobStore` in `job_store.py`), chosen by `MES_JOB_STORE`. The
in-memory store loses jobs on restart and shows them only to the process that created them, so it needs
a single worker process; the Redis store is shared by all workers and survives restarts. Either way a job
runs in the process that received the upload.

Retention: a finished job's data stays available for `MES_JOB_TTL_SECONDS` after it finishes; a job that
has not finished is given the same time from its creation. A job whose age equals the TTL is already
expired. Expired jobs are purged on access and on submit, and their upload is deleted then, so a job
that never finishes (or times out) does not keep its data. A late result of a purged job is discarded.

Batches (`POST /api/v1/jobs/batch/`): a batch is one ordinary job per file plus a `BatchRecord` listing
them. It has no state of its own; its status is computed from its jobs (`aggregate_batch_status`).
A batch lives or expires as one unit: its jobs never expire one by one, and when the batch is as old
as the TTL (its age is that of its youngest job: the latest finish, or the creation for a job that
has not finished) the batch record and every job go together, with their uploads. A batch that has
lost a job (for example a Redis key expired) is never served, and is removed whole once it is old enough.

Metrics: a job is counted in `mes_extract_requests_total` once a worker has run it (never while it
waits, and never if it expires before being picked up). `mes_jobs_waiting` and `mes_jobs_waiting_bytes`
are read from the store at scrape time (`waiting()`), not kept as per-process gauges.
"""

import logging
import os
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.api.job_store import (  # noqa: F401  (re-exported: the job types live in job_store)
    BatchRecord,
    InMemoryJobStore,
    JobRecord,
    JobStatus,
    JobStoreFull,
    RedisJobStore,
    build_job_store,
)
from apps.api.serializers import serialize_extraction_result
from apps.extraction.exceptions import ToolNotConfigured
from apps.extraction.services.orchestrator import ExtractionOrchestrator
from apps.metrics import metrics

logger = logging.getLogger(__name__)

_JOB_STORES = ("memory", "redis")
_JOB_SETTINGS = ("MES_ASYNC_MAX_WORKERS", "MES_ASYNC_MAX_JOBS", "MES_JOB_TTL_SECONDS")
_UNEXPECTED_ERROR = "An unexpected error occurred while processing the job."


def _extract_and_serialize(
    *, file_path, filename, puid, mime_type, object_identifier, object_identifier_type, request_id
) -> dict:
    result = ExtractionOrchestrator().extract(
        file_path, puid=puid, mime_type=mime_type, request_id=request_id, filename=filename
    )
    metrics.record_tool_results(result.results)
    return serialize_extraction_result(
        result,
        request_id,
        filename,
        object_identifier=object_identifier,
        object_identifier_type=object_identifier_type,
    )


def aggregate_batch_status(statuses) -> str:
    """pending: all pending; succeeded or failed: all of that; partial: all finished, some failed;
    running: anything else (some running, or pending mixed with finished)."""
    statuses = list(statuses)
    if all(s is JobStatus.PENDING for s in statuses):
        return "pending"
    if all(s is JobStatus.SUCCEEDED for s in statuses):
        return "succeeded"
    if all(s is JobStatus.FAILED for s in statuses):
        return "failed"
    if all(s in (JobStatus.SUCCEEDED, JobStatus.FAILED) for s in statuses):
        return "partial"
    return "running"


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
        self,
        *,
        file_path,
        work_dir,
        filename,
        puid,
        mime_type,
        request_id,
        size_bytes,
        object_identifier=None,
        object_identifier_type=None,
    ) -> JobRecord:
        """Queue a job. The caller owns `work_dir` until this returns; afterwards the job does
        (it is removed when the job finishes). Raises `JobStoreFull` when at capacity."""
        self.purge_expired()
        record = JobRecord(
            job_id=str(uuid.uuid4()),
            request_id=request_id,
            status=JobStatus.PENDING,
            created_at=self._clock(),
            work_dir=work_dir,
            size_bytes=size_bytes,
        )
        self._store.create(record)
        try:
            self._executor.submit(
                self._run,
                record.job_id,
                file_path,
                work_dir,
                filename,
                puid,
                mime_type,
                request_id,
                object_identifier,
                object_identifier_type,
            )
        except Exception:
            self._finish(record.job_id, JobStatus.FAILED, errors=[_UNEXPECTED_ERROR])
            raise
        return record

    def submit_batch(self, items, *, puid, mime_type, request_id) -> tuple[BatchRecord, list[JobRecord]]:
        """Queue one job per item (`file_path`, `work_dir`, `filename`, `size_bytes`), all or none.

        Raises `JobStoreFull`, with no job created, unless the store has room for every item; the
        caller then still owns every `work_dir`. Otherwise each job owns its `work_dir` from here on.
        Job `i` runs with request id `<request_id>-<i>`. If the executor refuses a job, that job and
        the ones after it are marked failed (and their uploads removed) instead of raising: the
        earlier ones are already running, so the batch is still reported.
        """
        self.purge_expired()
        now = self._clock()
        batch_id = str(uuid.uuid4())
        records = [
            JobRecord(
                job_id=str(uuid.uuid4()),
                request_id=f"{request_id}-{index}",
                status=JobStatus.PENDING,
                created_at=now,
                work_dir=item["work_dir"],
                size_bytes=item["size_bytes"],
                batch_id=batch_id,
            )
            for index, item in enumerate(items)
        ]
        self._store.create_many(records)
        batch = BatchRecord(
            batch_id=batch_id,
            request_id=request_id,
            created_at=now,
            jobs=[
                {"index": index, "filename": item["filename"], "job_id": record.job_id}
                for index, (item, record) in enumerate(zip(items, records))
            ],
        )
        self._store.create_batch(batch)
        for position, (item, record) in enumerate(zip(items, records)):
            try:
                self._executor.submit(
                    self._run,
                    record.job_id,
                    item["file_path"],
                    item["work_dir"],
                    item["filename"],
                    puid,
                    mime_type,
                    record.request_id,
                    None,
                    None,
                )
            except Exception:
                logger.exception("batch %s: could not queue job %s", batch.batch_id, record.job_id)
                for refused, refused_record in list(zip(items, records))[position:]:
                    self._finish(refused_record.job_id, JobStatus.FAILED, errors=[_UNEXPECTED_ERROR])
                    shutil.rmtree(refused["work_dir"], ignore_errors=True)
                break
        return batch, records

    def get_batch(self, batch_id) -> tuple[BatchRecord, list[JobRecord]] | None:
        """The batch with the current record of each of its jobs, or None if unknown or expired."""
        self.purge_expired()
        batch = self._store.get_batch(batch_id)
        if batch is None:
            return None
        jobs = [self._store.get(entry["job_id"]) for entry in batch.jobs]
        if any(job is None for job in jobs):
            return None
        return batch, jobs

    def get(self, job_id) -> JobRecord | None:
        self.purge_expired()
        return self._store.get(job_id)

    def waiting(self) -> tuple[int, int]:
        """The jobs still waiting for a worker (count, upload bytes), expired ones dropped first."""
        self.purge_expired()
        return self._store.waiting()

    def purge_expired(self) -> int:
        """Delete expired jobs and batches with their uploads; return how many jobs were removed.

        A job that belongs to a batch is only ever removed together with its batch.
        """
        now = self._clock()
        expired = self._store.delete_expired(now, self._ttl_seconds)
        expired += self._purge_expired_batches(now)
        for record in expired:
            if record.work_dir:
                shutil.rmtree(record.work_dir, ignore_errors=True)
        return len(expired)

    def _purge_expired_batches(self, now) -> list[JobRecord]:
        """Remove each batch that is as old as the TTL, with all of its jobs, and return those jobs.

        A batch cannot be older than its creation, so only batches created at least a TTL ago are
        looked at. Its age is the age of its youngest job; a batch that lost a job counts as old
        enough as soon as it is a TTL old, whatever the remaining jobs say.
        """
        cutoff = now - timedelta(seconds=self._ttl_seconds)
        removed = []
        for batch in self._store.batches_created_before(cutoff):
            jobs = [self._store.get(entry["job_id"]) for entry in batch.jobs]
            present = [job for job in jobs if job is not None]
            if len(present) == len(jobs) and max(job.aged_from for job in present) > cutoff:
                continue
            for job in present:
                deleted = self._store.delete(job.job_id)
                if deleted is not None:
                    removed.append(deleted)
            self._store.delete_batch(batch.batch_id)
        return removed

    def live_work_dirs(self) -> set[str]:
        """The upload directories of the jobs currently stored."""
        return self._store.work_dirs()

    def _run(
        self,
        job_id,
        file_path,
        work_dir,
        filename,
        puid,
        mime_type,
        request_id,
        object_identifier=None,
        object_identifier_type=None,
    ):
        extra = {"request_id": request_id}
        record = self._store.get(job_id)
        if record is None:  # expired while waiting: its upload is already gone
            shutil.rmtree(work_dir, ignore_errors=True)
            return
        record.status = JobStatus.RUNNING
        if not self._store.update(record):  # purged since the get: its upload is already gone
            shutil.rmtree(work_dir, ignore_errors=True)
            return
        started = time.perf_counter()
        status, result, errors = JobStatus.SUCCEEDED, None, []
        try:
            result = self._extract(
                file_path=file_path,
                filename=filename,
                puid=puid,
                mime_type=mime_type,
                object_identifier=object_identifier,
                object_identifier_type=object_identifier_type,
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
    """The job settings must be valid; checked at startup."""
    for name in _JOB_SETTINGS:
        value = getattr(settings, name, None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ImproperlyConfigured(f"settings.{name} must be an integer >= 1, got {value!r}.")

    store = getattr(settings, "MES_JOB_STORE", None)
    if store not in _JOB_STORES:
        raise ImproperlyConfigured(
            f"settings.MES_JOB_STORE must be one of {', '.join(_JOB_STORES)}, got {store!r}."
        )
    if store == "redis":
        url = getattr(settings, "MES_REDIS_URL", None)
        if not isinstance(url, str) or not url.strip():
            raise ImproperlyConfigured("settings.MES_REDIS_URL is required when MES_JOB_STORE=redis.")

    _validate_upload_dir(getattr(settings, "MES_JOB_UPLOAD_DIR", None))


def _validate_upload_dir(path) -> None:
    if not isinstance(path, str) or not path.strip():
        raise ImproperlyConfigured(f"settings.MES_JOB_UPLOAD_DIR must be a directory path, got {path!r}.")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise ImproperlyConfigured(f"MES_JOB_UPLOAD_DIR {path!r} cannot be created: {exc}") from exc
    if not os.path.isdir(path) or not os.access(path, os.W_OK | os.X_OK):
        raise ImproperlyConfigured(f"MES_JOB_UPLOAD_DIR {path!r} is not a writable directory.")


@lru_cache(maxsize=None)
def get_job_service() -> JobService:
    """The process-wide service, built once from the settings.

    Warmed up at startup (`ApiConfig.ready`) so concurrent first requests cannot build two.
    Tests call `get_job_service.cache_clear()` to rebuild it from overridden settings.
    """
    return JobService(
        store=build_job_store(settings),
        executor=ThreadPoolExecutor(
            max_workers=settings.MES_ASYNC_MAX_WORKERS, thread_name_prefix="mes-job"
        ),
        ttl_seconds=settings.MES_JOB_TTL_SECONDS,
    )
