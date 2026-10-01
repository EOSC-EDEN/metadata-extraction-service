import subprocess
from unittest import mock

from django.test import SimpleTestCase

from apps.extraction.adapters.base import ToolExecutionStatus
from apps.extraction.adapters.exiftool import ExifToolAdapter


class ExifToolAdapterTests(SimpleTestCase):
    def _adapter(self):
        return ExifToolAdapter(timeout_seconds=5)

    def test_build_command(self):
        command = self._adapter().build_command("/tmp/file.jpg")
        self.assertEqual(command, ["exiftool", "-json", "/tmp/file.jpg"])

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_success_returns_raw_json_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["exiftool", "-json", "/tmp/file.jpg"],
            returncode=0,
            stdout='[{"FileName":"file.jpg"}]',
            stderr="",
        )

        result = self._adapter().run("/tmp/file.jpg")

        self.assertEqual(result.tool_name, "exiftool")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, '[{"FileName":"file.jpg"}]')
        self.assertEqual(result.raw_output_format, "json")
        self.assertEqual(result.command, ("exiftool", "-json", "/tmp/file.jpg"))
        self.assertEqual(result.errors, [])
        mock_run.assert_called_once_with(
            ["exiftool", "-json", "/tmp/file.jpg"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_non_zero_exit_maps_to_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["exiftool"], stderr="bad file format"
        )

        result = self._adapter().run("/tmp/file.jpg")

        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
        self.assertIsNone(result.raw_output)
        self.assertIn("bad file format", result.errors[0])

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_timeout_maps_to_timeout_status(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["exiftool"], timeout=5)

        result = self._adapter().run("/tmp/file.jpg")

        self.assertEqual(result.status, ToolExecutionStatus.TIMEOUT)
        self.assertIsNone(result.raw_output)

    @mock.patch("apps.extraction.adapters.base.subprocess.run")
    def test_missing_binary_maps_to_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError("exiftool: command not found")

        result = self._adapter().run("/tmp/file.jpg")

        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
        self.assertIsNone(result.raw_output)
