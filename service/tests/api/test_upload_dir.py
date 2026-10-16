"""Uploads are written under `MES_JOB_UPLOAD_DIR` and removed again."""

import os
import subprocess
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.api.jobs import InMemoryJobStore, JobService
from tests.api.job_helpers import DeferredExecutor, FakeClock


def _upload():
    return SimpleUploadedFile("photo.jpg", b"data", content_type="image/jpeg")


def _tool_output():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="[{}]", stderr="")


class UploadDirTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.upload_dir = os.path.join(root.name, "uploads")
        os.makedirs(self.upload_dir)
        override = override_settings(MES_JOB_UPLOAD_DIR=self.upload_dir)
        override.enable()
        self.addCleanup(override.disable)

    def leftovers(self):
        return os.listdir(self.upload_dir)


@mock.patch("apps.extraction.adapters.base.subprocess.run", return_value=_tool_output())
class SyncUploadDirTests(UploadDirTestCase):
    def test_the_tool_reads_the_file_from_the_upload_dir_and_nothing_is_left(self, run):
        response = self.client.post(
            "/api/v1/extract/", {"file": _upload(), "mime_type": "image/jpeg"}, format="multipart"
        )
        self.assertEqual(response.status_code, 200)
        command = run.call_args[0][0]
        self.assertTrue(
            any(part.startswith(self.upload_dir + os.sep) for part in command), command
        )
        self.assertEqual(self.leftovers(), [])


@mock.patch("apps.extraction.adapters.base.subprocess.run", return_value=_tool_output())
class JobUploadDirTests(UploadDirTestCase):
    def _service(self, max_jobs=10):
        self.executor = DeferredExecutor()
        service = JobService(
            store=InMemoryJobStore(max_jobs=max_jobs),
            executor=self.executor,
            ttl_seconds=100,
            clock=FakeClock(),
        )
        patcher = mock.patch("apps.api.views.get_job_service", return_value=service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _submit(self):
        return self.client.post(
            "/api/v1/jobs/", {"file": _upload(), "mime_type": "image/jpeg"}, format="multipart"
        )

    def test_a_waiting_job_keeps_its_upload_in_the_upload_dir_until_it_finishes(self, _run):
        self._service()
        self.assertEqual(self._submit().status_code, 202)
        self.assertEqual(len(self.leftovers()), 1)
        self.executor.run_all()
        self.assertEqual(self.leftovers(), [])

    def test_a_rejected_job_leaves_no_upload_behind(self, _run):
        self._service(max_jobs=1)
        self._submit()
        self.assertEqual(self._submit().status_code, 503)
        self.assertEqual(len(self.leftovers()), 1)  # only the accepted job's upload
