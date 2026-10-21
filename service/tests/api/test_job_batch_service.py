import os
import shutil
from datetime import timedelta
import tempfile

from django.test import SimpleTestCase

from apps.api.jobs import (
    InMemoryJobStore,
    JobService,
    JobStatus,
    JobStoreFull,
    aggregate_batch_status,
)
from tests.api.job_helpers import DeferredExecutor, FakeClock, ImmediateExecutor

TTL = 100


def _item(name="a.bin"):
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "upload.bin")
    with open(path, "wb") as handle:
        handle.write(b"x")
    return {"file_path": path, "work_dir": directory, "filename": name, "size_bytes": 1}


def _service(extract=None, executor=None, max_jobs=10):
    clock = FakeClock()
    return (
        JobService(
            store=InMemoryJobStore(max_jobs=max_jobs),
            executor=executor or ImmediateExecutor(),
            ttl_seconds=TTL,
            clock=clock,
            extract=extract or (lambda **kwargs: {"filename": kwargs["filename"]}),
        ),
        clock,
    )


class AggregateStatusTests(SimpleTestCase):
    P, R, S, F = JobStatus.PENDING, JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.FAILED

    def test_aggregate_status(self):
        P, R, S, F = self.P, self.R, self.S, self.F
        cases = [
            ([P], "pending"),
            ([P, P, P], "pending"),
            ([R], "running"),
            ([P, R], "running"),
            ([S, P], "running"),  # mixed pending and finished
            ([F, P], "running"),
            ([S, R, F], "running"),
            ([S], "succeeded"),
            ([S, S], "succeeded"),
            ([F], "failed"),
            ([F, F], "failed"),
            ([S, F], "partial"),
            ([F, S, S], "partial"),
        ]
        for statuses, expected in cases:
            with self.subTest(statuses=[s.value for s in statuses]):
                self.assertEqual(aggregate_batch_status(statuses), expected)


class SubmitBatchTests(SimpleTestCase):
    def test_one_job_per_file_in_upload_order(self):
        service, _ = _service()
        batch, jobs = service.submit_batch(
            [_item("c.bin"), _item("a.bin"), _item("b.bin")], puid=None, mime_type=None, request_id="req"
        )

        self.assertEqual([j["index"] for j in batch.jobs], [0, 1, 2])
        self.assertEqual([j["filename"] for j in batch.jobs], ["c.bin", "a.bin", "b.bin"])
        self.assertEqual([j["job_id"] for j in batch.jobs], [job.job_id for job in jobs])
        self.assertEqual(len({job.job_id for job in jobs}), 3)

    def test_each_job_is_an_ordinary_job_with_its_own_request_id(self):
        seen = []
        service, _ = _service(extract=lambda **kw: seen.append(kw) or {"ok": True})

        batch, jobs = service.submit_batch(
            [_item(), _item()], puid="fmt/43", mime_type="image/jpeg", request_id="req"
        )

        self.assertEqual([kw["request_id"] for kw in seen], ["req-0", "req-1"])
        self.assertTrue(all(kw["puid"] == "fmt/43" and kw["mime_type"] == "image/jpeg" for kw in seen))
        self.assertTrue(all(kw["object_identifier"] is None for kw in seen))
        for job in jobs:
            self.assertEqual(service.get(job.job_id).status, JobStatus.SUCCEEDED)

    def test_get_batch_returns_the_current_job_states(self):
        executor = DeferredExecutor()
        service, _ = _service(executor=executor)
        batch, _ = service.submit_batch([_item(), _item()], puid=None, mime_type=None, request_id="r")

        _, jobs = service.get_batch(batch.batch_id)
        self.assertEqual([j.status for j in jobs], [JobStatus.PENDING] * 2)

        executor.run_all()
        _, jobs = service.get_batch(batch.batch_id)
        self.assertEqual([j.status for j in jobs], [JobStatus.SUCCEEDED] * 2)

    def test_a_failed_job_does_not_affect_its_siblings(self):
        def extract(**kwargs):
            if kwargs["filename"] == "bad.bin":
                raise RuntimeError("secret")
            return {"ok": True}

        service, _ = _service(extract=extract)
        batch, _ = service.submit_batch(
            [_item("a.bin"), _item("bad.bin"), _item("c.bin")], puid=None, mime_type=None, request_id="r"
        )

        _, jobs = service.get_batch(batch.batch_id)
        self.assertEqual(
            [j.status for j in jobs], [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.SUCCEEDED]
        )
        self.assertEqual(aggregate_batch_status([j.status for j in jobs]), "partial")
        self.assertNotIn("secret", str(jobs[1].errors))

    def test_unknown_batch_is_none(self):
        service, _ = _service()
        self.assertIsNone(service.get_batch("nope"))

    def test_room_for_exactly_n_jobs_is_accepted(self):
        service, _ = _service(max_jobs=3, executor=DeferredExecutor())
        batch, jobs = service.submit_batch(
            [_item(), _item(), _item()], puid=None, mime_type=None, request_id="r"
        )
        self.assertEqual(len(jobs), 3)

    def test_room_for_n_minus_one_creates_no_job_at_all(self):
        executor = DeferredExecutor()
        service, _ = _service(max_jobs=2, executor=executor)
        with self.assertRaises(JobStoreFull):
            service.submit_batch([_item(), _item(), _item()], puid=None, mime_type=None, request_id="r")

        self.assertEqual(executor.pending, [])
        self.assertEqual(service.live_work_dirs(), set())
        self.assertEqual(service.waiting(), (0, 0))
        self.assertEqual(service._store.batches(), [])

    def test_a_submit_failure_marks_the_remaining_jobs_failed_and_removes_their_uploads(self):
        class FlakyExecutor(DeferredExecutor):
            def submit(self, fn, *args, **kwargs):
                if len(self.pending) == 1:
                    raise RuntimeError("pool is shut down")
                return super().submit(fn, *args, **kwargs)

        executor = FlakyExecutor()
        service, _ = _service(executor=executor)
        items = [_item(), _item(), _item()]
        self.addCleanup(lambda: [shutil.rmtree(i["work_dir"], ignore_errors=True) for i in items])

        batch, jobs = service.submit_batch(items, puid=None, mime_type=None, request_id="r")

        executor.run_all()
        _, current = service.get_batch(batch.batch_id)
        self.assertEqual(
            [j.status for j in current], [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.FAILED]
        )
        self.assertFalse(os.path.exists(items[1]["work_dir"]))
        self.assertFalse(os.path.exists(items[2]["work_dir"]))


class BatchExpiryTests(SimpleTestCase):
    """A batch lives or expires as one unit: its record and all its jobs go together."""

    def run_first_job(self, executor):
        fn, args, kwargs = executor.pending.pop(0)
        fn(*args, **kwargs)

    def submit_two(self, executor=None):
        executor = executor or DeferredExecutor()
        service, clock = _service(executor=executor)
        batch, jobs = service.submit_batch([_item(), _item()], puid=None, mime_type=None, request_id="r")
        return service, clock, executor, batch, jobs

    def assertWholeBatchPresent(self, service, batch, jobs):
        self.assertIsNotNone(service.get_batch(batch.batch_id))
        for job in jobs:
            self.assertIsNotNone(service.get(job.job_id))

    def assertWholeBatchGone(self, service, batch, jobs):
        self.assertIsNone(service.get_batch(batch.batch_id))
        for job in jobs:
            self.assertIsNone(service.get(job.job_id))
        self.assertEqual(service._store.batches(), [])
        self.assertEqual(service.live_work_dirs(), set())

    def test_ttl_boundary_just_under_at_and_over(self):
        for offset, gone in ((-1, False), (0, True), (1, True)):
            with self.subTest(offset=offset):
                service, clock, _, batch, jobs = self.submit_two()
                clock.advance(TTL + offset)
                if gone:
                    self.assertWholeBatchGone(service, batch, jobs)
                else:
                    self.assertWholeBatchPresent(service, batch, jobs)

    def test_purge_removes_the_batch_with_all_its_jobs_and_uploads(self):
        service, clock, _, batch, jobs = self.submit_two()
        work_dirs = {job.work_dir for job in jobs}
        clock.advance(TTL)

        removed = service.purge_expired()

        self.assertEqual(removed, 2)
        self.assertTrue(all(not os.path.exists(d) for d in work_dirs))
        self.assertWholeBatchGone(service, batch, jobs)

    def test_a_batch_is_kept_while_it_is_younger_than_the_ttl(self):
        service, clock, _, batch, jobs = self.submit_two()
        clock.advance(TTL - 1)

        self.assertEqual(service.purge_expired(), 0)

        self.assertWholeBatchPresent(service, batch, jobs)

    def test_a_job_that_is_old_enough_does_not_expire_alone(self):
        service, clock, executor, batch, jobs = self.submit_two()
        clock.advance(TTL - 10)
        self.run_first_job(executor)  # the first job finishes at TTL-10
        clock.advance(10)  # the second job is now exactly TTL old, the first is 10 s old

        self.assertWholeBatchPresent(service, batch, jobs)

    def test_the_batch_ages_from_its_last_finished_job(self):
        service, clock, executor, batch, jobs = self.submit_two()
        clock.advance(TTL - 10)
        self.run_first_job(executor)
        finished_at = clock.now

        clock.now = finished_at + timedelta(seconds=TTL - 1)
        self.assertWholeBatchPresent(service, batch, jobs)
        clock.now = finished_at + timedelta(seconds=TTL)
        self.assertWholeBatchGone(service, batch, jobs)

    def test_a_standalone_job_still_expires_on_its_own(self):
        service, clock = _service(executor=DeferredExecutor())
        directory = _item()
        record = service.submit(
            file_path=directory["file_path"], work_dir=directory["work_dir"], filename="a",
            puid=None, mime_type=None, request_id="r", size_bytes=1,
        )
        batch, jobs = service.submit_batch([_item()], puid=None, mime_type=None, request_id="r")
        clock.advance(TTL)

        self.assertIsNone(service.get(record.job_id))
        self.assertIsNone(service.get_batch(batch.batch_id))

    def test_a_batch_that_lost_a_job_is_removed_whole_once_it_is_old_enough(self):
        service, clock, _, batch, jobs = self.submit_two()
        service._store.delete(jobs[0].job_id)  # e.g. its key expired in Redis

        self.assertIsNone(service.get_batch(batch.batch_id))  # never served partially
        clock.advance(TTL)
        service.purge_expired()

        self.assertWholeBatchGone(service, batch, jobs)
