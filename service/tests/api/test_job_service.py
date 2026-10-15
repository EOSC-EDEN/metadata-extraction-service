import os
import tempfile
from datetime import timedelta

from django.test import SimpleTestCase

from apps.api.jobs import (
    InMemoryJobStore,
    JobRecord,
    JobService,
    JobStatus,
    JobStoreFull,
)
from tests.api.job_helpers import DeferredExecutor, FakeClock, ImmediateExecutor

TTL = 100


def _work_dir():
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "upload.bin")
    with open(path, "wb") as handle:
        handle.write(b"x")
    return directory, path


def _service(extract=None, executor=None, max_jobs=10, clock=None):
    clock = clock or FakeClock()
    return JobService(
        store=InMemoryJobStore(max_jobs=max_jobs),
        executor=executor or ImmediateExecutor(),
        ttl_seconds=TTL,
        clock=clock,
        extract=extract or (lambda **kwargs: {"ok": True, "filename": kwargs["filename"]}),
    ), clock


def _submit(service, **overrides):
    directory, path = _work_dir()
    args = dict(
        file_path=path,
        work_dir=directory,
        filename="a.bin",
        puid=None,
        mime_type=None,
        request_id="req-1",
        size_bytes=1,
    )
    args.update(overrides)
    return service.submit(**args), directory


class SubmitTests(SimpleTestCase):
    def test_pending_until_the_worker_runs(self):
        executor = DeferredExecutor()
        service, _ = _service(executor=executor)
        record, _ = _submit(service)
        self.assertEqual(record.status, JobStatus.PENDING)
        self.assertEqual(service.get(record.job_id).status, JobStatus.PENDING)
        executor.run_all()
        self.assertEqual(service.get(record.job_id).status, JobStatus.SUCCEEDED)

    def test_status_is_running_while_extracting(self):
        seen = []
        holder = {}

        def extract(**kwargs):
            seen.append(holder["service"].get(holder["job_id"]).status)
            return {}

        executor = DeferredExecutor()
        service, _ = _service(extract=extract, executor=executor)
        holder["service"] = service
        record, _ = _submit(service)
        holder["job_id"] = record.job_id
        executor.run_all()
        self.assertEqual(seen, [JobStatus.RUNNING])

    def test_success_stores_result_and_removes_upload(self):
        service, clock = _service()
        record, directory = _submit(service)
        done = service.get(record.job_id)
        self.assertEqual(done.status, JobStatus.SUCCEEDED)
        self.assertEqual(done.result, {"ok": True, "filename": "a.bin"})
        self.assertEqual(done.finished_at, clock.now)
        self.assertEqual(done.errors, [])
        self.assertFalse(os.path.exists(directory))

    def test_extract_receives_the_submitted_parameters(self):
        received = {}
        service, _ = _service(extract=lambda **kwargs: received.update(kwargs) or {})
        record, _ = _submit(service, puid="fmt/43", mime_type="image/jpeg")
        self.assertEqual(received["puid"], "fmt/43")
        self.assertEqual(received["mime_type"], "image/jpeg")
        self.assertEqual(received["request_id"], "req-1")
        self.assertTrue(received["file_path"].endswith("upload.bin"))

    def test_failure_is_recorded_and_upload_removed(self):
        def boom(**kwargs):
            raise RuntimeError("tool exploded")

        service, _ = _service(extract=boom)
        with self.assertLogs("apps.api.jobs", "ERROR"):
            record, directory = _submit(service)
        failed = service.get(record.job_id)
        self.assertEqual(failed.status, JobStatus.FAILED)
        self.assertIsNone(failed.result)
        self.assertEqual(len(failed.errors), 1)
        self.assertNotIn("tool exploded", failed.errors[0])  # no internals leaked
        self.assertFalse(os.path.exists(directory))

    def test_unknown_job_is_none(self):
        service, _ = _service()
        self.assertIsNone(service.get("nope"))


class CapacityTests(SimpleTestCase):
    def test_rejects_when_full_and_accepts_at_the_cap(self):
        service, _ = _service(executor=DeferredExecutor(), max_jobs=2)
        _submit(service)
        _submit(service)  # exactly at the cap is fine
        with self.assertRaises(JobStoreFull):
            _submit(service)

    def test_cap_of_one(self):
        service, _ = _service(executor=DeferredExecutor(), max_jobs=1)
        _submit(service)
        with self.assertRaises(JobStoreFull):
            _submit(service)

    def test_expired_jobs_are_purged_on_submit_to_make_room(self):
        service, clock = _service(max_jobs=1)
        first, _ = _submit(service)
        clock.advance(TTL)
        second, _ = _submit(service)  # would be full without the purge
        self.assertIsNone(service.get(first.job_id))
        self.assertIsNotNone(service.get(second.job_id))


class RetentionTests(SimpleTestCase):
    def test_ttl_boundaries(self):
        for offset, available in ((TTL - 1, True), (TTL, False), (TTL + 1, False)):
            with self.subTest(offset=offset):
                service, clock = _service()
                record, _ = _submit(service)
                clock.advance(offset)
                self.assertEqual(service.get(record.job_id) is not None, available)

    def test_ttl_counts_from_finish_once_finished(self):
        executor = DeferredExecutor()
        service, clock = _service(executor=executor)
        record, _ = _submit(service)
        clock.advance(TTL - 1)  # queued almost as long as the TTL
        executor.run_all()
        clock.advance(TTL - 1)  # far past creation + TTL, but just under finish + TTL
        self.assertIsNotNone(service.get(record.job_id))
        clock.advance(1)
        self.assertIsNone(service.get(record.job_id))

    def test_unfinished_job_is_removed_with_its_upload_at_the_ttl(self):
        for offset, available in ((TTL - 1, True), (TTL, False), (TTL + 1, False)):
            with self.subTest(offset=offset):
                service, clock = _service(executor=DeferredExecutor())
                record, directory = _submit(service)
                clock.advance(offset)
                self.assertEqual(service.get(record.job_id) is not None, available)
                self.assertEqual(os.path.exists(directory), available)

    def test_running_job_that_times_out_is_removed_and_its_late_result_discarded(self):
        holder = {}

        def slow_extract(**kwargs):
            holder["clock"].advance(TTL)  # the run outlasts the TTL
            holder["service"].get(holder["job_id"])  # an access purges it
            return {"late": True}

        service, clock = _service(extract=slow_extract, executor=DeferredExecutor())
        holder.update(service=service, clock=clock)
        record, directory = _submit(service)
        holder["job_id"] = record.job_id
        service._executor.run_all()
        self.assertIsNone(service.get(record.job_id))
        self.assertFalse(os.path.exists(directory))

    def test_expired_pending_job_is_never_executed(self):
        calls = []
        executor = DeferredExecutor()
        service, clock = _service(extract=lambda **kw: calls.append(kw), executor=executor)
        record, _ = _submit(service)
        clock.advance(TTL)
        service.get(record.job_id)
        executor.run_all()
        self.assertEqual(calls, [])

    def test_failed_jobs_expire_too(self):
        def boom(**kwargs):
            raise RuntimeError

        service, clock = _service(extract=boom)
        with self.assertLogs("apps.api.jobs", "ERROR"):
            record, _ = _submit(service)
        clock.advance(TTL)
        self.assertIsNone(service.get(record.job_id))


class StoreTests(SimpleTestCase):
    def _record(self, job_id="j", **kwargs):
        clock = FakeClock()
        return JobRecord(job_id=job_id, request_id="r", status=JobStatus.PENDING, created_at=clock.now, **kwargs)

    def test_get_returns_a_copy(self):
        store = InMemoryJobStore(max_jobs=5)
        store.create(self._record())
        store.get("j").status = JobStatus.FAILED
        self.assertEqual(store.get("j").status, JobStatus.PENDING)

    def test_delete_expired_returns_count_and_keeps_fresh(self):
        store = InMemoryJobStore(max_jobs=5)
        clock = FakeClock()
        for job_id, age in (("old", TTL), ("fresh", TTL - 1)):
            record = self._record(job_id)
            record.status = JobStatus.SUCCEEDED
            record.finished_at = clock.now - timedelta(seconds=age)
            store.create(record)
        store.create(self._record("running"))
        self.assertEqual([r.job_id for r in store.delete_expired(clock.now, TTL)], ["old"])
        self.assertIsNone(store.get("old"))
        self.assertIsNotNone(store.get("fresh"))
        self.assertIsNotNone(store.get("running"))  # created just now
        self.assertEqual(store.delete_expired(clock.now, TTL), [])
