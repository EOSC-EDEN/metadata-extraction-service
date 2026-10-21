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
        self.assertEqual(set(ok["required"]), {"max_upload_bytes", "max_files_per_request", "async_enabled"})

    def test_local_rules_endpoint_is_listed(self):
        operation = self._schema()["paths"]["/api/v1/local-rules/"]["get"]
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(ok["required"], ["rules"])
        schema = self._schema()
        self.assertEqual(ok["properties"]["rules"]["items"], {"$ref": "#/components/schemas/LocalRule"})
        self.assertEqual(
            set(schema["components"]["schemas"]["LocalRule"]["required"]),
            {"name", "puids", "extensions", "tools", "match"},
        )

    def test_extract_operation_describes_request_and_responses(self):
        operation = self._schema()["paths"]["/api/v1/extract/"]["post"]
        form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
        self.assertEqual(set(form["properties"]), {"file", "puid", "mime_type", "object_identifier", "object_identifier_type"})
        self.assertEqual(form["required"], ["file"])
        self.assertEqual(set(operation["responses"]), {"200", "400", "413", "500", "503"})
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertIn("tool_resolution", ok["properties"])

    def test_job_operations_are_documented(self):
        paths = self._schema()["paths"]
        submit = paths["/api/v1/jobs/"]["post"]
        self.assertEqual(set(submit["responses"]), {"202", "400", "404", "413", "500", "503"})
        self.assertIn("Location", submit["responses"]["202"]["headers"])
        form = submit["requestBody"]["content"]["multipart/form-data"]["schema"]
        self.assertEqual(set(form["properties"]), {"file", "puid", "mime_type", "object_identifier", "object_identifier_type"})
        status = paths["/api/v1/jobs/{job_id}/"]["get"]
        self.assertEqual(set(status["responses"]), {"200", "404"})
        result = paths["/api/v1/jobs/{job_id}/result/"]["get"]
        self.assertEqual(set(result["responses"]), {"200", "404", "409"})
        ok = result["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertIn("tool_resolution", ok["properties"])  # the mes-result schema, unchanged

    def test_job_schema_urls_return_the_files(self):
        for url, filename in (
            ("/api/v1/schema/job.json", "mes-job.schema.json"),
            ("/api/v1/schema/job.xsd", "mes-job.xsd"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    b"".join(response.streaming_content), (SCHEMA_DIR / filename).read_bytes()
                )

    def test_batch_extract_operation_describes_a_repeated_files_field(self):
        operation = self._schema()["paths"]["/api/v1/extract/batch/"]["post"]
        form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
        self.assertEqual(set(form["properties"]), {"files", "puid", "mime_type"})
        self.assertEqual(form["required"], ["files"])
        self.assertEqual(
            form["properties"]["files"]["items"], {"type": "string", "format": "binary"}
        )
        self.assertEqual(form["properties"]["files"]["type"], "array")
        self.assertEqual(set(operation["responses"]), {"200", "400", "413", "500"})
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
        entry = ok["properties"]["results"]["items"]
        self.assertEqual(set(entry["properties"]), {"index", "filename", "result", "errors"})
        self.assertIn("tool_resolution", entry["properties"]["result"]["properties"])  # mes-result, unchanged
        self.assertIn("application/xml", operation["responses"]["200"]["content"])

    def test_the_single_file_extract_operation_is_unchanged_by_the_batch_route(self):
        operation = self._schema()["paths"]["/api/v1/extract/"]["post"]
        form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
        self.assertNotIn("files", form["properties"])

    def test_batch_job_operations_are_documented(self):
        schema = self._schema()
        paths = schema["paths"]
        submit = paths["/api/v1/jobs/batch/"]["post"]
        self.assertEqual(set(submit["responses"]), {"202", "400", "404", "413", "500", "503"})
        self.assertIn("Location", submit["responses"]["202"]["headers"])
        form = submit["requestBody"]["content"]["multipart/form-data"]["schema"]
        self.assertEqual(set(form["properties"]), {"files", "puid", "mime_type"})
        accepted = submit["responses"]["202"]["content"]["application/json"]["schema"]
        self.assertEqual(set(accepted["required"]), {"batch_id", "created_at", "status", "jobs"})
        status = paths["/api/v1/job-batches/{batch_id}/"]["get"]
        self.assertEqual(set(status["responses"]), {"200", "404"})
        self.assertEqual(status["parameters"][0]["name"], "batch_id")
        # the single-job routes are still there
        self.assertIn("/api/v1/jobs/{job_id}/", paths)

    def test_batch_schema_urls_return_the_files(self):
        for url, filename in (
            ("/api/v1/schema/batch.json", "mes-batch.schema.json"),
            ("/api/v1/schema/batch.xsd", "mes-batch.xsd"),
            ("/api/v1/schema/job-batch.json", "mes-job-batch.schema.json"),
            ("/api/v1/schema/job-batch.xsd", "mes-job-batch.xsd"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    b"".join(response.streaming_content), (SCHEMA_DIR / filename).read_bytes()
                )

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
