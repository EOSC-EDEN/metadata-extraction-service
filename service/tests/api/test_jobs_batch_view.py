import os
import subprocess
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.api import jobs
from tests.api import test_jobs_view
from tests.api.job_helpers import DeferredExecutor

TTL = test_jobs_view.TTL
SUBMIT = "/api/v1/jobs/batch/"
STATUS = "/api/v1/job-batches/{}/"


def _upload(content=b"data", name="photo.jpg"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout='[{"a": 1}]', stderr="")


def _stable(result):
    result = dict(result)
    result.pop("request_id")
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


class BatchJobsTestCase(test_jobs_view.JobsViewTestCase):
    def setUp(self):
        super().setUp()
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        self.upload_dir = uploads.name
        override = override_settings(MES_JOB_UPLOAD_DIR=self.upload_dir)
        override.enable()
        self.addCleanup(override.disable)

    def submit_batch(self, files, **fields):
        data = {"files": files, "mime_type": "image/jpeg", **fields}
        return self.client.post(SUBMIT, data, format="multipart")

    def batch_status(self, batch_id, **extra):
        return self.client.get(STATUS.format(batch_id), **extra)

    def assertNoUploadsLeft(self):
        self.assertEqual(os.listdir(self.upload_dir), [])


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class SubmitBatchTests(BatchJobsTestCase):
    def test_submit_returns_202_with_location_and_envelope(self, run):
        response = self.submit_batch([_upload(name="a.jpg"), _upload(name="b.jpg")])

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(set(body), {"batch_id", "created_at", "status", "jobs"})
        self.assertEqual(response["Location"], STATUS.format(body["batch_id"]))
        self.assertEqual([j["index"] for j in body["jobs"]], [0, 1])
        self.assertEqual([j["filename"] for j in body["jobs"]], ["a.jpg", "b.jpg"])
        self.assertEqual(set(body["jobs"][0]), {"index", "filename", "job_id", "status"})
        self.assertEqual(len({j["job_id"] for j in body["jobs"]}), 2)

    def test_each_file_is_an_ordinary_job_whose_result_equals_the_sync_result(self, run):
        names = ["a.jpg", "b.jpg", "c.jpg"]
        body = self.submit_batch([_upload(name=n) for n in names]).json()

        for entry in body["jobs"]:
            job_id = entry["job_id"]
            self.assertEqual(self.client.get(f"/api/v1/jobs/{job_id}/").json()["status"], "succeeded")
            async_result = self.client.get(f"/api/v1/jobs/{job_id}/result/").json()
            sync = self.client.post(
                "/api/v1/extract/",
                {"file": _upload(name=entry["filename"]), "mime_type": "image/jpeg"},
                format="multipart",
            ).json()
            self.assertEqual(_stable(async_result), _stable(sync))
        self.assertNoUploadsLeft()

    def test_jobs_get_request_ids_derived_from_the_request_and_the_index(self, run):
        body = self.submit_batch([_upload(), _upload()]).json()

        ids = [self.client.get(f"/api/v1/jobs/{j['job_id']}/result/").json()["request_id"] for j in body["jobs"]]
        prefix = ids[0].rsplit("-", 1)[0]
        self.assertEqual(ids, [f"{prefix}-0", f"{prefix}-1"])

    def test_puid_and_mime_type_apply_to_every_file(self, run):
        body = self.submit_batch([_upload(), _upload()], puid="fmt/43").json()

        for entry in body["jobs"]:
            resolution = self.client.get(f"/api/v1/jobs/{entry['job_id']}/result/").json()["tool_resolution"]
            self.assertEqual((resolution["puid"], resolution["mime_type"]), ("fmt/43", "image/jpeg"))

    def test_duplicate_filenames_are_kept_apart(self, run):
        paths = []

        def record(args, **kwargs):
            paths.append(next(a for a in args if "upload" in str(a)))
            return _ok()

        run.side_effect = record

        body = self.submit_batch([_upload(b"one", "same.jpg"), _upload(b"two", "same.jpg")]).json()

        self.assertEqual([j["filename"] for j in body["jobs"]], ["same.jpg", "same.jpg"])
        self.assertEqual(len(set(paths)), 2)
        self.assertEqual(len({os.path.dirname(p) for p in paths}), 2)

    def test_the_single_file_job_route_is_unchanged(self, run):
        response = self.submit()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(set(response.json()), {"job_id", "status", "created_at", "errors"})

    def test_get_on_the_submit_route_is_not_allowed(self, run):
        self.assertEqual(self.client.get(SUBMIT).status_code, 405)


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class BatchStatusTests(BatchJobsTestCase):
    executor_class = DeferredExecutor

    def run_job(self, index):
        fn, args, kwargs = self.executor.pending.pop(index)
        fn(*args, **kwargs)

    def test_all_pending(self, run):
        body = self.submit_batch([_upload(), _upload()]).json()

        status = self.batch_status(body["batch_id"]).json()
        self.assertEqual(status["status"], "pending")
        self.assertEqual([j["status"] for j in status["jobs"]], ["pending", "pending"])
        self.assertEqual(status["batch_id"], body["batch_id"])
        self.assertEqual(status["created_at"], body["created_at"])

    def test_pending_mixed_with_finished_is_running(self, run):
        body = self.submit_batch([_upload(), _upload()]).json()
        self.run_job(0)

        status = self.batch_status(body["batch_id"]).json()
        self.assertEqual(status["status"], "running")
        self.assertEqual([j["status"] for j in status["jobs"]], ["succeeded", "pending"])

    def test_a_running_job_makes_the_batch_running(self, run):
        body = self.submit_batch([_upload(), _upload()]).json()
        real = jobs.ExtractionOrchestrator.extract
        seen = []

        def spy(orchestrator, *args, **kwargs):
            seen.append(self.batch_status(body["batch_id"]).json())
            return real(orchestrator, *args, **kwargs)

        with mock.patch.object(jobs.ExtractionOrchestrator, "extract", spy):
            self.run_job(0)

        self.assertEqual(seen[0]["status"], "running")
        self.assertEqual([j["status"] for j in seen[0]["jobs"]], ["running", "pending"])

    def test_all_succeeded(self, run):
        body = self.submit_batch([_upload(), _upload()]).json()
        self.executor.run_all()

        status = self.batch_status(body["batch_id"]).json()
        self.assertEqual(status["status"], "succeeded")
        self.assertEqual([j["status"] for j in status["jobs"]], ["succeeded", "succeeded"])

    def test_all_failed(self, run):
        body = self.submit_batch([_upload(), _upload()]).json()
        with mock.patch.object(jobs.ExtractionOrchestrator, "extract", side_effect=RuntimeError("x")):
            self.executor.run_all()

        self.assertEqual(self.batch_status(body["batch_id"]).json()["status"], "failed")

    def test_some_failed_after_all_finished_is_partial_and_siblings_are_intact(self, run):
        body = self.submit_batch([_upload(name="a.jpg"), _upload(name="bad.jpg"), _upload(name="c.jpg")]).json()
        real = jobs.ExtractionOrchestrator.extract

        def extract(orchestrator, *args, **kwargs):
            if kwargs["filename"] == "bad.jpg":
                raise RuntimeError("boom")
            return real(orchestrator, *args, **kwargs)

        with mock.patch.object(jobs.ExtractionOrchestrator, "extract", extract):
            self.executor.run_all()

        status = self.batch_status(body["batch_id"]).json()
        self.assertEqual(status["status"], "partial")
        self.assertEqual([j["status"] for j in status["jobs"]], ["succeeded", "failed", "succeeded"])
        ok_id, bad_id = status["jobs"][0]["job_id"], status["jobs"][1]["job_id"]
        self.assertEqual(self.client.get(f"/api/v1/jobs/{ok_id}/result/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/jobs/{bad_id}/result/").status_code, 409)

    def test_unknown_batch_is_404(self, run):
        response = self.batch_status("nope")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.json()["errors"])

    def assertAvailableAfter(self, seconds, expected_status):
        body = self.submit_batch([_upload(), _upload()]).json()
        self.clock.advance(seconds)
        self.assertEqual(self.batch_status(body["batch_id"]).status_code, expected_status)
        # the jobs expire with the batch
        job = self.client.get(f"/api/v1/jobs/{body['jobs'][0]['job_id']}/")
        self.assertEqual(job.status_code, expected_status)

    def test_ttl_just_under_keeps_the_batch(self, run):
        self.assertAvailableAfter(TTL - 1, 200)

    def test_ttl_exactly_expires_the_batch_with_its_jobs(self, run):
        self.assertAvailableAfter(TTL, 404)

    def test_ttl_over_expires_the_batch_with_its_jobs(self, run):
        self.assertAvailableAfter(TTL + 1, 404)

    def test_expiry_removes_the_uploads(self, run):
        body = self.submit_batch([_upload(), _upload()]).json()
        self.assertEqual(len(os.listdir(self.upload_dir)), 2)  # one work dir per file

        self.clock.advance(TTL)
        self.assertEqual(self.batch_status(body["batch_id"]).status_code, 404)

        self.assertNoUploadsLeft()


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class BatchAsyncDisabledTests(BatchJobsTestCase):
    @override_settings(MES_ASYNC_ENABLED=False)
    def test_both_routes_are_404(self, run):
        self.assertEqual(self.submit_batch([_upload()]).status_code, 404)
        self.assertEqual(self.batch_status("anything").status_code, 404)
        run.assert_not_called()
        self.assertNoUploadsLeft()


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class BatchCapacityTests(BatchJobsTestCase):
    executor_class = DeferredExecutor
    max_jobs = 3

    def test_room_for_exactly_n_jobs_is_accepted(self, run):
        response = self.submit_batch([_upload(name=f"{n}.jpg") for n in range(3)])

        self.assertEqual(response.status_code, 202)

    def test_room_for_n_minus_one_refuses_the_whole_batch_and_creates_no_job(self, run):
        self.assertEqual(self.submit_batch([_upload(), _upload()]).status_code, 202)  # 1 slot left

        response = self.submit_batch([_upload(), _upload()])

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["errors"])
        self.assertEqual(len(self.executor.pending), 2)  # only the first batch's jobs
        self.assertEqual(len(os.listdir(self.upload_dir)), 2)  # no upload left by the refused batch
        self.assertEqual(self.submit().status_code, 202)  # the free slot is still usable

    def test_a_single_job_after_a_refused_batch_still_gets_the_slots(self, run):
        self.assertEqual(self.submit_batch([_upload(name=f"{n}.jpg") for n in range(4)]).status_code, 503)
        self.assertEqual(self.executor.pending, [])
        self.assertEqual(self.submit_batch([_upload(name=f"{n}.jpg") for n in range(3)]).status_code, 202)


@mock.patch("apps.extraction.adapters.base.subprocess.run", side_effect=_ok)
class BatchValidationTests(BatchJobsTestCase):
    def assertRejected(self, response, status_code):
        self.assertEqual(response.status_code, status_code)
        body = response.json()
        self.assertTrue(body["errors"])
        self.assertIn("request_id", body)
        self.assertNoUploadsLeft()
        self.assertEqual(self.executor.pending if hasattr(self.executor, "pending") else [], [])

    def test_no_files_is_400(self, run):
        self.assertRejected(self.client.post(SUBMIT, {"mime_type": "image/jpeg"}, format="multipart"), 400)

    @override_settings(MES_MAX_FILES_PER_REQUEST=3)
    def test_file_count_boundary(self, run):
        self.assertEqual(self.submit_batch([_upload(name=f"{n}.jpg") for n in range(3)]).status_code, 202)
        self.assertRejected(self.submit_batch([_upload(name=f"{n}.jpg") for n in range(4)]), 400)

    @override_settings(MES_MAX_UPLOAD_BYTES=10)
    def test_total_size_boundary(self, run):
        self.assertEqual(self.submit_batch([_upload(b"x" * 5), _upload(b"x" * 5)]).status_code, 202)
        self.assertRejected(self.submit_batch([_upload(b"x" * 5), _upload(b"x" * 6)]), 413)

    @override_settings(MES_MAX_UPLOAD_BYTES=100, DATA_UPLOAD_MAX_MEMORY_SIZE=50)
    def test_declared_length_over_limit_plus_overhead_is_413_without_parsing(self, run):
        with mock.patch("apps.api.views.BatchExtractionRequestSerializer") as serializer:
            response = self.client.post(
                SUBMIT, {"files": [_upload(b"x")]}, format="multipart", CONTENT_LENGTH="151"
            )

        self.assertRejected(response, 413)
        serializer.assert_not_called()

    def test_object_identifier_is_rejected(self, run):
        for field in ("object_identifier", "object_identifier_type"):
            with self.subTest(field=field):
                self.assertRejected(self.submit_batch([_upload()], **{field: "abc"}), 400)

    def test_an_empty_file_is_an_invalid_request(self, run):
        self.assertRejected(self.submit_batch([_upload(), _upload(b"", "empty.jpg")]), 400)
