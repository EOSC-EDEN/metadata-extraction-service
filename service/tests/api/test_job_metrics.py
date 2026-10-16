import os
import tempfile
from unittest import mock

from django.test import SimpleTestCase
from prometheus_client import REGISTRY

from apps.api.jobs import InMemoryJobStore, JobService, JobStatus
from tests.api.job_helpers import DeferredExecutor, FakeClock, ImmediateExecutor

TTL = 100


def _sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _requests(status):
    return _sample("mes_extract_requests_total", status=status)


def _service(extract, executor):
    clock = FakeClock()
    return (
        JobService(
            store=InMemoryJobStore(max_jobs=10),
            executor=executor,
            ttl_seconds=TTL,
            clock=clock,
            extract=extract,
        ),
        clock,
    )


def _submit(service, size_bytes):
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "upload.bin")
    open(path, "wb").close()
    return service.submit(
        file_path=path,
        work_dir=directory,
        filename="a.bin",
        puid=None,
        mime_type=None,
        request_id="r",
        size_bytes=size_bytes,
    )


class WaitingJobsTests(SimpleTestCase):
    """`mes_jobs_waiting*` come from `JobService.waiting()` at scrape time (see JobsWaitingCollector)."""

    def test_counts_the_jobs_waiting_for_a_worker_and_their_bytes(self):
        executor = DeferredExecutor()
        service, _ = _service(lambda **kw: {}, executor)
        _submit(service, 1000)
        _submit(service, 2500)
        self.assertEqual(service.waiting(), (2, 3500))
        executor.run_all()
        self.assertEqual(service.waiting(), (0, 0))

    def test_a_job_being_executed_is_not_waiting(self):
        seen = []
        executor = DeferredExecutor()
        holder = {}
        service, _ = _service(lambda **kw: seen.append(holder["service"].waiting()) or {}, executor)
        holder["service"] = service
        _submit(service, 10)
        executor.run_all()
        self.assertEqual(seen, [(0, 0)])

    def test_an_expired_waiting_job_is_not_counted_even_if_nobody_touched_it(self):
        service, clock = _service(lambda **kw: {}, DeferredExecutor())
        _submit(service, 100)
        clock.advance(TTL - 1)
        self.assertEqual(service.waiting(), (1, 100))
        clock.advance(1)
        self.assertEqual(service.waiting(), (0, 0))

    def test_the_waiting_numbers_are_not_per_process_gauges(self):
        # A per-worker gauge would be summed over all gunicorn workers and multiply a shared
        # store's global count; the numbers must only exist as a scrape-time collector.
        service, _ = _service(lambda **kw: {}, DeferredExecutor())
        _submit(service, 5)
        self.assertIsNone(REGISTRY.get_sample_value("mes_jobs_waiting"))
        self.assertIsNone(REGISTRY.get_sample_value("mes_jobs_waiting_bytes"))


class JobRequestCountTests(SimpleTestCase):
    def test_counted_only_once_picked_up_and_finished(self):
        executor = DeferredExecutor()
        results = mock.Mock(results=(), errors=[])
        service, _ = _service(lambda **kw: {}, executor)
        before = {s: _requests(s) for s in ("success", "partial", "failed", "error")}
        _submit(service, 1)
        self.assertEqual({s: _requests(s) for s in before}, before)  # still waiting: not counted
        executor.run_all()
        after = {s: _requests(s) for s in before}
        self.assertEqual(sum(after.values()) - sum(before.values()), 1)

    def test_extraction_error_counts_as_error_and_duration_is_observed(self):
        def boom(**kw):
            raise RuntimeError

        service, _ = _service(boom, ImmediateExecutor())
        errors_before = _requests("error")
        latency_before = _sample("mes_extract_request_duration_seconds_count")
        with self.assertLogs("apps.api.jobs", "ERROR"):
            _submit(service, 1)
        self.assertEqual(_requests("error") - errors_before, 1)
        self.assertEqual(_sample("mes_extract_request_duration_seconds_count") - latency_before, 1)

    def test_expired_unexecuted_job_is_not_counted(self):
        executor = DeferredExecutor()
        service, clock = _service(lambda **kw: {}, executor)
        record = _submit(service, 1)
        clock.advance(TTL)
        service.get(record.job_id)
        before = sum(_requests(s) for s in ("success", "partial", "failed", "error", "rejected"))
        executor.run_all()
        after = sum(_requests(s) for s in ("success", "partial", "failed", "error", "rejected"))
        self.assertEqual(after, before)
