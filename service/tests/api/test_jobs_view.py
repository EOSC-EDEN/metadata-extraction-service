import os
import subprocess
import tempfile
import time
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.api.jobs import InMemoryJobStore, JobService, get_job_service
from tests.api.job_helpers import DeferredExecutor, FakeClock, ImmediateExecutor

TTL = 100
SUBMIT = "/api/v1/jobs/"


def _upload(content=b"data", name="photo.jpg"):
    return SimpleUploadedFile(name, content, content_type="image/jpeg")


def _tool_output():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout='[{"a": 1}]', stderr="")


class JobsViewTestCase(TestCase):
    executor_class = ImmediateExecutor
    max_jobs = 10

    def setUp(self):
        self.client = APIClient()
        self.clock = FakeClock()
        self.executor = self.executor_class()
        service = JobService(
            store=InMemoryJobStore(max_jobs=self.max_jobs),
            executor=self.executor,
            ttl_seconds=TTL,
            clock=self.clock,
        )
        patcher = mock.patch("apps.api.views.get_job_service", return_value=service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def submit(self, **fields):
        fields.setdefault("file", _upload())
        fields.setdefault("mime_type", "image/jpeg")
        return self.client.post(SUBMIT, fields, format="multipart")


@mock.patch("apps.extraction.adapters.base.subprocess.run", return_value=_tool_output())
class SubmitAndFetchTests(JobsViewTestCase):
    def test_submit_returns_202_with_location_and_envelope(self, _run):
        response = self.submit()
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(set(body), {"job_id", "status", "created_at", "errors"})
        self.assertEqual(response["Location"], f"{SUBMIT}{body['job_id']}/")
        self.assertEqual(body["errors"], [])

    def test_status_then_result_equals_the_sync_result(self, _run):
        job_id = self.submit().json()["job_id"]
        status = self.client.get(f"{SUBMIT}{job_id}/")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "succeeded")

        result = self.client.get(f"{SUBMIT}{job_id}/result/")
        self.assertEqual(result.status_code, 200)
        sync = self.client.post(
            "/api/v1/extract/", {"file": _upload(), "mime_type": "image/jpeg"}, format="multipart"
        ).json()
        async_body = result.json()
        for body in (sync, async_body):
            body.pop("request_id")
            body["provenance"].pop("timestamp")
            event = body["provenance"]["event"]
            event.pop("eventDateTime")
            event.pop("eventIdentifier")  # both differ per request
            for item in body["results"]:
                item.pop("started_at")
                item.pop("finished_at")
                item.pop("command")  # names the per-request temp file
        self.assertEqual(async_body, sync)

    def test_async_result_carries_the_object_identifier_like_the_sync_result(self, _run):
        ids = {"object_identifier": "u-1", "object_identifier_type": "UUID"}
        job_id = self.submit(**ids).json()["job_id"]
        link = self.client.get(f"{SUBMIT}{job_id}/result/").json()["provenance"]["event"][
            "linkingObjectIdentifier"
        ]
        sync = self.client.post(
            "/api/v1/extract/",
            {"file": _upload(), "mime_type": "image/jpeg", **ids},
            format="multipart",
        ).json()["provenance"]["event"]["linkingObjectIdentifier"]
        self.assertEqual(link, sync)
        self.assertEqual(link[0]["linkingObjectIdentifierValue"], "u-1")

    def test_one_object_identifier_field_is_a_400(self, _run):
        self.assertEqual(self.submit(object_identifier="u-1").status_code, 400)

    def test_result_as_xml(self, _run):
        job_id = self.submit().json()["job_id"]
        response = self.client.get(f"{SUBMIT}{job_id}/result/", HTTP_ACCEPT="application/xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/xml", response["Content-Type"])
        self.assertIn(b"<extractionResult", response.content)

    def test_envelope_as_xml(self, _run):
        response = self.client.post(
            SUBMIT, {"file": _upload()}, format="multipart", HTTP_ACCEPT="application/xml"
        )
        self.assertEqual(response.status_code, 202)
        self.assertIn(b"<jobId>", response.content)

    def test_async_result_request_id_is_the_submitting_requests(self, _run):
        job_id = self.submit().json()["job_id"]
        body = self.client.get(f"{SUBMIT}{job_id}/result/").json()
        self.assertTrue(body["request_id"])

    def test_unknown_job_is_404_for_status_and_result(self, _run):
        for suffix in ("", "result/"):
            with self.subTest(suffix=suffix):
                response = self.client.get(f"{SUBMIT}does-not-exist/{suffix}")
                self.assertEqual(response.status_code, 404)
                self.assertTrue(response.json()["errors"])

    def test_expired_job_is_404_at_the_ttl_but_not_just_before(self, _run):
        job_id = self.submit().json()["job_id"]
        self.clock.advance(TTL - 1)
        self.assertEqual(self.client.get(f"{SUBMIT}{job_id}/").status_code, 200)
        self.clock.advance(1)
        self.assertEqual(self.client.get(f"{SUBMIT}{job_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"{SUBMIT}{job_id}/result/").status_code, 404)

    def test_temp_upload_is_removed_after_completion(self, _run):
        seen = []

        def fake_extract(_self, path, **kwargs):
            self.assertTrue(os.path.exists(path))
            seen.append(path)
            return _real_result()

        with mock.patch(
            "apps.extraction.services.orchestrator.ExtractionOrchestrator.extract",
            autospec=True,
            side_effect=fake_extract,
        ):
            self.assertEqual(self.submit().status_code, 202)
        self.assertEqual(len(seen), 1)
        self.assertFalse(os.path.exists(os.path.dirname(seen[0])))

    def test_orchestrator_exception_makes_a_failed_job(self, _run):
        with mock.patch(
            "apps.extraction.services.orchestrator.ExtractionOrchestrator.extract",
            side_effect=RuntimeError("boom"),
        ), self.assertLogs("apps.api.jobs", "ERROR"):
            job_id = self.submit().json()["job_id"]
        status = self.client.get(f"{SUBMIT}{job_id}/").json()
        self.assertEqual(status["status"], "failed")
        self.assertTrue(status["errors"])
        result = self.client.get(f"{SUBMIT}{job_id}/result/")
        self.assertEqual(result.status_code, 409)

    def test_tool_not_configured_fails_the_job(self, _run):
        with override_settings(MES_DEFAULT_TOOL=""):
            job_id = self.submit().json()["job_id"]
        self.assertEqual(self.client.get(f"{SUBMIT}{job_id}/").json()["status"], "failed")


def _real_result():
    from apps.extraction.services.orchestrator import AggregatedExtractionResult

    return AggregatedExtractionResult(resolved_tools=(), selection_method="x", results=())


@override_settings(MES_ASYNC_ENABLED=False)
class AsyncDisabledTests(JobsViewTestCase):
    executor_class = DeferredExecutor

    @mock.patch("apps.extraction.services.orchestrator.ExtractionOrchestrator.extract")
    def test_every_jobs_route_is_404_and_nothing_is_queued(self, mock_extract):
        for response in (
            self.submit(),
            self.client.get(f"{SUBMIT}some-id/"),
            self.client.get(f"{SUBMIT}some-id/result/"),
        ):
            self.assertEqual(response.status_code, 404)
            self.assertIn("disabled", response.json()["errors"][0])
        self.assertEqual(self.executor.pending, [])
        mock_extract.assert_not_called()

    @mock.patch("apps.extraction.adapters.base.subprocess.run", return_value=_tool_output())
    def test_sync_extract_is_unaffected(self, _run):
        response = self.client.post(
            "/api/v1/extract/", {"file": _upload(), "mime_type": "image/jpeg"}, format="multipart"
        )
        self.assertEqual(response.status_code, 200)


class UnfinishedJobTests(JobsViewTestCase):
    executor_class = DeferredExecutor

    @mock.patch("apps.extraction.adapters.base.subprocess.run", return_value=_tool_output())
    def test_unfinished_job_is_gone_at_the_ttl(self, _run):
        job_id = self.submit().json()["job_id"]
        self.clock.advance(TTL - 1)
        self.assertEqual(self.client.get(f"{SUBMIT}{job_id}/").status_code, 200)
        self.clock.advance(1)
        self.assertEqual(self.client.get(f"{SUBMIT}{job_id}/").status_code, 404)

    @mock.patch("apps.extraction.adapters.base.subprocess.run", return_value=_tool_output())
    def test_result_is_409_until_finished(self, _run):
        job_id = self.submit().json()["job_id"]
        self.assertEqual(self.client.get(f"{SUBMIT}{job_id}/").json()["status"], "pending")
        response = self.client.get(f"{SUBMIT}{job_id}/result/")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["errors"])
        self.executor.run_all()
        self.assertEqual(self.client.get(f"{SUBMIT}{job_id}/result/").status_code, 200)


class CapacityViewTests(JobsViewTestCase):
    executor_class = DeferredExecutor
    max_jobs = 1

    def test_503_when_full_and_upload_removed(self):
        self.assertEqual(self.submit().status_code, 202)
        created = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(*args, **kwargs):
            created.append(real_mkdtemp(*args, **kwargs))
            return created[-1]

        with mock.patch("apps.api.views.tempfile.mkdtemp", side_effect=spy):
            response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["errors"])
        self.assertEqual(len(created), 1)
        self.assertFalse(os.path.exists(created[0]))


class SubmitValidationTests(JobsViewTestCase):
    @mock.patch("apps.extraction.services.orchestrator.ExtractionOrchestrator.extract")
    def test_invalid_submissions_create_no_job_and_run_no_tool(self, mock_extract):
        missing = self.client.post(SUBMIT, {}, format="multipart")
        self.assertEqual(missing.status_code, 400)
        self.assertTrue(missing.json()["request_id"])
        self.assertEqual(self.submit(file=_upload(b"")).status_code, 400)
        mock_extract.assert_not_called()

    @override_settings(MES_MAX_UPLOAD_BYTES=10)
    @mock.patch("apps.extraction.services.orchestrator.ExtractionOrchestrator.extract")
    def test_upload_limit_boundaries(self, mock_extract):
        mock_extract.return_value = _real_result()
        self.assertEqual(self.submit(file=_upload(b"x" * 10)).status_code, 202)
        self.assertEqual(self.submit(file=_upload(b"x" * 11)).status_code, 413)
        self.assertEqual(mock_extract.call_count, 1)

    def test_get_on_collection_is_405(self):
        self.assertEqual(self.client.get(SUBMIT).status_code, 405)


@override_settings(MES_ASYNC_MAX_WORKERS=1, MES_ASYNC_MAX_JOBS=5, MES_JOB_TTL_SECONDS=60)
class DefaultServiceTests(TestCase):
    """The settings-built service really runs jobs on its background thread pool."""

    def setUp(self):
        get_job_service.cache_clear()  # rebuild from the overridden settings
        self.addCleanup(get_job_service.cache_clear)

    @mock.patch("apps.extraction.adapters.base.subprocess.run", return_value=_tool_output())
    def test_job_finishes_on_the_background_pool(self, _run):
        client = APIClient()
        response = client.post(SUBMIT, {"file": _upload(), "mime_type": "image/jpeg"}, format="multipart")
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 10
        status = None
        while time.monotonic() < deadline:
            status = client.get(f"{SUBMIT}{job_id}/").json()["status"]
            if status in ("succeeded", "failed"):
                break
            time.sleep(0.02)
        self.assertEqual(status, "succeeded")
        self.assertEqual(client.get(f"{SUBMIT}{job_id}/result/").status_code, 200)
