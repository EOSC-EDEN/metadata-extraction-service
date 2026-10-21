import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from jsonschema import Draft202012Validator, FormatChecker
from lxml import etree

from apps.api.renderers import JOB_BATCH_NAMESPACE, MESXMLRenderer
from tests.api import test_jobs_view

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "apps" / "api" / "schema"
NS = {"jb": JOB_BATCH_NAMESPACE}
STATUSES = ["pending", "running", "succeeded", "failed", "partial"]


def _envelope(**overrides):
    data = {
        "batch_id": "b-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "pending",
        "jobs": [
            {"index": 0, "filename": "a.jpg", "job_id": "j-0", "status": "pending"},
            {"index": 1, "filename": "b.jpg", "job_id": "j-1", "status": "pending"},
        ],
    }
    data.update(overrides)
    return data


class JobBatchSchemaTests(SimpleTestCase):
    def setUp(self):
        self.schema = json.loads((SCHEMA_DIR / "mes-job-batch.schema.json").read_text())
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())
        self.xsd = etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "mes-job-batch.xsd")))

    def test_json_schema_is_valid_and_accepts_every_aggregate_status(self):
        Draft202012Validator.check_schema(self.schema)
        for status in STATUSES:
            with self.subTest(status=status):
                self.validator.validate(_envelope(status=status))

    def test_json_schema_rejects_unknown_status_and_missing_fields(self):
        self.assertFalse(self.validator.is_valid(_envelope(status="done")))
        self.assertFalse(
            self.validator.is_valid(
                _envelope(jobs=[{"index": 0, "filename": "a", "job_id": "j", "status": "partial"}])
            )
        )  # `partial` is a batch status, not a job status
        for field in ("batch_id", "created_at", "status", "jobs"):
            data = _envelope()
            del data[field]
            with self.subTest(field=field):
                self.assertFalse(self.validator.is_valid(data))
        for field in ("index", "filename", "job_id", "status"):
            data = _envelope()
            del data["jobs"][0][field]
            with self.subTest(job_field=field):
                self.assertFalse(self.validator.is_valid(data))

    def test_xml_validates_for_every_aggregate_status(self):
        for status in STATUSES:
            with self.subTest(status=status):
                xml = etree.fromstring(MESXMLRenderer().render(_envelope(status=status)))
                self.assertTrue(self.xsd.validate(xml), self.xsd.error_log)

    def test_xml_carries_the_same_content(self):
        xml = etree.fromstring(MESXMLRenderer().render(_envelope(status="running")))

        self.assertEqual(etree.QName(xml).localname, "jobBatch")
        self.assertEqual(xml.find("jb:batchId", NS).text, "b-1")
        self.assertEqual(xml.find("jb:status", NS).text, "running")
        jobs = xml.findall("jb:jobs/jb:job", NS)
        self.assertEqual([j.find("jb:index", NS).text for j in jobs], ["0", "1"])
        self.assertEqual([j.find("jb:filename", NS).text for j in jobs], ["a.jpg", "b.jpg"])
        self.assertEqual([j.find("jb:jobId", NS).text for j in jobs], ["j-0", "j-1"])

    def test_xml_encodes_a_filename_xml_cannot_carry(self):
        data = _envelope(jobs=[{"index": 0, "filename": "a\x01.jpg", "job_id": "j", "status": "pending"}])

        xml = etree.fromstring(MESXMLRenderer().render(data))

        self.assertTrue(self.xsd.validate(xml), self.xsd.error_log)
        self.assertEqual(xml.find("jb:jobs/jb:job/jb:filename", NS).get("encoding"), "base64")


@mock.patch(
    "apps.extraction.adapters.base.subprocess.run",
    return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr=""),
)
class JobBatchResponseSchemaTests(test_jobs_view.JobsViewTestCase):
    def setUp(self):
        super().setUp()
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        override = override_settings(MES_JOB_UPLOAD_DIR=uploads.name)
        override.enable()
        self.addCleanup(override.disable)
        self.schema = json.loads((SCHEMA_DIR / "mes-job-batch.schema.json").read_text())
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())
        self.xsd = etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "mes-job-batch.xsd")))

    def _submit(self, **extra):
        files = [SimpleUploadedFile(f"{n}.jpg", b"data", content_type="image/jpeg") for n in range(2)]
        return self.client.post(
            "/api/v1/jobs/batch/", {"files": files, "mime_type": "image/jpeg"}, format="multipart", **extra
        )

    def test_json_responses_validate(self, run):
        submitted = self._submit()
        self.validator.validate(submitted.json())
        self.validator.validate(self.client.get(submitted["Location"]).json())

    def test_xml_responses_validate(self, run):
        submitted = self._submit(HTTP_ACCEPT="application/xml")

        self.assertEqual(submitted.status_code, 202)
        self.assertTrue(self.xsd.validate(etree.fromstring(submitted.content)), self.xsd.error_log)
        status = self.client.get(submitted["Location"], HTTP_ACCEPT="application/xml")
        xml = etree.fromstring(status.content)
        self.assertTrue(self.xsd.validate(xml), self.xsd.error_log)
        self.assertEqual(xml.find("jb:status", NS).text, "succeeded")

    def test_the_single_job_envelope_is_still_rendered_as_a_job(self, run):
        response = self.client.post(
            "/api/v1/jobs/",
            {"file": SimpleUploadedFile("a.jpg", b"data"), "mime_type": "image/jpeg"},
            format="multipart",
            HTTP_ACCEPT="application/xml",
        )

        self.assertEqual(etree.QName(etree.fromstring(response.content)).localname, "job")
