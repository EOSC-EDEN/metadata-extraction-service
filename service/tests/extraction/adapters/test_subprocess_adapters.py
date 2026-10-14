"""Mocked tests for the CLI adapters: FFprobe and ImageMagick."""

import subprocess
from unittest import mock

from django.test import SimpleTestCase

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.adapters.ffprobe import FFprobeAdapter
from apps.extraction.adapters.imagemagick import ImageMagickAdapter

RUN = "apps.extraction.adapters.base.subprocess.run"

CASES = [
    (FFprobeAdapter, "ffprobe", "json", ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "/tmp/f"]),
    (ImageMagickAdapter, "imagemagick", "text", ["identify", "-verbose", "/tmp/f"]),
]


class SubprocessAdapterTests(SimpleTestCase):
    def test_build_command_and_metadata(self):
        for adapter_class, name, output_format, command in CASES:
            with self.subTest(tool=name):
                adapter = adapter_class(timeout_seconds=5)
                self.assertEqual(adapter.build_command("/tmp/f"), command)
                self.assertEqual(adapter.tool_name, name)
                self.assertEqual(adapter.raw_output_format, output_format)

    def test_extra_args_precede_the_file_path(self):
        for adapter_class, name, _, _ in CASES:
            with self.subTest(tool=name):
                command = adapter_class(extra_args=["--x"]).build_command("/tmp/f")
                self.assertIn("--x", command)
                self.assertEqual(command[-1], "/tmp/f")

    def test_invocation_command_override(self):
        command = ImageMagickAdapter(invocation_command="magick", extra_args=["identify"]).build_command("/tmp/f")
        self.assertEqual(command, ["magick", "identify", "-verbose", "/tmp/f"])

    @mock.patch(RUN)
    def test_success_returns_unmodified_output(self, mock_run):
        for adapter_class, name, output_format, command in CASES:
            with self.subTest(tool=name):
                mock_run.return_value = subprocess.CompletedProcess(args=command, returncode=0, stdout="RAW", stderr="")
                result = adapter_class(timeout_seconds=5).run("/tmp/f")
                self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
                self.assertEqual(result.raw_output, "RAW")
                self.assertEqual(result.raw_output_format, output_format)
                self.assertEqual(result.tool_name, name)
                self.assertEqual(result.command, tuple(command))

    @mock.patch(RUN)
    def test_stderr_on_success_is_a_warning(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="x", stderr="minor issue")
        result = FFprobeAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.warnings, ["minor issue"])

    @mock.patch(RUN)
    def test_non_zero_exit_maps_to_failure(self, mock_run):
        for adapter_class, name, _, _ in CASES:
            with self.subTest(tool=name):
                mock_run.side_effect = subprocess.CalledProcessError(1, ["x"], stderr="bad data")
                result = adapter_class().run("/tmp/f")
                self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
                self.assertIn("bad data", result.errors[0])

    @mock.patch(RUN)
    def test_timeout_maps_to_timeout(self, mock_run):
        for adapter_class, name, _, _ in CASES:
            with self.subTest(tool=name):
                mock_run.side_effect = subprocess.TimeoutExpired(cmd=["x"], timeout=5)
                self.assertEqual(adapter_class().run("/tmp/f").status, ToolExecutionStatus.TIMEOUT)

    @mock.patch(RUN)
    def test_missing_binary_maps_to_failure(self, mock_run):
        for adapter_class, name, _, _ in CASES:
            with self.subTest(tool=name):
                mock_run.side_effect = FileNotFoundError("no such binary")
                self.assertEqual(adapter_class().run("/tmp/f").status, ToolExecutionStatus.FAILURE)
