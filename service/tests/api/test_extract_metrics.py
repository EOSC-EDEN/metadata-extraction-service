import subprocess
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from prometheus_client import REGISTRY
from rest_framework.test import APIClient


def _sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _upload(content=b"data", name="photo.jpg"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


def _ok(*_args, **_kwargs):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout='[{"a": 1}]', stderr="")


class ExtractMetricsTests(TestCase):
    def setUp(self):
        self.client = APIClient(raise_request_exception=False)
        self.url = "/api/v1/extract/"

    def _delta(self, name, before, **labels):
        return _sample(name, **labels) - before

    @mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
    def test_successful_request_is_counted_with_size_and_tool_run(self, _run):
        requests_before = _sample("mes_extract_requests_total", status="success")
        bytes_before = _sample("mes_uploaded_bytes_total")
        sizes_before = _sample("mes_uploaded_file_bytes_count")
        tools_before = _sample("mes_tool_runs_total", tool="exiftool", status="success")
        duration_before = _sample("mes_tool_duration_seconds_count", tool="exiftool")
        latency_before = _sample("mes_extract_request_duration_seconds_count")

        response = self.client.post(self.url, {"file": _upload(b"x" * 1234)}, format="multipart")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._delta("mes_extract_requests_total", requests_before, status="success"), 1)
        self.assertEqual(self._delta("mes_uploaded_bytes_total", bytes_before), 1234)
        self.assertEqual(self._delta("mes_uploaded_file_bytes_count", sizes_before), 1)
        self.assertEqual(
            self._delta("mes_tool_runs_total", tools_before, tool="exiftool", status="success"), 1
        )
        self.assertEqual(self._delta("mes_tool_duration_seconds_count", duration_before, tool="exiftool"), 1)
        self.assertEqual(self._delta("mes_extract_request_duration_seconds_count", latency_before), 1)

    @mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
    def test_one_byte_file_is_counted(self, _run):
        bytes_before = _sample("mes_uploaded_bytes_total")

        self.client.post(self.url, {"file": _upload(b"x")}, format="multipart")

        self.assertEqual(self._delta("mes_uploaded_bytes_total", bytes_before), 1)

    def test_empty_file_is_rejected_and_adds_no_bytes(self):
        rejected_before = _sample("mes_extract_requests_total", status="rejected")
        bytes_before = _sample("mes_uploaded_bytes_total")
        sizes_before = _sample("mes_uploaded_file_bytes_count")

        response = self.client.post(self.url, {"file": _upload(b"")}, format="multipart")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._delta("mes_extract_requests_total", rejected_before, status="rejected"), 1)
        self.assertEqual(self._delta("mes_uploaded_bytes_total", bytes_before), 0)
        self.assertEqual(self._delta("mes_uploaded_file_bytes_count", sizes_before), 0)

    def test_missing_file_is_rejected(self):
        rejected_before = _sample("mes_extract_requests_total", status="rejected")

        self.client.post(self.url, {}, format="multipart")

        self.assertEqual(self._delta("mes_extract_requests_total", rejected_before, status="rejected"), 1)

    @mock.patch(
        "apps.extraction.adapters.base.subprocess.run", side_effect=FileNotFoundError("nope")
    )
    def test_tool_failure_counts_request_as_failed(self, _run):
        failed_before = _sample("mes_extract_requests_total", status="failed")
        tool_before = _sample("mes_tool_runs_total", tool="exiftool", status="failure")

        response = self.client.post(self.url, {"file": _upload()}, format="multipart")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._delta("mes_extract_requests_total", failed_before, status="failed"), 1)
        self.assertEqual(
            self._delta("mes_tool_runs_total", tool_before, tool="exiftool", status="failure"),
            1,
        )

    @mock.patch(
        "apps.api.views.ExtractionOrchestrator.extract", side_effect=RuntimeError("boom")
    )
    def test_unexpected_error_is_counted_as_error(self, _extract):
        error_before = _sample("mes_extract_requests_total", status="error")
        bytes_before = _sample("mes_uploaded_bytes_total")

        response = self.client.post(self.url, {"file": _upload(b"abc")}, format="multipart")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._delta("mes_extract_requests_total", error_before, status="error"), 1)
        # The file was received, so its size still counts towards what was uploaded.
        self.assertEqual(self._delta("mes_uploaded_bytes_total", bytes_before), 3)
