import os
import subprocess
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from prometheus_client import REGISTRY
from rest_framework.test import APIClient

from apps.api import views

URL = "/api/v1/extract/batch/"
SINGLE_URL = "/api/v1/extract/"


def _upload(content=b"data", name="photo.jpg"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


def _ok(args=None, **kwargs):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout='[{"a": 1}]', stderr="")


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def _stable(result):
    """The parts of a `mes-result` document that must not depend on the route that produced it."""
    return {
        "filename": result["filename"],
        "tool_resolution": result["tool_resolution"],
        "results": [
            {key: tool[key] for key in ("tool", "status", "raw_output", "errors", "warnings")}
            for tool in result["results"]
        ],
        "errors": result["errors"],
        "warnings": result["warnings"],
    }


class BatchViewTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        self.upload_dir = uploads.name
        override = override_settings(MES_JOB_UPLOAD_DIR=self.upload_dir)
        override.enable()
        self.addCleanup(override.disable)

    def post(self, files, **fields):
        data = {"files": files, "mime_type": "image/jpeg", **fields}
        return self.client.post(URL, data, format="multipart")

    def assertWorkDirRemoved(self):
        self.assertEqual(os.listdir(self.upload_dir), [])


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class BatchExtractTests(BatchViewTestCase):
    def test_one_file_gives_the_same_result_as_the_single_file_route(self, run):
        single = self.client.post(
            SINGLE_URL, {"file": _upload(), "mime_type": "image/jpeg"}, format="multipart"
        ).json()

        response = self.post([_upload()])

        self.assertEqual(response.status_code, 200)
        (entry,) = response.json()["results"]
        self.assertEqual(entry["index"], 0)
        self.assertEqual(entry["filename"], "photo.jpg")
        self.assertNotIn("errors", entry)
        self.assertEqual(_stable(entry["result"]), _stable(single))
        self.assertWorkDirRemoved()

    def test_results_come_back_in_upload_order(self, run):
        names = ["c.jpg", "a.jpg", "b.jpg"]

        response = self.post([_upload(name=name) for name in names])

        entries = response.json()["results"]
        self.assertEqual([e["index"] for e in entries], [0, 1, 2])
        self.assertEqual([e["filename"] for e in entries], names)
        self.assertEqual([e["result"]["filename"] for e in entries], names)

    def test_duplicate_filenames_are_kept_apart(self, run):
        paths = []

        def record(args, **kwargs):
            paths.append(next(a for a in args if "upload" in str(a)))
            return _ok()

        run.side_effect = record

        response = self.post([_upload(b"one", "same.jpg"), _upload(b"two", "same.jpg")])

        entries = response.json()["results"]
        self.assertEqual([e["index"] for e in entries], [0, 1])
        self.assertEqual([e["filename"] for e in entries], ["same.jpg", "same.jpg"])
        self.assertEqual(len(set(paths)), 2)

    def test_puid_and_mime_type_apply_to_every_file(self, run):
        response = self.post([_upload(name="a.jpg"), _upload(name="b.jpg")], puid="fmt/43")

        for entry in response.json()["results"]:
            self.assertEqual(entry["result"]["tool_resolution"]["puid"], "fmt/43")
            self.assertEqual(entry["result"]["tool_resolution"]["mime_type"], "image/jpeg")

    def test_every_file_gets_its_own_request_id_derived_from_the_request(self, run):
        response = self.post([_upload(name=f"{n}.jpg") for n in range(3)])

        ids = [e["result"]["request_id"] for e in response.json()["results"]]
        self.assertEqual(len(set(ids)), 3)
        prefix = ids[0].rsplit("-", 1)[0]
        self.assertEqual(ids, [f"{prefix}-{n}" for n in range(3)])

    def test_the_event_uses_the_filename_as_object_identifier(self, run):
        response = self.post([_upload(name="x.jpg")])

        event = response.json()["results"][0]["result"]["provenance"]["event"]
        self.assertIn("x.jpg", str(event))

    def test_get_is_not_allowed(self, run):
        self.assertEqual(self.client.get(URL).status_code, 405)


class BatchFailureIsolationTests(BatchViewTestCase):
    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_a_failing_tool_leaves_the_other_files_intact(self, run):
        def fail_second(args, **kwargs):
            if any(os.sep + "1" + os.sep in str(a) for a in args):
                raise subprocess.CalledProcessError(1, args, output="", stderr="boom")
            return _ok()

        run.side_effect = fail_second

        response = self.post([_upload(name=f"{n}.jpg") for n in range(3)])

        self.assertEqual(response.status_code, 200)
        statuses = [e["result"]["results"][0]["status"] for e in response.json()["results"]]
        self.assertEqual(statuses[0], "success")
        self.assertNotEqual(statuses[1], "success")
        self.assertEqual(statuses[2], "success")
        self.assertWorkDirRemoved()

    @mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
    def test_a_save_failure_is_reported_for_that_file_only(self, run):
        real_save = views.save_upload
        calls = []

        def save(upload, directory, *args, **kwargs):
            calls.append(upload.name)
            if len(calls) == 2:
                raise OSError("disk full: /secret/path")
            return real_save(upload, directory, *args, **kwargs)

        with mock.patch("apps.api.views.save_upload", side_effect=save):
            response = self.post([_upload(name=f"{n}.jpg") for n in range(3)])

        self.assertEqual(response.status_code, 200)
        first, second, third = response.json()["results"]
        self.assertIn("result", first)
        self.assertIn("result", third)
        self.assertNotIn("result", second)
        self.assertEqual((second["index"], second["filename"]), (1, "1.jpg"))
        self.assertTrue(second["errors"])
        self.assertNotIn("/secret/path", str(second))
        self.assertWorkDirRemoved()

    @mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
    def test_an_unexpected_error_in_one_file_does_not_discard_the_others(self, run):
        real_extract = views.ExtractionOrchestrator.extract
        calls = []

        def extract(self, *args, **kwargs):
            calls.append(kwargs["filename"])
            if len(calls) == 1:
                raise RuntimeError("secret detail")
            return real_extract(self, *args, **kwargs)

        with mock.patch.object(views.ExtractionOrchestrator, "extract", extract):
            response = self.post([_upload(name="a.jpg"), _upload(name="b.jpg")])

        self.assertEqual(response.status_code, 200)
        first, second = response.json()["results"]
        self.assertTrue(first["errors"])
        self.assertNotIn("secret detail", str(first))
        self.assertIn("result", second)
        self.assertWorkDirRemoved()


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class BatchValidationTests(BatchViewTestCase):
    def assertRejected(self, response, status_code):
        self.assertEqual(response.status_code, status_code)
        body = response.json()
        self.assertTrue(body["errors"])
        self.assertIn("request_id", body)
        self.assertWorkDirRemoved()

    def test_no_files_is_400(self, run):
        self.assertRejected(self.client.post(URL, {"mime_type": "image/jpeg"}, format="multipart"), 400)

    def test_the_single_file_field_name_is_not_accepted(self, run):
        self.assertRejected(self.client.post(URL, {"file": _upload()}, format="multipart"), 400)

    @override_settings(MES_MAX_FILES_PER_REQUEST=3)
    def test_file_count_boundary(self, run):
        at_limit = self.post([_upload(name=f"{n}.jpg") for n in range(3)])
        over_limit = self.post([_upload(name=f"{n}.jpg") for n in range(4)])

        self.assertEqual(at_limit.status_code, 200)
        self.assertEqual(len(at_limit.json()["results"]), 3)
        self.assertRejected(over_limit, 400)

    @override_settings(MES_MAX_FILES_PER_REQUEST=1)
    def test_a_limit_of_one_still_takes_one_file(self, run):
        self.assertEqual(self.post([_upload()]).status_code, 200)
        self.assertRejected(self.post([_upload(), _upload()]), 400)

    @override_settings(MES_MAX_UPLOAD_BYTES=10)
    def test_total_size_boundary(self, run):
        at_limit = self.post([_upload(b"x" * 5), _upload(b"x" * 5)])
        over_limit = self.post([_upload(b"x" * 5), _upload(b"x" * 6)])

        self.assertEqual(at_limit.status_code, 200)
        self.assertRejected(over_limit, 413)

    @override_settings(MES_MAX_UPLOAD_BYTES=10)
    def test_one_file_over_the_limit_is_413(self, run):
        self.assertRejected(self.post([_upload(b"x" * 11)]), 413)

    @override_settings(MES_MAX_UPLOAD_BYTES=100, DATA_UPLOAD_MAX_MEMORY_SIZE=50)
    def test_declared_length_over_limit_plus_overhead_is_413_without_parsing(self, run):
        with mock.patch("apps.api.views.BatchExtractionRequestSerializer") as serializer:
            response = self.client.post(
                URL, {"files": [_upload(b"x")]}, format="multipart", CONTENT_LENGTH="151"
            )

        self.assertRejected(response, 413)
        serializer.assert_not_called()

    def test_object_identifier_is_rejected(self, run):
        for field in ("object_identifier", "object_identifier_type"):
            with self.subTest(field=field):
                self.assertRejected(self.post([_upload()], **{field: "abc"}), 400)

    def test_blank_object_identifier_counts_as_not_given(self, run):
        self.assertEqual(self.post([_upload()], object_identifier="").status_code, 200)

    def test_an_empty_file_makes_the_whole_request_invalid(self, run):
        response = self.post([_upload(), _upload(b"", "empty.jpg"), _upload()])

        self.assertRejected(response, 400)
        self.assertIn("empty.jpg", str(response.json()["errors"]))
        run.assert_not_called()

    def test_nothing_is_extracted_for_a_rejected_request(self, run):
        self.post([_upload()], object_identifier="abc")
        run.assert_not_called()


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class BatchMetricsTests(BatchViewTestCase):
    def test_every_file_counts_as_one_extraction_request(self, run):
        before = _sample("mes_extract_requests_total", {"status": "success"})

        self.post([_upload(name=f"{n}.jpg") for n in range(3)])

        self.assertEqual(_sample("mes_extract_requests_total", {"status": "success"}) - before, 3)

    def test_batch_request_and_size_are_recorded(self, run):
        requests = _sample("mes_batch_requests_total", {"status": "success"})
        count = _sample("mes_batch_files_count")
        total = _sample("mes_batch_files_sum")

        self.post([_upload(name=f"{n}.jpg") for n in range(3)])

        self.assertEqual(_sample("mes_batch_requests_total", {"status": "success"}) - requests, 1)
        self.assertEqual(_sample("mes_batch_files_count") - count, 1)
        self.assertEqual(_sample("mes_batch_files_sum") - total, 3)

    def test_a_batch_with_a_failed_file_is_partial(self, run):
        before = _sample("mes_batch_requests_total", {"status": "partial"})

        real_save = views.save_upload
        calls = []

        def save(upload, directory, *args, **kwargs):
            calls.append(upload.name)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_save(upload, directory, *args, **kwargs)

        with mock.patch("apps.api.views.save_upload", side_effect=save):
            self.post([_upload(name="a.jpg"), _upload(name="b.jpg")])

        self.assertEqual(_sample("mes_batch_requests_total", {"status": "partial"}) - before, 1)

    def test_a_rejected_batch_is_counted_as_rejected(self, run):
        before = _sample("mes_batch_requests_total", {"status": "rejected"})

        self.client.post(URL, {}, format="multipart")

        self.assertEqual(_sample("mes_batch_requests_total", {"status": "rejected"}) - before, 1)
