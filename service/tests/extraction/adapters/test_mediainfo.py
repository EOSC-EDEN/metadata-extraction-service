import subprocess
from unittest import mock

from django.test import SimpleTestCase

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.adapters.mediainfo import MediaInfoAdapter


class MediaInfoAdapterTests(SimpleTestCase):
    def _adapter(self):
        return MediaInfoAdapter(timeout_seconds=5)

    def test_build_command(self):
        command = self._adapter().build_command("/tmp/file.mp4")
        self.assertEqual(command, ["mediainfo", "--Output=JSON", "/tmp/file.mp4"])

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_success_returns_raw_json_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["mediainfo", "--Output=JSON", "/tmp/file.mp4"],
            returncode=0,
            stdout='{"media":{}}',
            stderr="",
        )

        result = self._adapter().run("/tmp/file.mp4")

        self.assertEqual(result.tool_name, "mediainfo")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, '{"media":{}}')
        self.assertEqual(result.raw_output_format, "json")
        self.assertEqual(result.command, ("mediainfo", "--Output=JSON", "/tmp/file.mp4"))

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_non_zero_exit_maps_to_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["mediainfo"], stderr="unsupported file"
        )

        result = self._adapter().run("/tmp/file.mp4")

        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
        self.assertIn("unsupported file", result.errors[0])

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_timeout_maps_to_timeout_status(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["mediainfo"], timeout=5)

        result = self._adapter().run("/tmp/file.mp4")

        self.assertEqual(result.status, ToolExecutionStatus.TIMEOUT)

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_missing_binary_maps_to_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError("mediainfo: command not found")

        result = self._adapter().run("/tmp/file.mp4")

        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
