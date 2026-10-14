import subprocess
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient



def _upload(content=b"data", name="photo.jpg"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


class ExtractViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/extract/"

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_extracts_with_default_tool(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='[{"a": 1}]', stderr=""
        )

        response = self.client.post(
            self.url, {"file": _upload(), "mime_type": "image/jpeg"}, format="multipart"
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["filename"], "photo.jpg")
        self.assertEqual(body["tool_resolution"]["mime_type"], "image/jpeg")
        self.assertEqual(
            body["tool_resolution"]["selection_method"], "default_tool_caller_identified"
        )
        self.assertEqual(body["results"][0]["tool"], "exiftool")
        self.assertEqual(body["results"][0]["status"], "success")
        self.assertEqual(body["results"][0]["raw_output"], '[{"a": 1}]')
        self.assertEqual(body["errors"], [])
        self.assertIn("request_id", body)

    def test_missing_file_is_400(self):
        response = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertTrue(any(e.startswith("file:") for e in body["errors"]))
        self.assertTrue(body["request_id"])

    def test_empty_file_is_400(self):
        response = self.client.post(self.url, {"file": _upload(b"")}, format="multipart")
        self.assertEqual(response.status_code, 400)

    @override_settings(MES_MAX_UPLOAD_BYTES=10)
    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_upload_of_exactly_the_limit_is_accepted(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        response = self.client.post(self.url, {"file": _upload(b"x" * 10)}, format="multipart")
        self.assertEqual(response.status_code, 200)

    @override_settings(MES_MAX_UPLOAD_BYTES=10)
    @mock.patch("apps.extraction.services.orchestrator.ExtractionOrchestrator.extract")
    def test_upload_one_byte_over_the_limit_is_413_and_runs_no_tool(self, mock_extract):
        response = self.client.post(self.url, {"file": _upload(b"x" * 11)}, format="multipart")
        self.assertEqual(response.status_code, 413)
        body = response.json()
        self.assertIn("exceeds the maximum of 10 bytes", body["errors"][0])
        self.assertTrue(body["request_id"])
        mock_extract.assert_not_called()

    @override_settings(MES_MAX_UPLOAD_BYTES=10)
    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_one_byte_upload_is_accepted(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        response = self.client.post(self.url, {"file": _upload(b"x")}, format="multipart")
        self.assertEqual(response.status_code, 200)

    @override_settings(MES_DEFAULT_TOOL="")
    def test_no_default_tool_is_503(self):
        response = self.client.post(self.url, {"file": _upload()}, format="multipart")
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["errors"])

    @mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=FileNotFoundError("nope"))
    def test_tool_failure_still_returns_200_with_failure_status(self, _run):
        response = self.client.post(self.url, {"file": _upload()}, format="multipart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["status"], "failure")

    @mock.patch(
        "apps.api.views.ExtractionOrchestrator.extract", side_effect=RuntimeError("secret detail")
    )
    def test_unexpected_error_is_json_500_without_internals(self, _extract):
        client = APIClient(raise_request_exception=False)
        response = client.post(self.url, {"file": _upload()}, format="multipart")
        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.json()["errors"])
        self.assertNotIn("secret detail", response.content.decode())
