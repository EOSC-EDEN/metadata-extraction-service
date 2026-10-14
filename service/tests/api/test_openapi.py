import json
from pathlib import Path

import yaml
from django.test import TestCase
from openapi_spec_validator import validate

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "apps" / "api" / "schema"


class OpenAPITests(TestCase):
    def _schema(self):
        response = self.client.get("/api/v1/schema/", HTTP_ACCEPT="application/vnd.oai.openapi")
        self.assertEqual(response.status_code, 200)
        return yaml.safe_load(response.content)

    def test_schema_is_valid_openapi3_and_lists_extract(self):
        schema = self._schema()
        validate(schema)
        self.assertTrue(schema["openapi"].startswith("3."))
        self.assertIn("/api/v1/extract/", schema["paths"])

    def test_limits_endpoint_is_listed(self):
        operation = self._schema()["paths"]["/api/v1/limits/"]["get"]
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(set(ok["required"]), {"max_upload_bytes"})

    def test_extract_operation_describes_request_and_responses(self):
        operation = self._schema()["paths"]["/api/v1/extract/"]["post"]
        form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
        self.assertEqual(set(form["properties"]), {"file", "puid", "mime_type"})
        self.assertEqual(form["required"], ["file"])
        self.assertEqual(set(operation["responses"]), {"200", "400", "413", "500", "503"})
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertIn("tool_resolution", ok["properties"])

    def test_docs_page_renders(self):
        response = self.client.get("/api/v1/docs/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "swagger-ui")
        self.assertContains(response, "/api/v1/schema/")

    def test_json_schema_and_xsd_urls_return_the_files(self):
        response = self.client.get("/api/v1/schema/result.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(b"".join(response.streaming_content)),
            json.loads((SCHEMA_DIR / "mes-result.schema.json").read_text()),
        )
        response = self.client.get("/api/v1/schema/result.xsd")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            b"".join(response.streaming_content), (SCHEMA_DIR / "mes-result.xsd").read_bytes()
        )
