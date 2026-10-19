import os
import tempfile
from unittest import mock

from django.test import override_settings
from rest_framework.test import APIClient

from tests.api.test_jobs_view import SUBMIT, JobsViewTestCase, _tool_output, _upload

RULES = "rules:\n- {name: jpeg-only, extensions: [JPG], tools: [exiftool]}\n"


def _volatile_stripped(body):
    body.pop("request_id")
    body["provenance"].pop("timestamp")
    event = body["provenance"]["event"]
    event.pop("eventDateTime")
    event.pop("eventIdentifier")
    for item in body["results"]:
        for key in ("started_at", "finished_at", "command"):
            item.pop(key)
    return body


@mock.patch("apps.extraction.adapters.base.subprocess.run", return_value=_tool_output())
class LocalRuleEndpointTests(JobsViewTestCase):
    def setUp(self):
        super().setUp()
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(RULES)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        override = override_settings(MES_TOOL_RULES_FILE=handle.name)
        override.enable()
        self.addCleanup(override.disable)

    def extract(self, **fields):
        fields.setdefault("file", _upload())
        return APIClient().post("/api/v1/extract/", fields, format="multipart")

    def test_sync_result_reports_rule_selection(self, _run):
        body = self.extract().json()
        resolution = body["tool_resolution"]
        self.assertEqual(resolution["selection_method"], "local_rule")
        self.assertEqual(resolution["matched_rules"], ["jpeg-only"])
        self.assertEqual(resolution["resolved_tools"], ["exiftool"])
        self.assertEqual(body["provenance"]["matched_rules"], ["jpeg-only"])
        self.assertIn("jpeg-only", body["provenance"]["event"]["eventDetailInformation"][0]["eventDetail"])

    def test_extension_without_a_rule_runs_all_enabled_tools(self, _run):
        body = self.extract(file=_upload(name="photo.png")).json()
        resolution = body["tool_resolution"]
        self.assertEqual(resolution["selection_method"], "default_tool_ffis_disabled")
        self.assertEqual(resolution["matched_rules"], [])
        self.assertEqual(resolution["resolved_tools"], ["exiftool", "mediainfo"])

    def test_async_result_equals_the_sync_result(self, _run):
        sync = self.extract().json()
        job_id = self.submit(mime_type="").json()["job_id"]
        result = self.client.get(f"{SUBMIT}{job_id}/result/").json()
        self.assertEqual(result["tool_resolution"]["matched_rules"], ["jpeg-only"])
        self.assertEqual(_volatile_stripped(result), _volatile_stripped(sync))

    def test_xml_carries_matched_rules(self, _run):
        response = APIClient().post(
            "/api/v1/extract/", {"file": _upload()}, format="multipart", HTTP_ACCEPT="application/xml"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<matchedRules><item>jpeg-only</item></matchedRules>", response.content)
