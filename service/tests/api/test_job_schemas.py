import json
from pathlib import Path

from django.test import SimpleTestCase
from jsonschema import Draft202012Validator, FormatChecker
from lxml import etree

from apps.api.renderers import MESXMLRenderer

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "apps" / "api" / "schema"


def _envelope(**overrides):
    data = {
        "job_id": "7f3c",
        "status": "failed",
        "created_at": "2026-01-01T00:00:00+00:00",
        "errors": ["Extraction failed."],
    }
    data.update(overrides)
    return data


class JobEnvelopeSchemaTests(SimpleTestCase):
    def setUp(self):
        self.schema = json.loads((SCHEMA_DIR / "mes-job.schema.json").read_text())
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())
        self.xsd = etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "mes-job.xsd")))

    def test_json_schema_is_valid_and_accepts_every_status(self):
        Draft202012Validator.check_schema(self.schema)
        for status in ("pending", "running", "succeeded", "failed"):
            with self.subTest(status=status):
                self.validator.validate(_envelope(status=status, errors=[]))

    def test_json_schema_rejects_unknown_status_and_missing_fields(self):
        self.assertFalse(self.validator.is_valid(_envelope(status="done")))
        for field in ("job_id", "status", "created_at", "errors"):
            data = _envelope()
            del data[field]
            self.assertFalse(self.validator.is_valid(data), field)

    def test_rendered_xml_validates_against_the_xsd(self):
        for data in (_envelope(), _envelope(status="pending", errors=[])):
            with self.subTest(status=data["status"]):
                xml = MESXMLRenderer().render(data)
                self.xsd.assertValid(etree.fromstring(xml))
