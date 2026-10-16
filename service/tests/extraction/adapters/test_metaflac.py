"""Mocked tests for the metaflac adapter (FLAC technical metadata)."""

import subprocess
from unittest import mock

from django.test import SimpleTestCase

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.adapters.metaflac import MetaflacAdapter

RUN = "apps.extraction.adapters.base.subprocess.run"
COMMAND = ["metaflac", "--list", "--block-type=STREAMINFO,VORBIS_COMMENT", "/tmp/f"]


class MetaflacAdapterTests(SimpleTestCase):
    def test_metadata(self):
        adapter = MetaflacAdapter()
        self.assertEqual(adapter.tool_name, "metaflac")
        self.assertEqual(adapter.invocation_command, "metaflac")
        self.assertEqual(adapter.raw_output_format, "text")

    def test_build_command(self):
        self.assertEqual(MetaflacAdapter(timeout_seconds=5).build_command("/tmp/f"), COMMAND)

    def test_extra_args_precede_the_file_path(self):
        command = MetaflacAdapter(extra_args=["--no-utf8-convert"]).build_command("/tmp/f")
        self.assertEqual(
            command,
            ["metaflac", "--list", "--block-type=STREAMINFO,VORBIS_COMMENT", "--no-utf8-convert", "/tmp/f"],
        )

    def test_invocation_command_override(self):
        command = MetaflacAdapter(invocation_command="/opt/bin/metaflac").build_command("/tmp/f")
        self.assertEqual(command[0], "/opt/bin/metaflac")

    @mock.patch(RUN)
    def test_success_returns_unmodified_output(self, mock_run):
        raw = "METADATA block #0\n  type: 0 (STREAMINFO)\n  sample_rate: 44100 Hz\n"
        mock_run.return_value = subprocess.CompletedProcess(args=COMMAND, returncode=0, stdout=raw, stderr="")
        result = MetaflacAdapter(timeout_seconds=5).run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, raw)
        self.assertEqual(result.raw_output_format, "text")
        self.assertEqual(result.tool_name, "metaflac")
        self.assertEqual(result.command, tuple(COMMAND))

    @mock.patch(RUN)
    def test_non_flac_file_maps_to_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, COMMAND, stderr="f: ERROR: not a FLAC file"
        )
        result = MetaflacAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
        self.assertIn("not a FLAC file", result.errors[0])

    @mock.patch(RUN)
    def test_timeout_maps_to_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=COMMAND, timeout=5)
        self.assertEqual(MetaflacAdapter().run("/tmp/f").status, ToolExecutionStatus.TIMEOUT)

    @mock.patch(RUN)
    def test_missing_binary_maps_to_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no such binary")
        self.assertEqual(MetaflacAdapter().run("/tmp/f").status, ToolExecutionStatus.FAILURE)
