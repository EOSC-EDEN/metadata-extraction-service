"""MES-REQ-5-001: errors and warnings reach the caller and the logs."""

import subprocess
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.extraction.exceptions import FFISUnavailable


def _upload():
    return SimpleUploadedFile("photo.jpg", b"data", content_type="image/jpeg")


class ErrorAndWarningSurfacingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/extract/"

    def _post(self, **extra):
        return self.client.post(self.url, {"file": _upload(), **extra}, format="multipart")

    @mock.patch(
        "apps.extraction.adapters.base.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="exiftool", timeout=1),
    )
    def test_tool_timeout_is_reported_per_tool_and_top_level(self, _run):
        body = self._post(mime_type="image/jpeg").json()
        self.assertEqual(body["results"][0]["status"], "timeout")
        self.assertTrue(body["results"][0]["errors"])
        self.assertTrue(any("timeout" in w for w in body["warnings"]))

    @mock.patch(
        "apps.extraction.adapters.base.subprocess.run", side_effect=FileNotFoundError("nope")
    )
    def test_mandatory_tool_failure_is_a_top_level_error(self, _run):
        tools = {k: {**v, "mandatory": True} for k, v in settings.MES_TOOLS.items()}
        with override_settings(MES_TOOLS=tools):
            body = self._post(mime_type="image/jpeg").json()
        self.assertTrue(any("Mandatory tool 'exiftool'" in e for e in body["errors"]))

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_tool_stderr_becomes_a_warning(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[{}]", stderr="Warning: minor problem"
        )
        body = self._post(mime_type="image/jpeg").json()
        self.assertEqual(body["results"][0]["warnings"], ["Warning: minor problem"])

    @override_settings(MES_FFIS_ENABLED=True, MES_FFIS_BASE_URL="http://ffis.invalid")
    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    @mock.patch(
        "apps.extraction.services.tool_resolution.FFISClient.identify",
        side_effect=FFISUnavailable("down"),
    )
    def test_ffis_unreachable_warning_reaches_response(self, _identify, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[{}]", stderr=""
        )
        body = self._post().json()
        self.assertTrue(any(w.startswith("ffis_unreachable") for w in body["warnings"]))

    @mock.patch(
        "apps.extraction.adapters.base.subprocess.run", side_effect=FileNotFoundError("nope")
    )
    def test_errors_are_logged_with_request_id(self, _run):
        with self.assertLogs("apps.extraction.services.orchestrator", level="WARNING") as logs:
            body = self._post(mime_type="image/jpeg").json()
        self.assertTrue(all(r.request_id == body["request_id"] for r in logs.records))
        self.assertTrue(any(r.levelname == "ERROR" for r in logs.records))

    def test_validation_error_is_xml_when_requested(self):
        response = self.client.post(self.url, {}, format="multipart", HTTP_ACCEPT="application/xml")
        self.assertEqual(response.status_code, 400)
        self.assertIn("application/xml", response["Content-Type"])
        self.assertIn(b"errors", response.content)

    @mock.patch(
        "apps.api.views.ExtractionOrchestrator.extract", side_effect=RuntimeError("secret detail")
    )
    def test_unexpected_error_includes_request_id(self, _extract):
        client = APIClient(raise_request_exception=False)
        response = client.post(self.url, {"file": _upload()}, format="multipart")
        self.assertTrue(response.json()["request_id"])
