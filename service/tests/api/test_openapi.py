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
        self.assertEqual(set(form["properties"]), {"file", "puid", "mime_type", "format_name", "format_version", "media_type_name", "object_identifier", "object_identifier_type"})
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
        self.assertEqual(set(form["properties"]), {"file", "puid", "mime_type", "format_name", "format_version", "media_type_name", "object_identifier", "object_identifier_type"})
        status = paths["/api/v1/jobs/{job_id}/"]["get"]
        self.assertEqual(set(status["responses"]), {"200", "404"})
        result = paths["/api/v1/jobs/{job_id}/result/"]["get"]
        self.assertEqual(set(result["responses"]), {"200", "404", "409"})
        ok = result["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertIn("tool_resolution", ok["properties"])  # the mes-result schema, unchanged

    def test_the_envelopes_are_json_only_and_the_results_still_have_xml(self):
        paths = self._schema()["paths"]
        for path, method, code in (
            ("/api/v1/jobs/", "post", "202"),
            ("/api/v1/jobs/{job_id}/", "get", "200"),
            ("/api/v1/jobs/batch/", "post", "202"),
            ("/api/v1/job-batches/{batch_id}/", "get", "200"),
        ):
            with self.subTest(path=path):
                self.assertEqual(set(paths[path][method]["responses"][code]["content"]), {"application/json"})
        result = paths["/api/v1/jobs/{job_id}/result/"]["get"]["responses"]["200"]["content"]
        self.assertEqual(set(result), {"application/json", "application/premis+xml"})

    def test_the_removed_envelope_xsd_routes_are_gone(self):
        for url in ("/api/v1/schema/job.xsd", "/api/v1/schema/job-batch.xsd"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_job_schema_urls_return_the_files(self):
        for url, filename in (
            ("/api/v1/schema/job.json", "mes-job.schema.json"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    b"".join(response.streaming_content), (SCHEMA_DIR / filename).read_bytes()
                )

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
        self.assertEqual(set(form["properties"]), {"files", "puid", "mime_type", "format_name", "format_version", "media_type_name", "file_metadata"})
        accepted = submit["responses"]["202"]["content"]["application/json"]["schema"]
        self.assertEqual(set(accepted["required"]), {"batch_id", "created_at", "status", "jobs"})
        status = paths["/api/v1/job-batches/{batch_id}/"]["get"]
        self.assertEqual(set(status["responses"]), {"200", "404"})
        self.assertEqual(status["parameters"][0]["name"], "batch_id")
        # the single-job routes are still there
        self.assertIn("/api/v1/jobs/{job_id}/", paths)

    def test_batch_schema_urls_return_the_files(self):
        for url, filename in (
            ("/api/v1/schema/job-batch.json", "mes-job-batch.schema.json"),
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
        for removed in ("result.xsd", "batch.xsd", "premis-extensions.xsd"):
            self.assertEqual(self.client.get(f"/api/v1/schema/{removed}").status_code, 404)
