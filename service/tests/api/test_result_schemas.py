import base64
import subprocess
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from jsonschema import Draft7Validator, FormatChecker
from lxml import etree
from rest_framework.test import APIClient

from apps.api.renderers import NAMESPACE, MESXMLRenderer

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "apps" / "api" / "schema"
NS = {"m": NAMESPACE}


def _json_validator():
    import json

    schema = json.loads((SCHEMA_DIR / "mes-result.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema, format_checker=FormatChecker())


def _xsd():
    return etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "mes-result.xsd")))


def _sample(**overrides):
    data = {
        "request_id": "abc",
        "filename": "photo.jpg",
        "tool_resolution": {
            "puid": "fmt/43",
            "mime_type": None,
            "resolved_tools": ["exiftool"],
            "selection_method": "default_tool_caller_identified",
        },
        "results": [
            {
                "tool": "exiftool",
                "version": "12.40",
                "status": "success",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:00:01+00:00",
                "command": ["exiftool", "-json", "f.jpg"],
                "raw_output": '[{"a": "<b> & c"}]',
                "raw_output_format": "json",
                "errors": [],
                "warnings": ["w"],
            },
            {
                "tool": "mediainfo",
                "version": None,
                "status": "failure",
                "started_at": None,
                "finished_at": None,
                "command": None,
                "raw_output": None,
                "raw_output_format": None,
                "errors": ["boom"],
                "warnings": [],
            },
        ],
        "provenance": {
            "timestamp": "2026-01-01T00:00:02+00:00",
            "service_version": "0.1.0",
            "tool_versions": {"exiftool": "12.40", "mediainfo": None},
            "ffis": {"outcome": "success", "tools": [{"name": "siegfried", "score": 0.9, "ok": True, "x": None}]},
        },
        "errors": ["e"],
        "warnings": [],
    }
    data.update(overrides)
    return data


class SchemaTests(SimpleTestCase):
    def test_json_schema_accepts_sample(self):
        _json_validator().validate(_sample())

    def test_json_schema_rejects_unknown_status(self):
        data = _sample()
        data["results"][0]["status"] = "weird"
        self.assertFalse(_json_validator().is_valid(data))

    def test_xsd_accepts_rendered_sample(self):
        xml = etree.fromstring(MESXMLRenderer().render(_sample()))
        xsd = _xsd()
        self.assertTrue(xsd.validate(xml), xsd.error_log)

    def test_xsd_accepts_sample_without_ffis(self):
        data = _sample()
        data["provenance"]["ffis"] = None
        xml = etree.fromstring(MESXMLRenderer().render(data))
        xsd = _xsd()
        self.assertTrue(xsd.validate(xml), xsd.error_log)

    def test_raw_output_is_unmodified_and_escaped(self):
        xml = etree.fromstring(MESXMLRenderer().render(_sample()))
        raw = xml.find("m:results/m:result/m:rawOutput", NS)
        self.assertEqual(raw.text, '[{"a": "<b> & c"}]')

    def test_control_characters_are_base64_encoded(self):
        data = _sample()
        data["results"][0]["raw_output"] = "a\x00b\x1f"
        xml = etree.fromstring(MESXMLRenderer().render(data))
        xsd = _xsd()
        self.assertTrue(xsd.validate(xml), xsd.error_log)
        raw = xml.find("m:results/m:result/m:rawOutput", NS)
        self.assertEqual(raw.get("encoding"), "base64")
        self.assertEqual(base64.b64decode(raw.text).decode(), "a\x00b\x1f")

    def test_null_fields_are_omitted(self):
        xml = etree.fromstring(MESXMLRenderer().render(_sample()))
        self.assertIsNone(xml.find("m:toolResolution/m:mimeType", NS))
        second = xml.findall("m:results/m:result", NS)[1]
        self.assertIsNone(second.find("m:version", NS))
        self.assertIsNone(second.find("m:rawOutput", NS))

    def test_error_payload_renders_without_failing(self):
        out = MESXMLRenderer().render({"errors": ["no default tool"]})
        xml = etree.fromstring(out)
        self.assertEqual(etree.QName(xml).localname, "error")


class EndpointSchemaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/extract/"

    def _post(self, **extra):
        upload = SimpleUploadedFile("photo.jpg", b"data", content_type="image/jpeg")
        with mock.patch("apps.extraction.adapters.base.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout='[{"a": 1}]', stderr=""
            )
            return self.client.post(
                self.url, {"file": upload, "mime_type": "image/jpeg"}, format="multipart", **extra
            )

    def test_json_response_validates(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        _json_validator().validate(response.json())

    def test_xml_response_validates(self):
        response = self._post(HTTP_ACCEPT="application/xml")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/xml"))
        xsd = _xsd()
        xml = etree.fromstring(response.content)
        self.assertTrue(xsd.validate(xml), xsd.error_log)
        raw = xml.find("m:results/m:result/m:rawOutput", NS)
        self.assertEqual(raw.text, '[{"a": 1}]')

    def test_default_is_json(self):
        self.assertTrue(self._post()["Content-Type"].startswith("application/json"))
