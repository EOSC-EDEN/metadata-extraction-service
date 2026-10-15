import subprocess
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.services.format_identification import FFISResolution
from apps.extraction.services.orchestrator import ExtractionOrchestrator, _log_outcome
from apps.extraction.tool_config import ToolConfig


def _unsaved_tool(name, is_mandatory=False):
    """An in-memory ToolConfig for exercising _run() without settings."""
    return ToolConfig(
        name=name,
        adapter="",
        kind="subprocess",
        invocation_command=name,
        timeout_seconds=5,
        mandatory=is_mandatory,
    )


class ExtractEndToEndTests(TestCase):
    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_extract_uses_the_seeded_default_tool(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[{}]", stderr=""
        )

        result = ExtractionOrchestrator().extract("/tmp/file.jpg")

        self.assertEqual(result.selection_method, "default_tool_ffis_disabled")
        self.assertEqual(result.resolved_tools, ("exiftool", "mediainfo"))
        self.assertEqual(result.results[0].status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.errors, [])

    @override_settings(MES_FFIS_ENABLED=True)
    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_extract_records_ffis_identification_and_warnings(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[{}]", stderr=""
        )
        ffis = mock.Mock()
        ffis.identify.return_value = FFISResolution(
            puid="fmt/43",
            mime_type="image/jpeg",
            outcome="warning",
            engine_warnings=["engines disagree"],
            provenance={"outcome": "warning", "tools": []},
        )

        result = ExtractionOrchestrator(ffis_client=ffis).extract("/tmp/file.jpg")

        self.assertEqual(result.selection_method, "default_tool_ffis_identified")
        self.assertEqual((result.puid, result.mime_type), ("fmt/43", "image/jpeg"))
        self.assertEqual(result.warnings, ["engines disagree"])
        self.assertEqual(result.ffis_provenance["outcome"], "warning")
        self.assertEqual(result.resolved_tools, ("exiftool", "mediainfo"))


class RunAggregationTests(SimpleTestCase):
    """Aggregation and failure-isolation against the real adapters."""

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_all_tools_succeed(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )
        tools = [_unsaved_tool("exiftool"), _unsaved_tool("mediainfo")]

        result = ExtractionOrchestrator()._run(tools, "stub", "/tmp/file.jpg")

        self.assertEqual({r.tool_name for r in result.results}, {"exiftool", "mediainfo"})
        self.assertTrue(all(r.status == ToolExecutionStatus.SUCCESS for r in result.results))
        self.assertEqual(result.errors, [])

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_optional_tool_failure_does_not_suppress_other_results(self, mock_run):
        def side_effect(command, **kwargs):
            if command[0] == "mediainfo":
                raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr="boom")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="{}", stderr="")

        mock_run.side_effect = side_effect
        tools = [_unsaved_tool("exiftool"), _unsaved_tool("mediainfo")]

        result = ExtractionOrchestrator()._run(tools, "stub", "/tmp/file.jpg")

        statuses = {r.tool_name: r.status for r in result.results}
        self.assertEqual(statuses["exiftool"], ToolExecutionStatus.SUCCESS)
        self.assertEqual(statuses["mediainfo"], ToolExecutionStatus.FAILURE)
        self.assertEqual(result.errors, [])  # mediainfo isn't mandatory here
        # ...but the failure is still surfaced at the top level (MES-REQ-5-001).
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("mediainfo", result.warnings[0])
        self.assertIn("boom", result.warnings[0])

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_mandatory_tool_failure_surfaces_top_level_error_but_keeps_other_results(
        self, mock_run
    ):
        def side_effect(command, **kwargs):
            if command[0] == "exiftool":
                raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr="boom")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="{}", stderr="")

        mock_run.side_effect = side_effect
        tools = [_unsaved_tool("exiftool", is_mandatory=True), _unsaved_tool("mediainfo")]

        result = ExtractionOrchestrator()._run(tools, "stub", "/tmp/file.jpg")

        self.assertEqual(len(result.results), 2)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("exiftool", result.errors[0])
        self.assertIn("boom", result.errors[0])
        mediainfo_result = next(r for r in result.results if r.tool_name == "mediainfo")
        self.assertEqual(mediainfo_result.status, ToolExecutionStatus.SUCCESS)

    def test_adapter_raising_unexpectedly_is_caught_as_a_second_safety_net(self):
        class ExplodingAdapter:
            def __init__(self, **kwargs):
                pass

            def run(self, file_path):
                raise RuntimeError("boom")

        with mock.patch(
            "apps.extraction.services.orchestrator.get_adapter_class",
            return_value=ExplodingAdapter,
        ):
            result = ExtractionOrchestrator()._run(
                [_unsaved_tool("exiftool")], "stub", "/tmp/file.jpg"
            )

        self.assertEqual(result.results[0].status, ToolExecutionStatus.FAILURE)
        self.assertIn("boom", result.results[0].errors[0])


class ToolStderrWarningTests(SimpleTestCase):
    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_stderr_on_successful_run_becomes_tool_warning(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr="Warning: minor issue\n"
        )
        result = ExtractionOrchestrator()._run([_unsaved_tool("exiftool")], "stub", "/tmp/f.jpg")
        self.assertEqual(result.results[0].status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.results[0].warnings, ["Warning: minor issue"])


class RequestIdLoggingTests(SimpleTestCase):
    """Every log record emitted for a request carries its request_id (MES-REQ-5-001)."""

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_tool_error_and_summary_records_carry_request_id(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="bad file"
        )
        tools = [_unsaved_tool("exiftool", is_mandatory=True)]

        result = ExtractionOrchestrator()._run(tools, "stub", "/tmp/file.jpg")

        with self.assertLogs("apps.extraction.services.orchestrator", level="INFO") as logs:
            _log_outcome(result, "req-123")

        self.assertGreaterEqual(len(logs.records), 2)
        self.assertTrue(any("extraction finished" in r.getMessage() for r in logs.records))
        self.assertEqual({r.request_id for r in logs.records}, {"req-123"})

    def test_console_formatter_renders_request_id(self):
        import logging

        record = logging.LogRecord("apps.x", logging.INFO, __file__, 1, "hello", None, None)
        record.request_id = "req-abc"
        handler = logging.getLogger("apps").handlers[0]

        self.assertIn("request_id=req-abc", handler.format(record))

    def test_console_formatter_falls_back_when_request_id_missing(self):
        import logging

        record = logging.LogRecord("apps.x", logging.INFO, __file__, 1, "hello", None, None)
        handler = logging.getLogger("apps").handlers[0]

        self.assertIn("request_id=-", handler.format(record))
