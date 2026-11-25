import subprocess
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest import mock
from xml.etree import ElementTree

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
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


def _events(result, **kwargs):
    kwargs.setdefault("timestamp", NOW.isoformat())
    kwargs.setdefault("agent_ids", premis.new_agent_ids(result))
    return premis.build_events(result, **kwargs)


def _summary(result, **kwargs):
    return _events(result, **kwargs)[-1]


def _object(result, **kwargs):
    kwargs.setdefault("filename", "a.bin")
    kwargs.setdefault("events", _events(result))
    return premis.build_object(result, **kwargs)


def _agents(result, **kwargs):
    kwargs.setdefault("service_version", "1")
    kwargs.setdefault("agent_ids", premis.new_agent_ids(result))
    return premis.build_agents(result, **kwargs)


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
            self.assertEqual(_outcome(_summary(result)), premis.EVENT_OUTCOME[expected])
            self.assertIn(_outcome(_summary(result)), {"success", "warning", "fail"})

    def test_outcome_mapping_is_the_loc_terms(self):
        self.assertEqual(premis.EVENT_OUTCOME, {"success": "success", "partial": "warning", "failed": "fail"})


class BuildEventsTests(SimpleTestCase):
    def test_one_event_per_tool_then_the_summary(self):
        for count in (0, 1, 3):
            result = _result([_tool(f"t{n}") for n in range(count)])
            events = _events(result)
            self.assertEqual(len(events), count + 1)
            ids = [e["eventIdentifier"]["eventIdentifierValue"] for e in events]
            self.assertEqual(len(set(ids)), count + 1)
            for value in ids:
                uuid.UUID(value)
            for event in events:
                self.assertEqual(event["eventType"], "metadata extraction")
                self.assertEqual(event["eventIdentifier"]["eventIdentifierType"], "UUID")

    def test_events_are_fresh_per_call(self):
        result = _result([_tool("a")])
        self.assertNotEqual(_events(result)[0]["eventIdentifier"], _events(result)[0]["eventIdentifier"])

    def test_tool_event_interval_comes_from_the_tool_run(self):
        started, finished = NOW, NOW + timedelta(seconds=5)
        tool = replace(_tool("a"), started_at=started, finished_at=finished)
        self.assertEqual(_events(_result([tool]))[0]["eventDateTime"], f"{started.isoformat()}/{finished.isoformat()}")

    def test_tool_event_with_equal_start_and_end_is_still_an_interval(self):
        self.assertEqual(_events(_result([_tool("a")]))[0]["eventDateTime"], f"{NOW.isoformat()}/{NOW.isoformat()}")

    def test_summary_interval_spans_all_tool_runs(self):
        late = NOW + timedelta(seconds=5)
        tools = [
            replace(_tool("a"), started_at=NOW + timedelta(seconds=1), finished_at=late),
            replace(_tool("b"), started_at=NOW, finished_at=NOW + timedelta(seconds=2)),
        ]
        self.assertEqual(_summary(_result(tools))["eventDateTime"], f"{NOW.isoformat()}/{late.isoformat()}")

    def test_summary_datetime_falls_back_to_the_timestamp_when_no_tool_ran(self):
        self.assertEqual(_summary(_result([]), timestamp="T")["eventDateTime"], "T")

    def test_tool_event_detail_is_the_command_line(self):
        tool = replace(_tool("a"), command=("exiftool", "-json", "my file.jpg"))
        detail = _events(_result([tool]))[0]["eventDetailInformation"][0]["eventDetail"]
        self.assertEqual(detail, "Command line: exiftool -json 'my file.jpg'")

    def test_http_tool_event_detail_is_the_request_not_a_command_line(self):
        tools = {"tika": {"adapter": "x", "kind": "http", "invocation_command": "http://tika:9998"}}
        tool = replace(_tool("tika"), command=("PUT", "http://tika:9998/rmeta"))
        with override_settings(MES_TOOLS=tools):
            detail = _events(_result([tool]))[0]["eventDetailInformation"][0]["eventDetail"]
        self.assertEqual(detail, "HTTP PUT http://tika:9998/rmeta")

    def test_tool_event_without_command_has_no_detail(self):
        self.assertNotIn("eventDetailInformation", _events(_result([_tool("a")]))[0])

    def test_tool_event_outcome_follows_the_tool_status(self):
        cases = {
            ToolExecutionStatus.SUCCESS: "success",
            ToolExecutionStatus.FAILURE: "fail",
            ToolExecutionStatus.TIMEOUT: "fail",
        }
        for status, expected in cases.items():
            self.assertEqual(_outcome(_events(_result([_tool("a", status)]))[0]), expected, status)

    def test_tool_event_notes(self):
        tool = replace(
            _tool("a", ToolExecutionStatus.TIMEOUT, errors=[ErrorDetail("too slow")]),
            raw_output_format="json",
            warnings=["careful"],
        )
        info = _events(_result([tool]))[0]["eventOutcomeInformation"][0]
        notes = [d["eventOutcomeDetailNote"] for d in info["eventOutcomeDetail"]]
        self.assertEqual(
            notes,
            [
                "Output format: json",
                "Execution status: timeout",
                "error: too slow",
                "error type: unknown",
                "warning: careful",
            ],
        )

    def test_a_tool_error_is_a_note_followed_by_its_category_note(self):
        tool = _tool("a", ToolExecutionStatus.FAILURE, errors=[ErrorDetail("Unable to parse", "truncated_or_broken")])
        details = _events(_result([tool]))[0]["eventOutcomeInformation"][0]["eventOutcomeDetail"]
        self.assertEqual(
            [d["eventOutcomeDetailNote"] for d in details],
            ["Execution status: failure", "error: Unable to parse", "error type: truncated_or_broken"],
        )

    def test_every_error_gets_its_own_pair_and_nothing_is_an_extension(self):
        tool = _tool("a", ToolExecutionStatus.FAILURE, errors=[ErrorDetail("e1"), ErrorDetail("e2", "encrypted")])
        tool = replace(tool, warnings=["w"])
        result = _result([tool], errors=[ErrorDetail("top")], warnings=["w2"])
        events = _events(result)
        tool_notes = [d["eventOutcomeDetailNote"] for d in events[0]["eventOutcomeInformation"][0]["eventOutcomeDetail"]]
        self.assertEqual(
            tool_notes,
            ["Execution status: failure", "error: e1", "error type: unknown", "error: e2", "error type: encrypted", "warning: w"],
        )
        for event in events:
            for detail in event["eventOutcomeInformation"][0]["eventOutcomeDetail"]:
                self.assertNotIn("eventOutcomeDetailExtension", detail)

    def test_clean_tool_event_only_records_the_execution_status(self):
        info = _events(_result([_tool("a")]))[0]["eventOutcomeInformation"][0]
        self.assertEqual(info["eventOutcomeDetail"], [{"eventOutcomeDetailNote": "Execution status: success"}])

    def test_every_status_is_recorded_exactly_even_when_the_outcome_is_the_same(self):
        for status in ToolExecutionStatus:
            with self.subTest(status=status):
                info = _events(_result([_tool("a", status)]))[0]["eventOutcomeInformation"][0]
                notes = [d["eventOutcomeDetailNote"] for d in info["eventOutcomeDetail"]]
                self.assertIn(f"Execution status: {status.value}", notes)

    def test_tool_event_links_only_its_tool_as_executing_program(self):
        result = _result([_tool("a"), _tool("b")])
        ids = premis.new_agent_ids(result)
        events = _events(result, agent_ids=ids)
        for event, name in zip(events, ("a", "b")):
            (link,) = event["linkingAgentIdentifier"]
            self.assertEqual(link["linkingAgentIdentifierType"], "UUID")
            self.assertEqual(link["linkingAgentIdentifierValue"], ids[name])
            self.assertEqual(link["linkingAgentRole"], ["executing program"])

    def test_summary_links_only_the_service_as_implementer(self):
        result = _result([_tool("a")])
        ids = premis.new_agent_ids(result)
        (link,) = _summary(result, agent_ids=ids)["linkingAgentIdentifier"]
        self.assertEqual(link["linkingAgentIdentifierValue"], ids["mes-service"])
        self.assertEqual(link["linkingAgentRole"], ["implementer"])

    def test_events_carry_no_object_link(self):
        for event in _events(_result([_tool("a")])):
            self.assertNotIn("linkingObjectIdentifier", event)

    def test_summary_detail_names_tools_and_selection_method(self):
        detail = _summary(_result([_tool("a"), _tool("b")]))["eventDetailInformation"][0]["eventDetail"]
        self.assertEqual(detail, "Tools run: a, b; selection_method: default_tool_caller_identified")

    def test_summary_detail_names_local_rules_when_matched(self):
        result = replace(_result([_tool("a")]), matched_rules=("r1", "r2"))
        detail = _summary(result)["eventDetailInformation"][0]["eventDetail"]
        self.assertTrue(detail.endswith("; local rules: r1, r2"))

    def test_summary_detail_without_tools(self):
        detail = _summary(_result([]))["eventDetailInformation"][0]["eventDetail"]
        self.assertTrue(detail.startswith("Tools run: no tools;"))

    def test_summary_errors_and_warnings_become_prefixed_notes(self):
        result = _result(
            [_tool("a", ToolExecutionStatus.FAILURE)],
            errors=[
                ErrorDetail(
                    "Mandatory tool 'a' did not complete successfully (failure). Unable to parse the file.",
                    "encrypted",
                )
            ],
            warnings=["Tool 'b' did not complete successfully (failure). boom"],
        )
        info = _summary(result)["eventOutcomeInformation"]
        self.assertEqual(len(info), 1)
        notes = [d["eventOutcomeDetailNote"] for d in info[0]["eventOutcomeDetail"]]
        self.assertEqual(
            notes,
            [
                "error: Mandatory tool 'a' did not complete successfully (failure). Unable to parse the file.",
                "error type: encrypted",
                "warning: Tool 'b' did not complete successfully (failure). boom",
            ],
        )

    def test_clean_run_summary_has_no_outcome_detail(self):
        self.assertNotIn("eventOutcomeDetail", _summary(_result([_tool("a")]))["eventOutcomeInformation"][0])

    def test_only_the_defined_vocabulary_is_emitted(self):
        result = _result([_tool("a"), _tool("b", ToolExecutionStatus.FAILURE)])
        events = _events(result)
        roles = {r for e in events for link in e["linkingAgentIdentifier"] for r in link["linkingAgentRole"]}
        self.assertEqual(roles, {premis.TOOL_AGENT_ROLE, premis.SERVICE_AGENT_ROLE})
        for event in events:
            self.assertIn(_outcome(event), {"success", "warning", "fail"})
        self.assertEqual({a["agentType"] for a in _agents(result)}, {premis.AGENT_TYPE})


class BuildObjectTests(SimpleTestCase):
    def test_object_uses_the_callers_identifier(self):
        obj = _object(_result([_tool("a")]), object_identifier="u-1", object_identifier_type="UUID")
        self.assertEqual(obj["objectIdentifier"], [{"objectIdentifierType": "UUID", "objectIdentifierValue": "u-1"}])

    def test_object_falls_back_to_the_filepath(self):
        obj = _object(_result([_tool("a")]))
        self.assertEqual(
            obj["objectIdentifier"], [{"objectIdentifierType": "filepath", "objectIdentifierValue": "a.bin"}]
        )

    def test_one_identifier_field_alone_falls_back(self):
        obj = _object(_result([_tool("a")]), object_identifier="u-1")
        self.assertEqual(obj["objectIdentifier"][0]["objectIdentifierType"], "filepath")

    def test_object_is_a_file_and_links_every_event_once(self):
        result = _result([_tool("a"), _tool("b")])
        events = _events(result)
        obj = _object(result, events=events)
        self.assertEqual(obj["objectCategory"], "file")
        linked = [x["linkingEventIdentifierValue"] for x in obj["linkingEventIdentifier"]]
        self.assertEqual(linked, [e["eventIdentifier"]["eventIdentifierValue"] for e in events])
        self.assertEqual(len(set(linked)), 3)
        self.assertEqual({x["linkingEventIdentifierType"] for x in obj["linkingEventIdentifier"]}, {"UUID"})

    def test_format_registries_for_puid_and_mime_type(self):
        result = replace(_result([_tool("a")]), puid="fmt/43", mime_type="image/jpeg")
        (characteristics,) = _object(result)["objectCharacteristics"]
        self.assertEqual(
            characteristics["format"],
            [
                {"formatRegistry": {"formatRegistryName": "PRONOM", "formatRegistryKey": "fmt/43"}},
                {"formatRegistry": {"formatRegistryName": "IANA Media types", "formatRegistryKey": "image/jpeg"}},
            ],
        )

    def test_no_format_registry_has_a_role(self):
        # reviewer's comment: PRONOM is not a specification library, so no formatRegistryRole is given
        for resolution in (
            dict(puid="fmt/43", mime_type="image/jpeg"),
            dict(puid="fmt/43"),
            dict(mime_type="image/jpeg"),
            dict(),
        ):
            with self.subTest(resolution=resolution):
                formats = _object(replace(_result([_tool("a")]), **resolution))["objectCharacteristics"][0]["format"]
                for format_ in formats:
                    self.assertNotIn("formatRegistryRole", format_.get("formatRegistry", {}))

    def test_only_the_known_format_is_listed(self):
        puid_only = _object(replace(_result([_tool("a")]), puid="fmt/43"))["objectCharacteristics"][0]["format"]
        self.assertEqual([f["formatRegistry"]["formatRegistryName"] for f in puid_only], ["PRONOM"])
        mime_only = _object(replace(_result([_tool("a")]), mime_type="image/jpeg"))["objectCharacteristics"][0]["format"]
        self.assertEqual([f["formatRegistry"]["formatRegistryName"] for f in mime_only], ["IANA Media types"])

    def test_unidentified_file_gets_a_designation_because_premis_requires_a_format(self):
        (fmt,) = _object(_result([_tool("a")]))["objectCharacteristics"][0]["format"]
        self.assertEqual(fmt, {"formatDesignation": {"formatName": "unknown"}})


class VocabularyAttributesTests(SimpleTestCase):
    def test_outcome_terms(self):
        for term, code in {"success": "suc", "fail": "fai", "warning": "war"}.items():
            self.assertEqual(
                premis.vocabulary_attributes("eventOutcome", term),
                {
                    "authority": "eventOutcome",
                    "authorityURI": "http://id.loc.gov/vocabulary/preservation/eventOutcome",
                    "valueURI": f"http://id.loc.gov/vocabulary/preservation/eventOutcome/{code}",
                },
            )

    def test_agent_type_and_roles(self):
        self.assertTrue(premis.vocabulary_attributes("agentType", "software")["valueURI"].endswith("/sof"))
        for role, code in {"executing program": "exe", "implementer": "imp"}.items():
            self.assertTrue(premis.vocabulary_attributes("linkingAgentRole", role)["valueURI"].endswith(f"/{code}"))

    def test_unknown_term_or_element_has_no_attributes(self):
        self.assertEqual(premis.vocabulary_attributes("eventOutcome", "partial"), {})
        self.assertEqual(premis.vocabulary_attributes("eventType", "success"), {})

    def test_every_emitted_term_is_in_the_vocabulary(self):
        for outcome in premis.EVENT_OUTCOME.values():
            self.assertTrue(premis.vocabulary_attributes("eventOutcome", outcome))
        self.assertTrue(premis.vocabulary_attributes("agentType", premis.AGENT_TYPE))
        for role in (premis.TOOL_AGENT_ROLE, premis.SERVICE_AGENT_ROLE):
            self.assertTrue(premis.vocabulary_attributes("linkingAgentRole", role))


class BuildAgentsTests(SimpleTestCase):
    def test_service_first_then_tools_with_versions(self):
        result = _result([_tool("a", version="2.1"), _tool("b", version=None)])
        ids = premis.new_agent_ids(result)
        agents = _agents(result, service_version="0.9", agent_ids=ids)
        self.assertEqual(
            [a["agentIdentifier"][0]["agentIdentifierValue"] for a in agents],
            [ids["mes-service"], ids["a"], ids["b"]],
        )
        self.assertEqual(agents[0]["agentVersion"], "0.9")
        self.assertEqual(agents[1]["agentVersion"], "2.1")
        self.assertNotIn("agentVersion", agents[2])

    def test_identifiers_are_distinct_uuids(self):
        ids = premis.new_agent_ids(_result([_tool("a"), _tool("b")]))
        self.assertEqual(len(set(ids.values())), 3)
        for value in ids.values():
            uuid.UUID(value)
        agents = _agents(_result([_tool("a")]))
        self.assertEqual(agents[0]["agentIdentifier"][0]["agentIdentifierType"], "UUID")

    def test_display_name_comes_from_the_tool_config(self):
        tools = {
            "exiftool": {"adapter": "x", "kind": "subprocess", "display_name": "ExifTool"},
            "plain": {"adapter": "x", "kind": "subprocess"},
        }
        with override_settings(MES_TOOLS=tools):
            names = [a["agentName"] for a in _agents(_result([_tool("exiftool"), _tool("plain"), _tool("removed")]))]
        # configured name, no display_name set, tool not in MES_TOOLS: the last two fall back to the tool name
        self.assertEqual(
            names, [["Metadata Extraction Service"], ["ExifTool"], ["plain"], ["removed"]]
        )

    def test_shipped_display_names(self):
        names = [a["agentName"] for a in _agents(_result([_tool(n) for n in ("exiftool", "mediainfo", "ffprobe")]))]
        self.assertEqual(names[1:], [["Exiftool"], ["MediaInfo"], ["ffprobe"]])

    def test_every_linked_agent_has_an_agent(self):
        result = _result([_tool("a"), _tool("b")])
        ids = premis.new_agent_ids(result)
        linked = {
            link["linkingAgentIdentifierValue"]
            for event in _events(result, agent_ids=ids)
            for link in event["linkingAgentIdentifier"]
        }
        agents = {a["agentIdentifier"][0]["agentIdentifierValue"] for a in _agents(result, agent_ids=ids)}
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
            self.url, {"file": _upload(), **fields}, format="multipart"
        )

    def test_object_events_and_agents_on_a_successful_response(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        body = self._post().json()
        prov = body["provenance"]
        events = prov["events"]
        self.assertEqual(len(events), len(body["results"]) + 1)
        for event in events:
            uuid.UUID(event["eventIdentifier"]["eventIdentifierValue"])
            self.assertNotEqual(event["eventIdentifier"]["eventIdentifierValue"], body["request_id"])
            start, end = event["eventDateTime"].split("/")
            self.assertLessEqual(start, end)
        self.assertEqual(_outcome(events[-1]), "success")
        linked = [x["linkingEventIdentifierValue"] for x in prov["object"]["linkingEventIdentifier"]]
        self.assertEqual(linked, [e["eventIdentifier"]["eventIdentifierValue"] for e in events])
        self.assertEqual(len(prov["agents"]), 1 + len(body["results"]))
        self.assertEqual(prov["agents"][0]["agentVersion"], SERVICE_VERSION)
        self.assertNotIn("event", prov)

    def _format_keys(self, body):
        return [
            f["formatRegistry"]["formatRegistryKey"]
            for f in body["provenance"]["object"]["objectCharacteristics"][0]["format"]
        ]

    def test_object_carries_the_callers_puid_and_mime_type_as_registries(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        body = self._post(puid="fmt/43", mime_type="image/jpeg").json()
        self.assertEqual(self._format_keys(body), ["fmt/43", "image/jpeg"])

    def test_object_has_only_the_registry_the_caller_gave(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        self.assertEqual(self._format_keys(self._post(puid="fmt/43").json()), ["fmt/43"])
        self.assertEqual(self._format_keys(self._post(mime_type="image/jpeg").json()), ["image/jpeg"])

    def test_xml_carries_the_vocabulary_attributes(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        response = self.client.post(
            self.url,
            {"file": _upload()},
            format="multipart",
            HTTP_ACCEPT="application/premis+xml",
        )
        ns = {"p": "http://www.loc.gov/premis/v3"}
        root = ElementTree.fromstring(response.content)
        outcome = root.find(".//p:eventOutcome", ns)
        self.assertEqual(outcome.get("valueURI"), "http://id.loc.gov/vocabulary/preservation/eventOutcome/suc")
        self.assertEqual(outcome.get("authority"), "eventOutcome")
        roles = {e.text: e.get("valueURI").rsplit("/", 1)[1] for e in root.iterfind(".//p:linkingAgentRole", ns)}
        self.assertEqual(roles, {"implementer": "imp", "executing program": "exe"})
        obj = root.find(".//p:object", ns)
        self.assertEqual(obj.get("{http://www.w3.org/2001/XMLSchema-instance}type"), "premis:file")
        for agent_type in root.iterfind(".//p:agentType", ns):
            self.assertTrue(agent_type.get("valueURI").endswith("/sof"))
        self.assertEqual(root.find(".//p:eventType", ns).attrib, {})

    def test_all_failed_run_still_has_events_and_agents(self, run):
        run.side_effect = OSError("boom")
        prov = self._post().json()["provenance"]
        self.assertEqual(_outcome(prov["events"][-1]), "fail")
        self.assertTrue(all(_outcome(e) == "fail" for e in prov["events"][:-1]))
        self.assertTrue(prov["agents"])
        self.assertTrue(prov["events"][-1]["eventOutcomeInformation"][0]["eventOutcomeDetail"])

    def test_object_identifier_is_echoed(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        obj = self._post(object_identifier="u-1", object_identifier_type="UUID").json()["provenance"]["object"]
        identifier = obj["objectIdentifier"][0]
        self.assertEqual(identifier["objectIdentifierType"], "UUID")
        self.assertEqual(identifier["objectIdentifierValue"], "u-1")

    def test_without_object_identifier_the_filepath_is_used(self, run):
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        identifier = self._post().json()["provenance"]["object"]["objectIdentifier"][0]
        self.assertEqual(identifier["objectIdentifierType"], "filepath")
        self.assertEqual(identifier["objectIdentifierValue"], "photo.jpg")

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


class ToolSelectionTests(SimpleTestCase):
    """The summary event records how the tools were chosen (MES-REQ-3-009); FFIS has its own event and agent."""

    FFIS = "https://ffis.example/api"

    def _identified(self, **fields):
        return replace(_result([_tool("a")]), puid="fmt/43", mime_type="image/jpeg", **fields)

    def _details(self, event):
        return [d["eventDetail"] for d in event.get("eventDetailInformation", [])]

    def test_the_summary_event_names_the_selection_but_no_longer_the_identification(self):
        result = self._identified(identification_url=self.FFIS)
        self.assertEqual(
            self._details(_summary(result)),
            [
                "Tools run: a; selection_method: default_tool_caller_identified",
                f"Request timestamp: {NOW.isoformat()}",
            ],
        )

    def test_local_rules_stay_in_the_first_detail(self):
        result = self._identified(matched_rules=("rule-a", "rule-b"))
        self.assertTrue(self._details(_summary(result))[0].endswith("local rules: rule-a, rule-b"))

    def test_ffis_is_an_agent_after_the_service_when_it_was_consulted(self):
        result = self._identified(identification_url=self.FFIS)
        ids = premis.new_agent_ids(result)
        agents = _agents(result, agent_ids=ids)

        self.assertEqual([a["agentName"][0] for a in agents][:3], ["Metadata Extraction Service", premis.FFIS_AGENT_NAME, "a"])
        ffis = agents[1]
        # one identifier only, so the link between an event and its agent stays unambiguous (reviewer's comment);
        # the URL of FFIS is an agentNote
        self.assertEqual(
            [(i["agentIdentifierType"], i["agentIdentifierValue"]) for i in ffis["agentIdentifier"]],
            [("UUID", ids["ffis"])],
        )
        self.assertEqual(ffis["agentNote"][0], f"URL: {self.FFIS}")
        self.assertEqual(ffis["agentType"], "software")
        self.assertNotIn("agentVersion", ffis)

    def test_the_ffis_agent_has_a_url_note_whatever_ffis_reported(self):
        for provenance in ({"outcome": "success"}, {}, None):
            with self.subTest(provenance=provenance):
                result = replace(_result([_tool("a")]), identification_url=self.FFIS, ffis_provenance=provenance)
                ffis = _agents(result)[1]
                self.assertEqual(len(ffis["agentIdentifier"]), 1)
                self.assertEqual(ffis["agentNote"][0], f"URL: {self.FFIS}")

    def test_every_agent_is_linked_by_an_event(self):
        result = self._identified(identification_url=self.FFIS)
        ids = premis.new_agent_ids(result)
        linked = {l["linkingAgentIdentifierValue"] for e in _events(result, agent_ids=ids) for l in e["linkingAgentIdentifier"]}
        self.assertEqual(linked, {a["agentIdentifier"][0]["agentIdentifierValue"] for a in _agents(result, agent_ids=ids)})


class IdentificationEventTests(SimpleTestCase):
    """FFIS is recorded as a `format identification` event, before the tool events (section 11 of the plan)."""

    FFIS = "https://ffis.example/api"
    START = datetime(2025, 12, 31, 23, 59, 58, tzinfo=timezone.utc)
    END = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    def _consulted(self, **fields):
        fields.setdefault("ffis_provenance", {"outcome": "ok"})
        fields.setdefault("identification_started_at", self.START)
        fields.setdefault("identification_finished_at", self.END)
        defaults = {
            "puid": "fmt/43",
            "mime_type": "image/jpeg",
            "identification_url": self.FFIS,
            "selection_method": "default_tool_ffis_identified",
        }
        return replace(_result([_tool("a"), _tool("b")]), **{**defaults, **fields})

    def _identification(self, result, **kwargs):
        events = _events(result, **kwargs)
        found = [e for e in events if e["eventType"] == "format identification"]
        self.assertLessEqual(len(found), 1)
        return events, (found[0] if found else None)

    def test_one_event_first_then_the_tools_then_the_summary(self):
        events, event = self._identification(self._consulted())
        self.assertEqual(len(events), 4)
        self.assertIs(events[0], event)
        self.assertEqual([e["eventType"] for e in events], ["format identification", "metadata extraction", "metadata extraction", "metadata extraction"])
        ids = [e["eventIdentifier"]["eventIdentifierValue"] for e in events]
        self.assertEqual(len(set(ids)), 4)
        uuid.UUID(ids[0])

    def test_the_event_links_ffis_as_executing_program(self):
        result = self._consulted()
        ids = premis.new_agent_ids(result)
        _, event = self._identification(result, agent_ids=ids)
        self.assertEqual(
            [(l["linkingAgentIdentifierValue"], l["linkingAgentRole"]) for l in event["linkingAgentIdentifier"]],
            [(ids["ffis"], ["executing program"])],
        )

    def test_the_summary_no_longer_links_ffis(self):
        result = self._consulted()
        ids = premis.new_agent_ids(result)
        links = _summary(result, agent_ids=ids)["linkingAgentIdentifier"]
        self.assertEqual([l["linkingAgentIdentifierValue"] for l in links], [ids["mes-service"]])

    def _notes(self, event):
        return [d["eventOutcomeDetailNote"] for d in event["eventOutcomeInformation"][0].get("eventOutcomeDetail", [])]

    def test_the_puid_and_the_media_type_are_outcome_notes_for_the_known_parts_only(self):
        # reviewer's comment: what FFIS found is the outcome of the event, not its detail
        cases = [
            (dict(), ["PUID: fmt/43", "Media type: image/jpeg"]),
            (dict(mime_type=None), ["PUID: fmt/43"]),
            (dict(puid=None), ["Media type: image/jpeg"]),
            (dict(puid=None, mime_type=None), []),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                _, event = self._identification(self._consulted(**fields))
                self.assertEqual(self._notes(event), expected)

    def test_the_identification_event_has_no_event_detail_and_no_url(self):
        # the URL of FFIS is the agentNote of its agent, so the event does not repeat it
        for fields in (dict(), dict(puid=None, mime_type=None)):
            with self.subTest(fields=fields):
                _, event = self._identification(self._consulted(**fields))
                self.assertNotIn("eventDetailInformation", event)
                self.assertFalse(any(self.FFIS in note for note in self._notes(event)))

    def test_the_notes_come_before_a_warning_of_ffis(self):
        result = self._consulted(warnings=["ffis_unreachable: x"])  # an identified file that also warned
        _, event = self._identification(result)
        self.assertEqual(self._notes(event), ["PUID: fmt/43", "Media type: image/jpeg", "warning: ffis_unreachable: x"])

    def test_the_date_time_is_the_interval_of_the_ffis_call(self):
        _, event = self._identification(self._consulted())
        self.assertEqual(event["eventDateTime"], f"{self.START.isoformat()}/{self.END.isoformat()}")

    def test_equal_start_and_end_is_still_an_interval(self):
        _, event = self._identification(self._consulted(identification_finished_at=self.START))
        self.assertEqual(event["eventDateTime"], f"{self.START.isoformat()}/{self.START.isoformat()}")

    def test_without_times_the_request_timestamp_is_used(self):
        _, event = self._identification(self._consulted(identification_started_at=None, identification_finished_at=None))
        self.assertEqual(event["eventDateTime"], NOW.isoformat())

    def test_the_outcome_is_success_when_ffis_identified_the_file(self):
        for provenance in ({"outcome": "ok"}, {}):  # an empty provenance block is still an identification
            with self.subTest(provenance=provenance):
                _, event = self._identification(self._consulted(ffis_provenance=provenance))
                self.assertEqual(_outcome(event), "success")

    def test_the_outcome_is_fail_with_the_warning_when_ffis_did_not_identify(self):
        cases = {
            "default_tool_ffis_unreachable": "ffis_unreachable: connection refused",
            "default_tool_ffis_inconclusive": "ffis_inconclusive: FFIS could not identify the file's format.",
        }
        for selection, warning in cases.items():
            with self.subTest(selection=selection):
                result = self._consulted(
                    ffis_provenance=None, selection_method=selection, warnings=[warning, "tool warning"]
                )
                result = replace(result, puid=None, mime_type=None)
                _, event = self._identification(result)
                self.assertEqual(_outcome(event), "fail")
                self.assertEqual(self._notes(event), [f"warning: {warning}"])  # only the FFIS warning, not the tools'
                self.assertNotIn("eventDetailInformation", event)

    def test_a_successful_identification_notes_what_it_found_and_nothing_else(self):
        _, event = self._identification(self._consulted())
        self.assertEqual(self._notes(event), ["PUID: fmt/43", "Media type: image/jpeg"])

    def test_local_rules_after_a_failed_identification_still_record_the_failed_call(self):
        result = replace(
            self._consulted(ffis_provenance=None, warnings=["ffis_inconclusive: nothing"]),
            puid=None,
            selection_method="local_rule",
        )
        _, event = self._identification(result)
        self.assertEqual(_outcome(event), "fail")

    def test_no_event_and_no_agent_when_ffis_was_not_consulted(self):
        for url in (None, ""):
            with self.subTest(url=url):
                result = replace(self._consulted(), identification_url=url)
                events, event = self._identification(result)
                self.assertIsNone(event)
                self.assertEqual(len(events), 3)
                self.assertNotIn("ffis", premis.new_agent_ids(result))

    def test_the_object_links_the_identification_event(self):
        result = self._consulted()
        events = _events(result)
        linked = [l["linkingEventIdentifierValue"] for l in premis.build_object(result, filename="a", events=events)["linkingEventIdentifier"]]
        self.assertEqual(linked, [e["eventIdentifier"]["eventIdentifierValue"] for e in events])
        self.assertEqual(len(linked), len(set(linked)))


class FileFixityTests(SimpleTestCase):
    def _file(self, content: bytes):
        import os
        import tempfile

        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.write(content)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_size_and_sha256_of_the_file(self):
        import hashlib

        fixity = premis.file_fixity(self._file(b"abc"))
        self.assertEqual(fixity, {"size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()})

    def test_empty_file_and_chunk_boundaries(self):
        import hashlib

        chunk = premis._HASH_CHUNK_BYTES
        for length in (0, 1, chunk - 1, chunk, chunk + 1, 2 * chunk):
            with self.subTest(length=length):
                content = b"x" * length
                self.assertEqual(
                    premis.file_fixity(self._file(content)),
                    {"size": length, "sha256": hashlib.sha256(content).hexdigest()},
                )

    def test_an_unreadable_file_gives_none(self):
        self.assertIsNone(premis.file_fixity("/nonexistent/file"))

    def test_the_object_has_fixity_then_size_then_format_only_when_the_fixity_is_known(self):
        result = replace(_result([_tool("a")]), puid="fmt/43")
        with_fixity = _object(result, file_fixity={"size": 3, "sha256": "ab" * 32})["objectCharacteristics"][0]
        self.assertEqual(list(with_fixity), ["fixity", "size", "format"])
        self.assertEqual(with_fixity["size"], 3)
        self.assertEqual(with_fixity["fixity"][0]["messageDigest"], "ab" * 32)

        for missing in (None, {}):
            with self.subTest(file_fixity=missing):
                self.assertEqual(list(_object(result, file_fixity=missing)["objectCharacteristics"][0]), ["format"])

    def test_the_algorithm_is_a_controlled_term(self):
        self.assertEqual(
            premis.vocabulary_attributes("messageDigestAlgorithm", "SHA-256")["valueURI"],
            "http://id.loc.gov/vocabulary/preservation/cryptographicHashFunctions/sha256",
        )


class RequestDetailTests(SimpleTestCase):
    """The summary event names the request, the original file name and the request time; agents get the tool key and the FFIS note."""

    def _details(self, **kwargs):
        result = _result([_tool("a")])
        events = _events(result, **kwargs)
        return [d["eventDetail"] for d in events[-1]["eventDetailInformation"]]

    def test_the_summary_event_has_the_request_id_the_original_name_and_the_request_time(self):
        details = self._details(request_id="REQ-1", filename="photo 1.jpg")
        self.assertEqual(
            details[1:],
            ["Request id: REQ-1", "Original name: photo 1.jpg", f"Request timestamp: {NOW.isoformat()}"],
        )

    def test_missing_request_id_or_name_is_left_out(self):
        for kwargs, absent in (({"request_id": None}, "Request id"), ({"filename": ""}, "Original name")):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(any(d.startswith(absent) for d in self._details(**kwargs)))

    def test_tool_events_do_not_carry_the_request_details(self):
        events = _events(_result([_tool("a")]), request_id="REQ-1", filename="f.jpg")
        self.assertNotIn("eventDetailInformation", events[0])

    def test_the_serialised_result_carries_them(self):
        from apps.api.serializers import serialize_extraction_result

        data = serialize_extraction_result(_result([_tool("a")]), "REQ-9", "x.jpg")
        details = [d["eventDetail"] for d in data["provenance"]["events"][-1]["eventDetailInformation"]]
        self.assertIn("Request id: REQ-9", details)
        self.assertIn("Original name: x.jpg", details)
        self.assertIn(f"Request timestamp: {data['provenance']['timestamp']}", details)

    def test_every_agent_has_one_identifier_and_a_tool_agent_names_its_tool_in_the_first_note(self):
        # one agentIdentifier per agent (reviewer's comment); the configured tool name is the first agentNote
        result = _result([_tool("a"), _tool("exiftool")])
        ids = premis.new_agent_ids(result)
        service, a, exiftool = _agents(result, agent_ids=ids)
        self.assertEqual(len(service["agentIdentifier"]), 1)
        self.assertNotIn("agentNote", service)  # the service is no tool
        for agent, key in ((a, "a"), (exiftool, "exiftool")):
            self.assertEqual(
                [(i["agentIdentifierType"], i["agentIdentifierValue"]) for i in agent["agentIdentifier"]],
                [("UUID", ids[key])],
            )
            self.assertEqual(agent["agentNote"], [f"Tool: {key}"])

    def test_the_tool_note_holds_the_configured_name_and_not_the_display_name(self):
        result = _result([_tool("exiftool"), _tool("mediainfo"), _tool("ffprobe")])
        agents = _agents(result)[1:]
        self.assertEqual([a["agentName"][0] for a in agents], ["Exiftool", "MediaInfo", "ffprobe"])
        self.assertEqual([a["agentNote"][0] for a in agents], ["Tool: exiftool", "Tool: mediainfo", "Tool: ffprobe"])

    def test_no_agent_has_more_than_one_identifier_in_any_case(self):
        result = replace(_result([_tool("a"), _tool("b")]), identification_url=self.FFIS_URL, ffis_provenance={"outcome": "success"})
        for agent in _agents(result):
            self.assertEqual(len(agent["agentIdentifier"]), 1, agent["agentName"])

    FFIS_URL = "https://ffis.example/api"

    def test_the_ffis_agent_note_is_short_and_only_from_what_ffis_reported(self):
        provenance = {"outcome": "success", "tools": [{"name": "siegfried"}, {"name": "magika", "error": "x"}, {}]}
        for provenance_value, expected in (
            (provenance, "FFIS provenance, outcome: success; engines: siegfried, magika"),
            ({"outcome": "success"}, "FFIS provenance, outcome: success"),
            ({"tools": [{"name": "a"}]}, "FFIS provenance, engines: a"),
            ({}, None),
            (None, None),
            ("not a dict", None),
        ):
            with self.subTest(provenance=provenance_value):
                result = replace(
                    _result([_tool("a")]), identification_url="https://f", ffis_provenance=provenance_value
                )
                ffis = _agents(result)[1]
                self.assertEqual(ffis["agentNote"], ["URL: https://f"] + ([expected] if expected else []))

    def test_request_level_error_categories_are_notes_of_the_summary_event_without_an_extension(self):
        result = _result([_tool("a")], errors=[ErrorDetail("bad", "wrong_format"), ErrorDetail("worse", "unknown")])
        details = _summary(result)["eventOutcomeInformation"][0]["eventOutcomeDetail"]
        self.assertEqual(
            [d["eventOutcomeDetailNote"] for d in details],
            ["error: bad", "error type: wrong_format", "error: worse", "error type: unknown"],
        )
        self.assertFalse(any("eventOutcomeDetailExtension" in d for d in details))
