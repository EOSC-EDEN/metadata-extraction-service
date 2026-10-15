import os
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from apps.api.jobs import InMemoryJobStore, JobService
from tests.api.job_helpers import DeferredExecutor, FakeClock


class MetricsViewTests(TestCase):
    def test_metrics_endpoint_returns_prometheus_text(self):
        response = self.client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        body = response.content.decode()
        self.assertIn("mes_extract_requests_total", body)
        self.assertIn("mes_uploaded_bytes_total", body)

    def test_metrics_endpoint_is_get_only(self):
        self.assertEqual(self.client.post("/metrics").status_code, 405)


class JobsWaitingAtScrapeTests(TestCase):
    """`mes_jobs_waiting*` describe the moment of the scrape, not the last job event."""

    def _service(self, executor, clock):
        return JobService(
            store=InMemoryJobStore(max_jobs=10),
            executor=executor,
            ttl_seconds=100,
            clock=clock,
            extract=lambda **kwargs: {},
        )

    def _scrape(self, service):
        with mock.patch("apps.metrics.views.get_job_service", return_value=service):
            body = self.client.get("/metrics").content.decode()
        values = dict(
            line.split(" ")
            for line in body.splitlines()
            if line.startswith(("mes_jobs_waiting ", "mes_jobs_waiting_bytes "))
        )
        return float(values["mes_jobs_waiting"]), float(values["mes_jobs_waiting_bytes"])

    def _submit(self, service, size):
        directory = tempfile.mkdtemp()
        return service.submit(
            file_path=os.path.join(directory, "u"), work_dir=directory, filename="a", puid=None,
            mime_type=None, request_id="r", size_bytes=size,
        )

    def test_reports_the_jobs_waiting_right_now(self):
        service = self._service(DeferredExecutor(), FakeClock())
        self._submit(service, 10)
        self._submit(service, 20)
        self.assertEqual(self._scrape(service), (2.0, 30.0))

    def test_a_waiting_job_that_expired_is_not_reported_even_if_nobody_touched_it(self):
        clock = FakeClock()
        service = self._service(DeferredExecutor(), clock)
        self._submit(service, 10)
        clock.advance(99)
        self.assertEqual(self._scrape(service), (1.0, 10.0))
        clock.advance(1)  # the job reaches the TTL; no request has accessed it
        self.assertEqual(self._scrape(service), (0.0, 0.0))

    @override_settings(MES_ASYNC_ENABLED=False)
    def test_scrape_does_not_touch_the_job_service_when_async_is_disabled(self):
        with mock.patch("apps.metrics.views.get_job_service") as get_service:
            self.assertEqual(self.client.get("/metrics").status_code, 200)
        get_service.assert_not_called()
