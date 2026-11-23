"""Batch is asynchronous only: there is no `/extract/batch/` route (use `/jobs/batch/`)."""

import yaml
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient


def _file(name="a.jpg"):
    return SimpleUploadedFile(name, b"data", content_type="image/jpeg")


class NoSyncBatchTests(TestCase):
    def test_the_synchronous_batch_route_does_not_exist(self):
        client = APIClient()
        for accept in (None, "application/json", "application/premis+xml", "application/xml"):
            with self.subTest(accept=accept):
                extra = {} if accept is None else {"HTTP_ACCEPT": accept}
                post = client.post("/api/v1/extract/batch/", {"files": [_file(), _file("b.jpg")]}, format="multipart", **extra)
                self.assertEqual(post.status_code, 404)
                self.assertEqual(client.get("/api/v1/extract/batch/", **extra).status_code, 404)

    def test_its_schema_is_gone_and_the_async_batch_schema_stays(self):
        client = APIClient()
        self.assertEqual(client.get("/api/v1/schema/batch.json").status_code, 404)
        self.assertEqual(client.get("/api/v1/schema/job-batch.json").status_code, 200)

    def test_the_openapi_document_has_only_the_async_batch(self):
        response = APIClient().get("/api/v1/schema/", HTTP_ACCEPT="application/vnd.oai.openapi")
        paths = yaml.safe_load(response.content)["paths"]
        self.assertNotIn("/api/v1/extract/batch/", paths)
        self.assertIn("/api/v1/jobs/batch/", paths)
        self.assertIn("/api/v1/job-batches/{batch_id}/", paths)
