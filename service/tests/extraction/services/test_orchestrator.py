import subprocess
from unittest import mock

from django.test import SimpleTestCase, TestCase

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.exceptions import ToolNotConfigured
from apps.extraction.models import ExtractionTool
from apps.extraction.services.orchestrator import ExtractionOrchestrator


def _unsaved_tool(name, is_mandatory=False):
    """An in-memory ExtractionTool for exercising _run() without the DB."""
    return ExtractionTool(
        name=name,
        display_name=name,
        adapter_kind=ExtractionTool.AdapterKind.SUBPROCESS,
        invocation_command=name,
        extra_args=[],
        timeout_seconds=5,
        is_enabled=True,
        is_mandatory=is_mandatory,
    )


class ResolveToolsStubTests(TestCase):
    """Covers the stubbed "always default tool" resolver."""

    def test_resolves_to_the_seeded_default_tool(self):
        tools, selection_method = ExtractionOrchestrator()._resolve_tools(
            puid=None, mime_type=None
        )

        self.assertEqual([tool.name for tool in tools], ["exiftool"])
        self.assertEqual(selection_method, "default_tool")

    def test_raises_when_no_default_tool_is_configured(self):
        ExtractionTool.objects.update(is_default=False)

        with self.assertRaises(ToolNotConfigured):
            ExtractionOrchestrator()._resolve_tools(puid=None, mime_type=None)

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_extract_end_to_end_uses_the_seeded_default_tool(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[{}]", stderr=""
        )

        result = ExtractionOrchestrator().extract("/tmp/file.jpg")

        self.assertEqual(result.selection_method, "default_tool")
        self.assertEqual(result.resolved_tools, ("exiftool",))
        self.assertEqual(result.results[0].status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.errors, [])


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
