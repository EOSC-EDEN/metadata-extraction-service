import base64
import subprocess
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from jsonschema import Draft202012Validator, FormatChecker
from lxml import etree
from rest_framework.test import APIClient

from apps.api.renderers import NAMESPACE, PREMIS_NAMESPACE, MESXMLRenderer

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "apps" / "api" / "schema"
NS = {"m": NAMESPACE}


def _json_validator():
    import json

    schema = json.loads((SCHEMA_DIR / "mes-result.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _xsd():
    return etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "mes-result.xsd")))


def _event():
    def agent(value):
        return {
            "linkingAgentIdentifierType": "MES",
            "linkingAgentIdentifierValue": value,
            "linkingAgentRole": ["executing program"],
        }

    return {
        "eventIdentifier": {"eventIdentifierType": "UUID", "eventIdentifierValue": "abc"},
        "eventType": "metadata extraction",
        "eventDateTime": "2026-01-01T00:00:02+00:00",
        "eventDetailInformation": [{"eventDetail": "Metadata extraction with exiftool, mediainfo."}],
        "eventOutcomeInformation": [
            {
                "eventOutcome": "warning",
                "eventOutcomeDetail": [{"eventOutcomeDetailNote": "error: [unknown] e"}],
            }
        ],
        "linkingAgentIdentifier": [agent("mes-service"), agent("exiftool"), agent("mediainfo")],
        "linkingObjectIdentifier": [
            {"linkingObjectIdentifierType": "filename", "linkingObjectIdentifierValue": "photo.jpg"}
        ],
    }


def _agents():
    def agent(value, version):
        data = {
            "agentIdentifier": [{"agentIdentifierType": "MES", "agentIdentifierValue": value}],
            "agentName": [value],
            "agentType": "software",
        }
        if version:
            data["agentVersion"] = version
        return data

    return [agent("mes-service", "0.1.0"), agent("exiftool", "12.40"), agent("mediainfo", None)]


def _sample(**overrides):
    data = {
        "request_id": "abc",
        "filename": "photo.jpg",
        "tool_resolution": {
            "puid": "fmt/43",
            "mime_type": None,
            "resolved_tools": ["exiftool"],
            "selection_method": "default_tool_caller_identified",
            "identification_url": "http://ffis.example:8001",
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
            "event": _event(),
            "agents": _agents(),
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

    def test_old_shape_without_categories_still_validates(self):
        data = _sample()
        self.assertNotIn("error_details", data)
        _json_validator().validate(data)
        xml = etree.fromstring(MESXMLRenderer().render(data))
        self.assertIsNone(xml.find("m:errorDetails", NS))
        self.assertTrue(_xsd().validate(xml))

    def test_categorised_sample_validates_in_json_and_xml(self):
        data = _sample()
        data["results"][1].update(
            errors=["boom\x00"],
            error_categories=["wrong_format"],
            error_details=[{"message": "boom\x00", "category": "wrong_format"}],
        )
        data.update(
            errors=["e"], error_categories=["encrypted"],
            error_details=[{"message": "e", "category": "encrypted"}],
        )
        _json_validator().validate(data)
        xml = etree.fromstring(MESXMLRenderer().render(data))
        xsd = _xsd()
        self.assertTrue(xsd.validate(xml), xsd.error_log)
        self.assertEqual(xml.find("m:errorCategories/m:item", NS).text, "encrypted")
        msg = xml.findall("m:results/m:result", NS)[1].find("m:errorDetails/m:errorDetail/m:message", NS)
        self.assertEqual(msg.get("encoding"), "base64")

    def test_unknown_category_is_rejected(self):
        data = _sample(error_categories=["bogus"])
        self.assertFalse(_json_validator().is_valid(data))

    def test_event_and_agents_are_required_in_json(self):
        for key in ("event", "agents"):
            data = _sample()
            del data["provenance"][key]
            self.assertFalse(_json_validator().is_valid(data), key)

    def test_event_and_agents_are_required_in_xml(self):
        xsd = _xsd()
        for local in ("event", "agent"):
            xml = etree.fromstring(MESXMLRenderer().render(_sample()))
            prov = xml.find("m:provenance", NS)
            for node in prov.findall(f"{{{PREMIS_NAMESPACE}}}{local}"):
                prov.remove(node)
            self.assertFalse(xsd.validate(xml), local)

    def test_json_agents_must_not_be_empty(self):
        data = _sample()
        data["provenance"]["agents"] = []
        self.assertFalse(_json_validator().is_valid(data))

    def test_unknown_event_outcome_is_rejected_in_json(self):
        data = _sample()
        data["provenance"]["event"]["eventOutcomeInformation"][0]["eventOutcome"] = "partial"
        self.assertFalse(_json_validator().is_valid(data))

    def test_premis_elements_validate_against_premis_xsd(self):
        premis = etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "premis.xsd")))
        xml = etree.fromstring(MESXMLRenderer().render(_sample()))
        nodes = xml.findall(f"m:provenance/{{{PREMIS_NAMESPACE}}}*", NS)
        self.assertEqual(len(nodes), 4)  # the event and three agents
        for node in nodes:
            self.assertTrue(premis.validate(node), premis.error_log)

    def test_agent_version_follows_agent_type(self):
        xml = etree.fromstring(MESXMLRenderer().render(_sample()))
        agent = xml.find(f"m:provenance/{{{PREMIS_NAMESPACE}}}agent", NS)
        self.assertEqual(
            [etree.QName(c).localname for c in agent],
            ["agentIdentifier", "agentName", "agentType", "agentVersion"],
        )

    def test_control_characters_in_premis_text_keep_the_document_valid(self):
        data = _sample()
        note = "error: [unknown] boom\x00"
        data["provenance"]["event"]["eventOutcomeInformation"][0]["eventOutcomeDetail"][0][
            "eventOutcomeDetailNote"
        ] = note
        xml = etree.fromstring(MESXMLRenderer().render(data))
        xsd = _xsd()
        self.assertTrue(xsd.validate(xml), xsd.error_log)
        text = xml.find(f".//{{{PREMIS_NAMESPACE}}}eventOutcomeDetailNote", NS).text
        self.assertEqual(text, "error: [unknown] boom\ufffd")

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

    def test_xml_response_has_a_valid_premis_event(self):
        response = self._post(HTTP_ACCEPT="application/xml")
        xml = etree.fromstring(response.content)
        premis = etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "premis.xsd")))
        nodes = xml.findall(f"m:provenance/{{{PREMIS_NAMESPACE}}}*", NS)
        self.assertGreaterEqual(len(nodes), 2)  # the event and at least the service agent
        for node in nodes:
            self.assertTrue(premis.validate(node), premis.error_log)
        self.assertEqual(
            xml.find(f"m:provenance/{{{PREMIS_NAMESPACE}}}event/{{{PREMIS_NAMESPACE}}}eventType", NS).text,
            "metadata extraction",
        )

    def test_failed_tool_response_has_aligned_categories(self):
        upload = SimpleUploadedFile("photo.jpg", b"data", content_type="image/jpeg")
        with mock.patch("apps.extraction.adapters.base.subprocess.run") as run:
            run.side_effect = OSError("Error: File format error")
            response = self.client.post(
                self.url, {"file": upload, "mime_type": "image/jpeg"}, format="multipart"
            )
        body = response.json()
        _json_validator().validate(body)
        for holder in (body, *body["results"]):
            self.assertEqual(len(holder["error_categories"]), len(holder["errors"]))
            self.assertEqual(len(holder["error_details"]), len(holder["errors"]))
            for i, detail in enumerate(holder["error_details"]):
                self.assertEqual(detail["message"], holder["errors"][i])
                self.assertEqual(detail["category"], holder["error_categories"][i])
        self.assertIn("wrong_format", body["results"][0]["error_categories"])

    def test_default_is_json(self):
        self.assertTrue(self._post()["Content-Type"].startswith("application/json"))
