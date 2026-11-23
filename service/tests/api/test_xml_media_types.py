"""XML is only `application/premis+xml`, only on the result routes (`/extract/`, `/jobs/{id}/result/`); `application/xml` is a 406."""

import subprocess
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from lxml import etree
from rest_framework.test import APIClient

from apps.api.renderers import PREMIS_NAMESPACE
from tests.api import test_jobs_view
from tests.api.job_helpers import DeferredExecutor
from tests.api.premis_schema import premis_xsd
from tests.api.test_result_schemas import SCHEMA_DIR

PREMIS = "application/premis+xml"
NOT_ACCEPTABLE = ["application/xml", "text/xml", "application/xml;q=1.0, text/xml;q=0.9"]
MES = {"p": PREMIS_NAMESPACE}


def _file(name="a.jpg"):
    return SimpleUploadedFile(name, b"data", content_type="image/jpeg")


def _ok(stdout="<r/>"):
    return mock.patch(
        "apps.extraction.adapters.base.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=""),
    )


class ResultRoutesTests(test_jobs_view.JobsViewTestCase):
    max_jobs = 1000

    def setUp(self):
        super().setUp()
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        override = override_settings(MES_JOB_UPLOAD_DIR=uploads.name)
        override.enable()
        self.addCleanup(override.disable)

    def _routes(self):
        with _ok():
            job_id = self.submit().json()["job_id"]
        return {
            "extract": lambda **kw: self.client.post("/api/v1/extract/", {"file": _file(), "mime_type": "image/jpeg"}, format="multipart", **kw),
            "job result": lambda **kw: self.client.get(f"/api/v1/jobs/{job_id}/result/", **kw),
        }

    def test_application_xml_is_not_acceptable(self):
        for label, call in self._routes().items():
            for accept in NOT_ACCEPTABLE:
                with self.subTest(route=label, accept=accept), _ok():
                    response = call(HTTP_ACCEPT=accept)
                    self.assertEqual(response.status_code, 406)
                    self.assertIn("application/json", response["Content-Type"])
                    self.assertIn("errors", response.json())

    def test_premis_xml_is_the_xml_form(self):
        for label, call in self._routes().items():
            with self.subTest(route=label, how="accept"), _ok():
                response = call(HTTP_ACCEPT=PREMIS)
                self.assertEqual(response.status_code, 200)
                self.assertIn(PREMIS, response["Content-Type"])
                self.assertEqual(etree.QName(etree.fromstring(response.content)).text, f"{{{PREMIS_NAMESPACE}}}premis")
            with self.subTest(route=label, how="format"), _ok():
                response = call(**{"QUERY_STRING": "format=premis"})
                self.assertEqual(response.status_code, 200)
                self.assertIn(PREMIS, response["Content-Type"])

    def test_json_stays_the_default(self):
        for label, call in self._routes().items():
            for accept in (None, "*/*", "application/json", "application/json, application/premis+xml;q=0.5"):
                with self.subTest(route=label, accept=accept), _ok():
                    response = call(**({} if accept is None else {"HTTP_ACCEPT": accept}))
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("application/json", response["Content-Type"])

    def test_the_old_xml_format_parameter_is_gone(self):
        for label, call in self._routes().items():
            with self.subTest(route=label), _ok():
                response = call(**{"QUERY_STRING": "format=xml"})
                self.assertEqual(response.status_code, 404)

    def test_errors_are_json_even_when_premis_xml_was_asked_for(self):
        with _ok():
            response = self.client.post("/api/v1/extract/", {}, format="multipart", HTTP_ACCEPT=PREMIS)
        self.assertEqual(response.status_code, 400)
        self.assertIn("application/json", response["Content-Type"])
        self.assertIn("errors", response.json())
        missing = self.client.get("/api/v1/jobs/nope/result/", HTTP_ACCEPT=PREMIS)
        self.assertEqual(missing.status_code, 404)
        self.assertIn("application/json", missing["Content-Type"])
        self.assertIn("errors", missing.json())


class UnfinishedJobTests(test_jobs_view.JobsViewTestCase):
    executor_class = DeferredExecutor

    def test_a_job_that_has_not_finished_is_a_json_409_whatever_xml_was_asked_for(self):
        with _ok():
            job_id = self.submit().json()["job_id"]
        for accept in (PREMIS, "application/xml"):
            with self.subTest(accept=accept):
                response = self.client.get(f"/api/v1/jobs/{job_id}/result/", HTTP_ACCEPT=accept)
                self.assertIn(response.status_code, (409, 406))
                self.assertIn("application/json", response["Content-Type"])
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}/result/", HTTP_ACCEPT=PREMIS).status_code, 409)


class OtherRoutesAreJsonOnlyTests(TestCase):
    def test_tools_limits_and_local_rules_answer_406_for_any_xml(self):
        for url in ("/api/v1/tools/", "/api/v1/limits/", "/api/v1/local-rules/"):
            for accept in ("application/xml", PREMIS, "text/xml"):
                with self.subTest(url=url, accept=accept):
                    self.assertEqual(APIClient().get(url, HTTP_ACCEPT=accept).status_code, 406)
            with self.subTest(url=url, accept="json"):
                self.assertEqual(APIClient().get(url).status_code, 200)


class RawOutputElementTests(TestCase):
    """`rawOutput` inside a PREMIS extension is an element without a namespace (as the reviewer's `ffprobe`)."""

    def _document(self, stdout, failure=None, **query):
        outcome = {"side_effect": failure} if failure else {
            "return_value": subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
        }
        with mock.patch("apps.extraction.adapters.base.subprocess.run", **outcome):
            response = APIClient().post(
                "/api/v1/extract/?" + "&".join(f"{k}={v}" for k, v in query.items()),
                {"file": _file(), "mime_type": "image/jpeg"},
                format="multipart",
                HTTP_ACCEPT=PREMIS,
            )
        self.assertEqual(response.status_code, 200)
        return etree.fromstring(response.content)

    def test_a_failed_tool_has_error_notes_and_no_error_element(self):
        root = self._document("", failure=subprocess.CalledProcessError(1, ["exiftool"], stderr="Unable to parse the file"))
        notes = [n.text for n in root.findall(".//p:eventOutcomeDetailNote", MES)]
        self.assertTrue(any(n.startswith("error: ") for n in notes))
        self.assertTrue(any(n.startswith("error type: ") for n in notes))
        self.assertEqual(root.findall(".//errors"), [])

    def test_the_element_has_no_namespace_and_the_document_stays_valid(self):
        schema = premis_xsd()
        for query in ({}, {"raw": "exact"}):
            with self.subTest(query=query):
                root = self._document("not xml {", **query)
                elements = root.findall(".//p:eventOutcomeDetailExtension/rawOutput", MES)
                self.assertTrue(elements)
                for element in elements:
                    self.assertIsNone(etree.QName(element).namespace)
                self.assertTrue(schema.validate(root), schema.error_log)

    def test_the_document_does_not_declare_the_mes_namespace_any_more(self):
        response = APIClient().post(
            "/api/v1/extract/", {"file": _file(), "mime_type": "image/jpeg"}, format="multipart", HTTP_ACCEPT=PREMIS
        )
        self.assertNotIn(b"urn:eden:mes:result:1", response.content)
        self.assertNotIn(b"xmlns:mes", response.content)

    def test_there_is_no_extensions_schema_any_more(self):
        self.assertFalse((SCHEMA_DIR / "mes-premis-extensions.xsd").exists())
        self.assertEqual(APIClient().get("/api/v1/schema/premis-extensions.xsd").status_code, 404)
        for name in ("mes-result.xsd", "mes-batch.xsd", "mes-batch.schema.json", "premis.xsd"):
            with self.subTest(name=name):
                self.assertFalse((SCHEMA_DIR / name).exists())
