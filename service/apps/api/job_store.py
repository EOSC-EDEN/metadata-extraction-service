"""Job state for asynchronous extraction (MES-REQ-3-010).

`JobService` talks to a job store: any object with the methods listed on `InMemoryJobStore`.
`InMemoryJobStore` keeps jobs in the process that
created them (single worker process only); `RedisJobStore` keeps them in Redis, so jobs survive a
restart and every gunicorn worker sees them. `build_job_store` picks one from the settings.

Age: a finished job ages from its finish time, a job that has not finished from its creation. A job
whose age equals the TTL is already expired.
"""

import json
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

import redis

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MICROSECOND = timedelta(microseconds=1)


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
    #: The batch this job belongs to. A batch's jobs never expire one by one: the batch and all its
    #: jobs expire together (see `JobService.purge_expired`), so `delete_expired` leaves them alone.
    batch_id: str | None = None

    def to_dict(self) -> dict:
        """Plain JSON-serialisable values (ISO datetimes, the status as its string value)."""
        data = asdict(self)
        data["status"] = self.status.value
        data["created_at"] = self.created_at.isoformat()
        data["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "JobRecord":
        data = dict(data)
        data["status"] = JobStatus(data["status"])
        data["created_at"] = _parse_datetime(data["created_at"])
        if data.get("finished_at"):
            data["finished_at"] = _parse_datetime(data["finished_at"])
        return cls(**data)

    @property
    def aged_from(self) -> datetime:
        return self.finished_at or self.created_at


@dataclass
class BatchRecord:
    """A group of jobs submitted together (`POST /api/v1/jobs/batch/`).

    It holds only the job ids and filenames; the status of each file is read from its job, so the
    two cannot drift apart. `jobs` is `[{"index", "filename", "job_id"}, ...]` in upload order.
    """

    batch_id: str
    request_id: str
    created_at: datetime
    jobs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BatchRecord":
        data = dict(data)
        data["created_at"] = _parse_datetime(data["created_at"])
        return cls(**data)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"datetime without a time zone: {value!r}")
    return parsed


class InMemoryJobStore:
    """Jobs in this process only (single worker process).

    The store contract, which `RedisJobStore` also follows and which
    `tests/api/test_job_store_contract.py` checks for both:

      create(record)             store a new job; raise `JobStoreFull` when at capacity
      create_many(records)       store all the jobs or none: raise `JobStoreFull` unless every one fits
      get(job_id)                the job, or None
      update(record)             save a known job; False (storing nothing) if it was purged
      delete(job_id)             remove one job; return it, or None if it was not stored
      delete_expired(now, ttl)   delete jobs aged at least the TTL (not those of a batch); return the
                                 ones this call removed
      waiting()                  (number of pending jobs, total size of their uploads in bytes)
      work_dirs()                the upload directories of all stored jobs (to find orphans)
      create_batch(batch)        store a `BatchRecord` (batches do not count against the job cap)
      get_batch(batch_id)        the batch, or None
      batches()                  all stored batches
      batches_created_before(t)  the batches created at or before `t` (the candidates for expiry)
      delete_batch(batch_id)     remove a batch record; unknown ids are ignored
    """

    def __init__(self, max_jobs: int):
        self.max_jobs = max_jobs
        self._jobs: dict[str, JobRecord] = {}
        self._batches: dict[str, BatchRecord] = {}
        self._lock = threading.Lock()

    def create(self, record):
        with self._lock:
            if len(self._jobs) >= self.max_jobs:
                raise JobStoreFull(f"The job store is full ({self.max_jobs} jobs).")
            self._jobs[record.job_id] = replace(record)

    def create_many(self, records):
        with self._lock:
            if len(self._jobs) + len(records) > self.max_jobs:
                raise JobStoreFull(
                    f"The job store cannot take {len(records)} more jobs (limit {self.max_jobs})."
                )
            for record in records:
                self._jobs[record.job_id] = replace(record)

    def get(self, job_id):
        with self._lock:
            record = self._jobs.get(job_id)
            return replace(record) if record else None

    def create_batch(self, batch):
        with self._lock:
            self._batches[batch.batch_id] = _copy_batch(batch)

    def get_batch(self, batch_id):
        with self._lock:
            batch = self._batches.get(batch_id)
            return _copy_batch(batch) if batch else None

    def batches(self):
        with self._lock:
            return [_copy_batch(batch) for batch in self._batches.values()]

    def batches_created_before(self, cutoff):
        with self._lock:
            return [_copy_batch(b) for b in self._batches.values() if b.created_at <= cutoff]

    def delete_batch(self, batch_id):
        with self._lock:
            self._batches.pop(batch_id, None)

    def update(self, record):
        with self._lock:
            if record.job_id not in self._jobs:
                return False
            self._jobs[record.job_id] = replace(record)
            return True

    def delete(self, job_id):
        with self._lock:
            record = self._jobs.pop(job_id, None)
        return replace(record) if record else None

    def delete_expired(self, now, ttl_seconds):
        cutoff = now - timedelta(seconds=ttl_seconds)
        with self._lock:
            expired = [r for r in self._jobs.values() if r.batch_id is None and r.aged_from <= cutoff]
            for record in expired:
                del self._jobs[record.job_id]
        return [replace(record) for record in expired]

    def waiting(self):
        with self._lock:
            pending = [r for r in self._jobs.values() if r.status is JobStatus.PENDING]
        return len(pending), sum(r.size_bytes for r in pending)

    def work_dirs(self):
        with self._lock:
            return {r.work_dir for r in self._jobs.values() if r.work_dir}


def _copy_batch(batch: BatchRecord) -> BatchRecord:
    return replace(batch, jobs=[dict(job) for job in batch.jobs])


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _score(moment: datetime) -> int:
    """Microseconds since the epoch: an exact integer, so the TTL boundary has no float error."""
    return (moment - _EPOCH) // _MICROSECOND


class RedisJobStore:
    """Jobs as JSON under `mes:job:{id}`, indexed by age in the sorted set `mes:jobs:index`.

    The key expiry (twice the TTL) is only a safety net so data cannot pile up if nothing purges;
    `delete_expired` (run on access and by `purge_expired_jobs`) is the authoritative cleanup. The
    cap is enforced with WATCH/MULTI, so concurrent workers cannot overshoot it.
    """

    KEY_PREFIX = "mes:job:"
    INDEX_KEY = "mes:jobs:index"
    BATCH_PREFIX = "mes:batch:"
    BATCH_INDEX_KEY = "mes:batches:index"

    def __init__(self, client, max_jobs: int, ttl_seconds: int):
        self._client = client
        self.max_jobs = max_jobs
        self._ttl_seconds = ttl_seconds

    def _key(self, job_id: str) -> str:
        return self.KEY_PREFIX + job_id

    def _expiry(self) -> int:
        return self._ttl_seconds * 2

    def _stored(self) -> list[JobRecord]:
        """All stored jobs; drops index entries whose key Redis has already expired."""
        ids = [_text(i) for i in self._client.zrange(self.INDEX_KEY, 0, -1)]
        if not ids:
            return []
        records = []
        stale = []
        for job_id, raw in zip(ids, self._client.mget([self._key(i) for i in ids])):
            if raw is None:
                stale.append(job_id)
            else:
                records.append(JobRecord.from_dict(json.loads(raw)))
        if stale:
            self._client.zrem(self.INDEX_KEY, *stale)
        return records

    def create(self, record):
        self._stored()  # frees the slots of jobs whose key has expired
        payload = json.dumps(record.to_dict())
        with self._client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(self.INDEX_KEY)
                    if pipe.zcard(self.INDEX_KEY) >= self.max_jobs:
                        pipe.unwatch()
                        raise JobStoreFull(f"The job store is full ({self.max_jobs} jobs).")
                    pipe.multi()
                    pipe.set(self._key(record.job_id), payload, ex=self._expiry())
                    pipe.zadd(self.INDEX_KEY, {record.job_id: _score(record.aged_from)})
                    pipe.execute()
                    return
                except redis.WatchError:
                    continue  # another worker changed the index: check the cap again

    def create_many(self, records):
        if not records:
            return
        self._stored()  # frees the slots of jobs whose key has expired
        with self._client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(self.INDEX_KEY)
                    if pipe.zcard(self.INDEX_KEY) + len(records) > self.max_jobs:
                        pipe.unwatch()
                        raise JobStoreFull(
                            f"The job store cannot take {len(records)} more jobs (limit {self.max_jobs})."
                        )
                    pipe.multi()
                    for record in records:
                        pipe.set(self._key(record.job_id), json.dumps(record.to_dict()), ex=self._expiry())
                        pipe.zadd(self.INDEX_KEY, {record.job_id: _score(record.aged_from)})
                    pipe.execute()
                    return
                except redis.WatchError:
                    continue  # another worker changed the index: check the cap again

    def get(self, job_id):
        raw = self._client.get(self._key(job_id))
        return JobRecord.from_dict(json.loads(raw)) if raw is not None else None

    def create_batch(self, batch):
        pipe = self._client.pipeline()
        pipe.set(self.BATCH_PREFIX + batch.batch_id, json.dumps(batch.to_dict()), ex=self._expiry())
        pipe.zadd(self.BATCH_INDEX_KEY, {batch.batch_id: _score(batch.created_at)})
        pipe.execute()

    def get_batch(self, batch_id):
        raw = self._client.get(self.BATCH_PREFIX + batch_id)
        return BatchRecord.from_dict(json.loads(raw)) if raw is not None else None

    def batches(self):
        ids = [_text(i) for i in self._client.zrange(self.BATCH_INDEX_KEY, 0, -1)]
        if not ids:
            return []
        found, stale = [], []
        for batch_id, raw in zip(ids, self._client.mget([self.BATCH_PREFIX + i for i in ids])):
            if raw is None:
                stale.append(batch_id)  # the key expired on its own
            else:
                found.append(BatchRecord.from_dict(json.loads(raw)))
        if stale:
            self._client.zrem(self.BATCH_INDEX_KEY, *stale)
        return found

    def batches_created_before(self, cutoff):
        ids = [_text(i) for i in self._client.zrangebyscore(self.BATCH_INDEX_KEY, "-inf", _score(cutoff))]
        if not ids:
            return []
        found = []
        for raw in self._client.mget([self.BATCH_PREFIX + i for i in ids]):
            if raw is not None:
                found.append(BatchRecord.from_dict(json.loads(raw)))
        return found

    def delete_batch(self, batch_id):
        pipe = self._client.pipeline()
        pipe.delete(self.BATCH_PREFIX + batch_id)
        pipe.zrem(self.BATCH_INDEX_KEY, batch_id)
        pipe.execute()

    def update(self, record):
        saved = self._client.set(
            self._key(record.job_id),
            json.dumps(record.to_dict()),
            ex=self._expiry(),
            xx=True,
        )
        if not saved:
            return False
        self._client.zadd(self.INDEX_KEY, {record.job_id: _score(record.aged_from)}, xx=True)
        return True

    def delete(self, job_id):
        raw = self._client.get(self._key(job_id))
        pipe = self._client.pipeline()
        pipe.delete(self._key(job_id))
        pipe.zrem(self.INDEX_KEY, job_id)
        _, claimed = pipe.execute()
        return JobRecord.from_dict(json.loads(raw)) if claimed and raw is not None else None

    def delete_expired(self, now, ttl_seconds):
        cutoff = _score(now - timedelta(seconds=ttl_seconds))
        removed = []
        for job_id in (_text(i) for i in self._client.zrangebyscore(self.INDEX_KEY, "-inf", cutoff)):
            raw = self._client.get(self._key(job_id))
            if raw is not None and json.loads(raw).get("batch_id"):
                continue  # a batch's jobs expire with the batch, never alone
            pipe = self._client.pipeline()
            pipe.delete(self._key(job_id))
            pipe.zrem(self.INDEX_KEY, job_id)
            _, claimed = pipe.execute()
            # Only the worker whose ZREM took the entry reports it, so a purge run by two
            # workers at once removes each upload and counts each job once.
            if claimed and raw is not None:
                removed.append(JobRecord.from_dict(json.loads(raw)))
        return removed

    def waiting(self):
        pending = [r for r in self._stored() if r.status is JobStatus.PENDING]
        return len(pending), sum(r.size_bytes for r in pending)

    def work_dirs(self):
        return {r.work_dir for r in self._stored() if r.work_dir}


def build_job_store(conf):
    """The store selected by `conf.MES_JOB_STORE` (`memory` or `redis`)."""
    if conf.MES_JOB_STORE == "redis":
        return RedisJobStore(
            redis.Redis.from_url(conf.MES_REDIS_URL, decode_responses=True),
            max_jobs=conf.MES_ASYNC_MAX_JOBS,
            ttl_seconds=conf.MES_JOB_TTL_SECONDS,
        )
    return InMemoryJobStore(max_jobs=conf.MES_ASYNC_MAX_JOBS)
