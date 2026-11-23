import base64
import subprocess
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from jsonschema import Draft202012Validator, FormatChecker
from lxml import etree
from rest_framework.test import APIClient

from apps.api.renderers import PREMIS_NAMESPACE, PremisXMLRenderer

from tests.api.premis_schema import premis_xsd

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "apps" / "api" / "schema"


def _json_validator():
    import json

    schema = json.loads((SCHEMA_DIR / "mes-result.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _premis_xsd():
    return premis_xsd()


def _premis(data):
    """The standalone PREMIS document of `data` (the only XML form of an extraction result)."""
    return etree.fromstring(PremisXMLRenderer().render(data))


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
        "eventDateTime": "2026-01-01T00:00:01+00:00/2026-01-01T00:00:02+00:00",
        "eventDetailInformation": [{"eventDetail": "Metadata extraction with exiftool, mediainfo."}],
        "eventOutcomeInformation": [
            {
                "eventOutcome": "warning",
                "eventOutcomeDetail": [{"eventOutcomeDetailNote": "error: [unknown] e"}],
            }
        ],
        "linkingAgentIdentifier": [agent("mes-service"), agent("exiftool"), agent("mediainfo")],
    }


def _object():
    return {
        "objectCategory": "file",
        "objectIdentifier": [{"objectIdentifierType": "filepath", "objectIdentifierValue": "photo.jpg"}],
        "objectCharacteristics": [
            {
                "format": [
                    {
                        "formatRegistry": {
                            "formatRegistryName": "PRONOM",
                            "formatRegistryKey": "fmt/43",
                            "formatRegistryRole": "specification",
                        }
                    },
                    {"formatRegistry": {"formatRegistryName": "IANA media types", "formatRegistryKey": "image/jpeg"}},
                ]
            }
        ],
        "linkingEventIdentifier": [
            {"linkingEventIdentifierType": "UUID", "linkingEventIdentifierValue": "abc"},
            {"linkingEventIdentifierType": "UUID", "linkingEventIdentifierValue": "def"},
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
            "matched_rules": [],
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
            "object": _object(),
            "events": [_event(), _event()],
            "agents": _agents(),
            "matched_rules": [],
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

    def test_unknown_category_is_rejected(self):
        data = _sample(error_categories=["bogus"])
        self.assertFalse(_json_validator().is_valid(data))

    def test_premis_document_of_the_sample_validates(self):
        xml = _premis(_sample())
        self.assertTrue(_premis_xsd().validate(xml), _premis_xsd().error_log)

    def test_premis_document_validates_without_ffis(self):
        data = _sample()
        del data["provenance"]["ffis"]
        xml = _premis(data)
        self.assertTrue(_premis_xsd().validate(xml), _premis_xsd().error_log)

    def test_the_premis_document_always_has_an_object_which_premis_xsd_requires(self):
        xml = _premis(_sample())
        for element in xml.findall(f"{{{PREMIS_NAMESPACE}}}object"):
            xml.remove(element)
        self.assertFalse(_premis_xsd().validate(xml))

    def test_premis_document_holds_the_object_events_and_agents(self):
        xml = _premis(_sample())
        self.assertEqual(
            [etree.QName(child).localname for child in xml],
            ["object", "event", "event", "agent", "agent", "agent"],
        )
        self.assertTrue(_premis_xsd().validate(xml))

    def test_control_characters_in_premis_text_keep_the_standalone_document_valid(self):
        data = _sample()
        data["provenance"]["events"][0]["eventOutcomeInformation"][0]["eventOutcomeDetail"][0][
            "eventOutcomeDetailNote"
        ] = "error: [unknown] boom\x00"
        xml = _premis(data)
        self.assertTrue(_premis_xsd().validate(xml), _premis_xsd().error_log)
        note = xml.find(f".//{{{PREMIS_NAMESPACE}}}eventOutcomeDetailNote")
        self.assertEqual(note.text, "error: [unknown] boom\ufffd")

    def test_a_tool_error_note_pair_validates_in_json_and_in_the_premis_document(self):
        data = self._with_error_notes("bad\x00byte")
        _json_validator().validate(data)
        xml = _premis(data)
        self.assertTrue(_premis_xsd().validate(xml), _premis_xsd().error_log)
        notes = [n.text for n in xml.findall(f".//{{{PREMIS_NAMESPACE}}}eventOutcomeDetailNote")]
        self.assertIn("error: bad\ufffdbyte", notes)
        self.assertEqual(notes[notes.index("error: bad\ufffdbyte") + 1], "error type: truncated_or_broken")
        self.assertIsNone(xml.find(f".//{{{PREMIS_NAMESPACE}}}eventOutcomeDetailExtension/errors"))

    def test_object_events_and_agents_are_required_in_json(self):
        for key in ("object", "events", "agents"):
            data = _sample()
            del data["provenance"][key]
            self.assertFalse(_json_validator().is_valid(data), key)

    def _with_error_notes(self, message="Unable to parse the file.", category="truncated_or_broken"):
        data = _sample()
        detail = data["provenance"]["events"][0]["eventOutcomeInformation"][0]["eventOutcomeDetail"]
        detail.append({"eventOutcomeDetailNote": f"error: {message}"})
        detail.append({"eventOutcomeDetailNote": f"error type: {category}"})
        return data

    def test_json_events_must_not_be_empty(self):
        data = _sample()
        data["provenance"]["events"] = []
        self.assertFalse(_json_validator().is_valid(data))

    def test_object_is_rendered_as_a_premis_file_in_the_standalone_document(self):
        obj = _premis(_sample()).find(f"{{{PREMIS_NAMESPACE}}}object")
        self.assertEqual(obj.get("{http://www.w3.org/2001/XMLSchema-instance}type"), "premis:file")
        self.assertEqual(
            [etree.QName(c).localname for c in obj],
            ["objectIdentifier", "objectCharacteristics", "linkingEventIdentifier", "linkingEventIdentifier"],
        )
        self.assertIsNone(obj.find(f"{{{PREMIS_NAMESPACE}}}objectCategory"))

    def test_json_agents_must_not_be_empty(self):
        data = _sample()
        data["provenance"]["agents"] = []
        self.assertFalse(_json_validator().is_valid(data))

    def test_unknown_event_outcome_is_rejected_in_json(self):
        data = _sample()
        data["provenance"]["events"][0]["eventOutcomeInformation"][0]["eventOutcome"] = "partial"
        self.assertFalse(_json_validator().is_valid(data))

    def test_agent_version_follows_agent_type_in_the_standalone_document(self):
        agent = _premis(_sample()).find(f"{{{PREMIS_NAMESPACE}}}agent")
        self.assertEqual(
            [etree.QName(c).localname for c in agent],
            ["agentIdentifier", "agentName", "agentType", "agentVersion"],
        )


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

    def test_premis_response_validates_against_premis_xsd(self):
        response = self._post(HTTP_ACCEPT="application/premis+xml")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/premis+xml"))
        xml = etree.fromstring(response.content)
        self.assertTrue(_premis_xsd().validate(xml), _premis_xsd().error_log)

    def test_premis_response_has_a_valid_metadata_extraction_event(self):
        response = self._post(HTTP_ACCEPT="application/premis+xml")
        xml = etree.fromstring(response.content)
        self.assertTrue(_premis_xsd().validate(xml), _premis_xsd().error_log)
        event_type = xml.find(f"{{{PREMIS_NAMESPACE}}}event/{{{PREMIS_NAMESPACE}}}eventType")
        self.assertEqual(event_type.text, "metadata extraction")

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
