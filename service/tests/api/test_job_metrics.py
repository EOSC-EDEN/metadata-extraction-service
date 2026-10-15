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


class WaitingGaugeTests(SimpleTestCase):
    def test_gauges_track_jobs_waiting_for_a_worker(self):
        executor = DeferredExecutor()
        service, _ = _service(lambda **kw: {}, executor)
        _submit(service, 1000)
        _submit(service, 2500)
        self.assertEqual(_sample("mes_jobs_waiting"), 2)
        self.assertEqual(_sample("mes_jobs_waiting_bytes"), 3500)
        executor.run_all()
        self.assertEqual(_sample("mes_jobs_waiting"), 0)
        self.assertEqual(_sample("mes_jobs_waiting_bytes"), 0)

    def test_a_job_being_executed_is_not_waiting(self):
        seen = []
        executor = DeferredExecutor()
        service, _ = _service(
            lambda **kw: seen.append((_sample("mes_jobs_waiting"), _sample("mes_jobs_waiting_bytes"))) or {},
            executor,
        )
        _submit(service, 10)
        executor.run_all()
        self.assertEqual(seen, [(0.0, 0.0)])

    def test_expiry_of_a_waiting_job_updates_the_gauges(self):
        service, clock = _service(lambda **kw: {}, DeferredExecutor())
        expiring = _submit(service, 100)
        clock.advance(TTL)
        service.get(expiring.job_id)
        self.assertEqual(_sample("mes_jobs_waiting"), 0)
        self.assertEqual(_sample("mes_jobs_waiting_bytes"), 0)


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
