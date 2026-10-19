import subprocess
from datetime import datetime, timezone
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.api import premis
from apps.api.serializers import SERVICE_VERSION
from apps.extraction.adapters.base import ErrorDetail, ToolExecutionStatus, ToolInvocationResult
from apps.extraction.services.orchestrator import AggregatedExtractionResult, classify_statuses
from apps.metrics import metrics

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _tool(name, status=ToolExecutionStatus.SUCCESS, version="1.0", errors=()):
    return ToolInvocationResult(
        tool_name=name,
        tool_version=version,
        status=status,
        raw_output=None,
        raw_output_format=None,
        started_at=NOW,
        finished_at=NOW,
        command=None,
        error_details=tuple(errors),
    )


def _result(tools, errors=(), warnings=()):
    return AggregatedExtractionResult(
        resolved_tools=tuple(t.tool_name for t in tools),
        selection_method="default_tool_caller_identified",
        results=tuple(tools),
        error_details=tuple(errors),
        warnings=list(warnings),
    )


def _event(result, **kwargs):
    kwargs.setdefault("request_id", "req-1")
    kwargs.setdefault("timestamp", NOW.isoformat())
    kwargs.setdefault("filename", "a.bin")
    return premis.build_event(result, **kwargs)


def _outcome(event):
    return event["eventOutcomeInformation"][0]["eventOutcome"]


class OutcomeRuleTests(SimpleTestCase):
    def test_classify_statuses(self):
        self.assertEqual(classify_statuses(["success", "success"], False), "success")
        self.assertEqual(classify_statuses(["success", "failure"], False), "partial")
        self.assertEqual(classify_statuses(["success"], True), "partial")
        self.assertEqual(classify_statuses(["failure", "timeout"], False), "failed")
        self.assertEqual(classify_statuses([], False), "failed")

    def test_event_outcome_follows_classify_outcome(self):
        failed = ToolExecutionStatus.FAILURE
        cases = {
            "success": [_tool("a"), _tool("b")],
            "partial": [_tool("a"), _tool("b", failed)],
            "failed": [_tool("a", failed), _tool("b", ToolExecutionStatus.TIMEOUT)],
        }
        for expected, tools in cases.items():
            result = _result(tools)
            self.assertEqual(metrics.classify_outcome(result), expected)
            self.assertEqual(_outcome(_event(result)), premis.EVENT_OUTCOME[expected])
            self.assertIn(_outcome(_event(result)), {"success", "warning", "fail"})

    def test_outcome_mapping_is_the_loc_terms(self):
        self.assertEqual(premis.EVENT_OUTCOME, {"success": "success", "partial": "warning", "failed": "fail"})


class BuildEventTests(SimpleTestCase):
    def test_identity_type_and_datetime(self):
        event = _event(_result([_tool("a")]))
        self.assertEqual(event["eventIdentifier"], {"eventIdentifierType": "UUID", "eventIdentifierValue": "req-1"})
        self.assertEqual(event["eventType"], "metadata extraction")
        self.assertEqual(event["eventDateTime"], NOW.isoformat())

    def test_detail_names_tools_and_selection_method(self):
        detail = _event(_result([_tool("a"), _tool("b")]))["eventDetailInformation"][0]["eventDetail"]
        self.assertIn("a, b", detail)
        self.assertIn("default_tool_caller_identified", detail)

    def test_linked_agents_are_the_service_plus_each_tool(self):
        event = _event(_result([_tool("a"), _tool("b")]))
        values = [x["linkingAgentIdentifierValue"] for x in event["linkingAgentIdentifier"]]
        self.assertEqual(values, ["mes-service", "a", "b"])
        for link in event["linkingAgentIdentifier"]:
            self.assertEqual(link["linkingAgentIdentifierType"], "MES")
            self.assertEqual(link["linkingAgentRole"], ["executing program"])

    def test_object_link_uses_the_callers_identifier(self):
        event = _event(_result([_tool("a")]), object_identifier="u-1", object_identifier_type="UUID")
        self.assertEqual(
            event["linkingObjectIdentifier"],
            [{"linkingObjectIdentifierType": "UUID", "linkingObjectIdentifierValue": "u-1"}],
        )

    def test_object_link_falls_back_to_the_filename(self):
        event = _event(_result([_tool("a")]))
        self.assertEqual(
            event["linkingObjectIdentifier"],
            [{"linkingObjectIdentifierType": "filename", "linkingObjectIdentifierValue": "a.bin"}],
        )

    def test_errors_and_warnings_become_prefixed_notes(self):
        result = _result(
            [_tool("a", ToolExecutionStatus.FAILURE)],
            errors=[ErrorDetail("Mandatory tool 'a' failed", "encrypted")],
            warnings=["Tool 'b' did not complete"],
        )
        info = _event(result)["eventOutcomeInformation"]
        self.assertEqual(len(info), 1)
        notes = [d["eventOutcomeDetailNote"] for d in info[0]["eventOutcomeDetail"]]
        self.assertEqual(
            notes, ["error: [encrypted] Mandatory tool 'a' failed", "warning: Tool 'b' did not complete"]
        )

    def test_clean_run_has_no_outcome_detail(self):
        self.assertNotIn("eventOutcomeDetail", _event(_result([_tool("a")]))["eventOutcomeInformation"][0])

    def test_only_the_defined_vocabulary_is_emitted(self):
        event = _event(_result([_tool("a"), _tool("b", ToolExecutionStatus.FAILURE)]))
        self.assertEqual(event["eventType"], premis.EVENT_TYPE)
        roles = {r for link in event["linkingAgentIdentifier"] for r in link["linkingAgentRole"]}
        self.assertEqual(roles, {premis.AGENT_ROLE})
        agents = premis.build_agents(_result([_tool("a")]), service_version="9")
        self.assertEqual({a["agentType"] for a in agents}, {premis.AGENT_TYPE})


class BuildAgentsTests(SimpleTestCase):
    def test_service_first_then_tools_with_versions(self):
        agents = premis.build_agents(_result([_tool("a", version="2.1"), _tool("b", version=None)]), service_version="0.9")
        ids = [a["agentIdentifier"][0]["agentIdentifierValue"] for a in agents]
        self.assertEqual(ids, ["mes-service", "a", "b"])
        self.assertEqual(agents[0]["agentVersion"], "0.9")
        self.assertEqual(agents[1]["agentVersion"], "2.1")
        self.assertNotIn("agentVersion", agents[2])

    def test_every_linked_agent_has_an_agent(self):
        result = _result([_tool("a"), _tool("b")])
        linked = [x["linkingAgentIdentifierValue"] for x in _event(result)["linkingAgentIdentifier"]]
        agents = [a["agentIdentifier"][0]["agentIdentifierValue"] for a in premis.build_agents(result, service_version="1")]
        self.assertEqual(linked, agents)


def _upload():
    return SimpleUploadedFile("photo.jpg", b"data", content_type="image/jpeg")


@mock.patch("apps.extraction.adapters.base.subprocess.run")
class EventEndpointTests(TestCase):
    url = "/api/v1/extract/"

    def setUp(self):
        self.client = APIClient()

    def _post(self, **fields):
        return self.client.post(
            self.url, {"file": _upload(), "mime_type": "image/jpeg", **fields}, format="multipart"
        )

    def test_event_and_agents_on_a_successful_response(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        body = self._post().json()
        prov = body["provenance"]
        event = prov["event"]
        self.assertEqual(event["eventIdentifier"]["eventIdentifierValue"], body["request_id"])
        self.assertEqual(event["eventDateTime"], prov["timestamp"])
        self.assertEqual(_outcome(event), "success")
        self.assertEqual(len(prov["agents"]), 1 + len(body["results"]))
        self.assertEqual(prov["agents"][0]["agentVersion"], SERVICE_VERSION)

    def test_all_failed_run_still_has_event_and_agents(self, run):
        run.side_effect = OSError("boom")
        prov = self._post().json()["provenance"]
        self.assertEqual(_outcome(prov["event"]), "fail")
        self.assertTrue(prov["agents"])
        self.assertTrue(prov["event"]["eventOutcomeInformation"][0]["eventOutcomeDetail"])

    def test_object_identifier_is_echoed(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        event = self._post(object_identifier="u-1", object_identifier_type="UUID").json()["provenance"]["event"]
        link = event["linkingObjectIdentifier"][0]
        self.assertEqual(link["linkingObjectIdentifierType"], "UUID")
        self.assertEqual(link["linkingObjectIdentifierValue"], "u-1")

    def test_without_object_identifier_the_filename_is_linked(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        link = self._post().json()["provenance"]["event"]["linkingObjectIdentifier"][0]
        self.assertEqual(link["linkingObjectIdentifierType"], "filename")
        self.assertEqual(link["linkingObjectIdentifierValue"], "photo.jpg")

    def test_only_one_of_the_two_fields_is_a_400_naming_both(self, run):
        for fields in ({"object_identifier": "u-1"}, {"object_identifier_type": "UUID"}):
            response = self._post(**fields)
            self.assertEqual(response.status_code, 400, fields)
            text = " ".join(response.json()["errors"])
            self.assertIn("object_identifier", text)
            self.assertIn("object_identifier_type", text)
        run.assert_not_called()

    def test_length_boundaries(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        cases = [
            ("x" * 255, "UUID", 200),
            ("x" * 256, "UUID", 400),
            ("x", "t" * 64, 200),
            ("x", "t" * 65, 400),
        ]
        for value, kind, expected in cases:
            response = self._post(object_identifier=value, object_identifier_type=kind)
            self.assertEqual(response.status_code, expected, (len(value), len(kind)))
