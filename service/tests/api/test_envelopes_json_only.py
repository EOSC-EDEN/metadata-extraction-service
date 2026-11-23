"""The job and job-batch envelopes (job_id, status, ...) are JSON only; XML is only for results."""

import subprocess
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from tests.api import test_jobs_view

XML_ACCEPTS = ["application/xml", "application/premis+xml", "text/xml", "application/xml;q=1.0, application/premis+xml"]
JSON_ACCEPTS = [None, "application/json", "*/*", "application/json, application/xml;q=0.5"]


@mock.patch(
    "apps.extraction.adapters.base.subprocess.run",
    return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr=""),
)
class EnvelopesAreJsonOnlyTests(test_jobs_view.JobsViewTestCase):
    max_jobs = 1000  # every subtest submits jobs

    def setUp(self):
        super().setUp()
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        override = override_settings(MES_JOB_UPLOAD_DIR=uploads.name)
        override.enable()
        self.addCleanup(override.disable)

    def _file(self, name="a.jpg"):
        return SimpleUploadedFile(name, b"data", content_type="image/jpeg")

    def _routes(self):
        """(label, call(accept)) for the four envelope routes, each on a job that exists."""
        job_id = self.submit().json()["job_id"]
        batch = self.client.post(
            "/api/v1/jobs/batch/", {"files": [self._file("a.jpg"), self._file("b.jpg")], "mime_type": "image/jpeg"}, format="multipart"
        )
        extra = lambda accept: {} if accept is None else {"HTTP_ACCEPT": accept}  # noqa: E731
        return [
            ("submit", lambda a: self.client.post("/api/v1/jobs/", {"file": self._file(), "mime_type": "image/jpeg"}, format="multipart", **extra(a))),
            ("status", lambda a: self.client.get(f"/api/v1/jobs/{job_id}/", **extra(a))),
            (
                "batch submit",
                lambda a: self.client.post(
                    "/api/v1/jobs/batch/", {"files": [self._file("c.jpg"), self._file("d.jpg")], "mime_type": "image/jpeg"}, format="multipart", **extra(a)
                ),
            ),
            ("batch status", lambda a: self.client.get(batch["Location"], **extra(a))),
        ]

    def test_json_is_the_answer_whenever_json_is_acceptable(self, run):
        for label, call in self._routes():
            for accept in JSON_ACCEPTS:
                with self.subTest(route=label, accept=accept):
                    response = call(accept)
                    self.assertIn(response.status_code, (200, 202))
                    self.assertIn("application/json", response["Content-Type"])
                    response.json()

    def test_xml_is_not_acceptable(self, run):
        for label, call in self._routes():
            for accept in XML_ACCEPTS:
                with self.subTest(route=label, accept=accept):
                    response = call(accept)
                    self.assertEqual(response.status_code, 406)
                    self.assertIn("application/json", response["Content-Type"])  # the error itself is JSON

    def test_a_406_queues_nothing(self, run):
        before = self.client.get("/api/v1/jobs/").status_code  # the route has no GET: 405, no listing exists
        self.assertEqual(before, 405)
        response = self.client.post(
            "/api/v1/jobs/", {"file": self._file(), "mime_type": "image/jpeg"}, format="multipart", HTTP_ACCEPT="application/xml"
        )
        self.assertEqual(response.status_code, 406)
        run.assert_not_called()

    def test_the_xml_format_parameters_are_not_available(self, run):
        job_id = self.submit().json()["job_id"]
        for fmt in ("xml", "premis"):
            with self.subTest(format=fmt):
                response = self.client.get(f"/api/v1/jobs/{job_id}/?format={fmt}")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("xml", response["Content-Type"])

    def test_an_unknown_job_with_xml_requested_is_still_json(self, run):
        for accept in (None, "application/xml"):
            with self.subTest(accept=accept):
                extra = {} if accept is None else {"HTTP_ACCEPT": accept}
                response = self.client.get("/api/v1/jobs/nope/", **extra)
                self.assertIn(response.status_code, (404, 406))
                self.assertIn("application/json", response["Content-Type"])

    def test_the_result_route_answers_premis_xml_but_not_application_xml(self, run):
        job_id = self.submit().json()["job_id"]
        premis = self.client.get(f"/api/v1/jobs/{job_id}/result/", HTTP_ACCEPT="application/premis+xml")
        self.assertEqual(premis.status_code, 200)
        self.assertIn(b"<premis:premis", premis.content)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}/result/", HTTP_ACCEPT="application/xml").status_code, 406)
