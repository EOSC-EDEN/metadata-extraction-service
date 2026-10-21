import json
import subprocess
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from jsonschema import Draft202012Validator, FormatChecker
from lxml import etree
from referencing import Registry, Resource
from rest_framework.test import APIClient

from apps.api import views
from apps.api.renderers import BATCH_NAMESPACE, NAMESPACE, MESXMLRenderer

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "apps" / "api" / "schema"
NS = {"b": BATCH_NAMESPACE, "m": NAMESPACE}


def _load(name):
    return json.loads((SCHEMA_DIR / name).read_text())


def _json_validator():
    result = _load("mes-result.schema.json")
    registry = Registry().with_resource(result["$id"], Resource.from_contents(result))
    return Draft202012Validator(
        _load("mes-batch.schema.json"), registry=registry, format_checker=FormatChecker()
    )


def _xsd():
    return etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "mes-batch.xsd")))


def _upload(content=b"data", name="photo.jpg"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout='[{"a": 1}]', stderr="")


class BatchSchemaTests(SimpleTestCase):
    def test_json_schema_is_valid(self):
        Draft202012Validator.check_schema(_load("mes-batch.schema.json"))

    def test_xsd_is_loadable(self):
        _xsd()

    def test_json_schema_rejects_malformed_documents(self):
        validator = _json_validator()
        bad = [
            {},
            {"results": [{"index": 0, "filename": "a"}]},  # neither result nor errors
            {"results": [{"index": 0, "filename": "a", "errors": [], "result": {}}]},  # both
            {"results": [{"index": -1, "filename": "a", "errors": ["x"]}]},
            {"results": [{"filename": "a", "errors": ["x"]}]},
            {"results": [{"index": 0, "filename": "a", "errors": ["x"]}], "extra": 1},
        ]
        for document in bad:
            with self.subTest(document=document):
                self.assertFalse(validator.is_valid(document))

    def test_json_schema_accepts_an_error_entry(self):
        _json_validator().validate({"results": [{"index": 0, "filename": "a", "errors": ["x"]}]})

    def test_xml_error_entry_validates_and_keeps_the_text(self):
        data = {"results": [{"index": 3, "filename": "a\x00.jpg", "errors": ["boom \x01"]}]}

        xml = etree.fromstring(MESXMLRenderer().render(data))

        self.assertTrue(_xsd().validate(xml), _xsd().error_log)
        entry = xml.find("b:entry", NS)
        self.assertEqual(entry.find("b:index", NS).text, "3")
        self.assertEqual(entry.find("b:filename", NS).get("encoding"), "base64")
        self.assertEqual(entry.find("b:errors/m:item", NS).get("encoding"), "base64")


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class BatchResponseSchemaTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _post(self, files, **extra):
        return self.client.post(
            "/api/v1/extract/batch/",
            {"files": files, "mime_type": "image/jpeg"},
            format="multipart",
            **extra,
        )

    def _post_with_failing_second_save(self, **extra):
        real_save = views.save_upload
        calls = []

        def save(upload, directory, *args, **kwargs):
            calls.append(upload.name)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_save(upload, directory, *args, **kwargs)

        files = [_upload(name="a.jpg"), _upload(name="b.jpg"), _upload(name="c.jpg")]
        with mock.patch("apps.api.views.save_upload", side_effect=save):
            return self._post(files, **extra)

    def test_json_response_validates(self, run):
        response = self._post_with_failing_second_save()

        self.assertEqual(response.status_code, 200)
        document = response.json()
        _json_validator().validate(document)
        self.assertEqual(["result" in e for e in document["results"]], [True, False, True])

    def test_xml_response_validates_and_wraps_the_existing_result(self, run):
        response = self._post_with_failing_second_save(HTTP_ACCEPT="application/xml")

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/xml", response["Content-Type"])
        xml = etree.fromstring(response.content)
        self.assertTrue(_xsd().validate(xml), _xsd().error_log)
        self.assertEqual(etree.QName(xml).localname, "batch")
        first, second, third = xml.findall("b:entry", NS)
        self.assertEqual(first.find("b:index", NS).text, "0")
        self.assertEqual(first.find("m:extractionResult/m:filename", NS).text, "a.jpg")
        self.assertIsNone(first.find("b:errors", NS))
        self.assertIsNone(second.find("m:extractionResult", NS))
        self.assertTrue(second.find("b:errors/m:item", NS).text)
        self.assertIsNotNone(third.find("m:extractionResult", NS))

    def test_the_embedded_result_still_validates_against_the_unchanged_result_xsd(self, run):
        response = self._post([_upload()], HTTP_ACCEPT="application/xml")

        xml = etree.fromstring(response.content)
        result = xml.find("b:entry/m:extractionResult", NS)
        result_xsd = etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "mes-result.xsd")))
        self.assertTrue(result_xsd.validate(etree.fromstring(etree.tostring(result))), result_xsd.error_log)

    def test_a_rejected_request_is_still_the_plain_error_document(self, run):
        response = self.client.post("/api/v1/extract/batch/", {}, format="multipart", HTTP_ACCEPT="application/xml")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(etree.QName(etree.fromstring(response.content)).localname, "error")
