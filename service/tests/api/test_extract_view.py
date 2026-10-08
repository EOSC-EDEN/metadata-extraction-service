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
        self.assertIn("file", response.json())

    def test_empty_file_is_400(self):
        response = self.client.post(self.url, {"file": _upload(b"")}, format="multipart")
        self.assertEqual(response.status_code, 400)

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
