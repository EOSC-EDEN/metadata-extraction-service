"""Mocked tests for the File Scraper adapter, plus one real run that skips without `scraper`."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.extraction.adapters.base import ErrorCategory, ToolExecutionStatus
from apps.extraction.adapters.file_scraper import FileScraperAdapter

RUN = "apps.extraction.adapters.base.subprocess.run"
COMMAND = ["scraper", "scrape-file", "--tool-info", "/tmp/f"]


class FileScraperAdapterTests(SimpleTestCase):
    def test_metadata(self):
        adapter = FileScraperAdapter()
        self.assertEqual(adapter.tool_name, "file_scraper")
        self.assertEqual(adapter.invocation_command, "scraper")
        self.assertEqual(adapter.raw_output_format, "json")

    def test_build_command(self):
        self.assertEqual(FileScraperAdapter().build_command("/tmp/f"), COMMAND)

    def test_extra_args_precede_the_file_path(self):
        command = FileScraperAdapter(extra_args=["--mimetype", "image/jpeg"]).build_command("/tmp/f")
        self.assertEqual(
            command,
            ["scraper", "scrape-file", "--tool-info", "--mimetype", "image/jpeg", "/tmp/f"],
        )

    @override_settings(MES_FILE_SCRAPER_SKIP_WELLFORMED=True)
    def test_skip_wellformed_flag(self):
        command = FileScraperAdapter(extra_args=["-x"]).build_command("/tmp/f")
        self.assertEqual(
            command, ["scraper", "scrape-file", "--tool-info", "--skip-wellformed-check", "-x", "/tmp/f"]
        )

    @override_settings(MES_FILE_SCRAPER_SKIP_WELLFORMED=False)
    def test_no_skip_flag_when_disabled(self):
        self.assertNotIn("--skip-wellformed-check", FileScraperAdapter().build_command("/tmp/f"))

    def test_invocation_command_override(self):
        command = FileScraperAdapter(invocation_command="/opt/bin/scraper").build_command("/tmp/f")
        self.assertEqual(command[0], "/opt/bin/scraper")

    @mock.patch(RUN)
    def test_success_returns_unmodified_output(self, mock_run):
        raw = '{"path": "/tmp/f", "MIME type": "text/plain", "well-formed": true}'
        mock_run.return_value = subprocess.CompletedProcess(args=COMMAND, returncode=0, stdout=raw, stderr="")
        result = FileScraperAdapter(timeout_seconds=5).run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, raw)
        self.assertEqual(result.raw_output_format, "json")
        self.assertEqual(result.tool_name, "file_scraper")
        self.assertEqual(result.command, tuple(COMMAND))

    @mock.patch(RUN)
    def test_not_wellformed_with_exit_zero_is_success(self, mock_run):
        raw = '{"well-formed": false, "errors": {"PilExtractor": ["Truncated File Read"]}}'
        mock_run.return_value = subprocess.CompletedProcess(args=COMMAND, returncode=0, stdout=raw, stderr="")
        result = FileScraperAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, raw)
        self.assertEqual(result.warnings, ["PilExtractor: Truncated File Read"])

    @mock.patch(RUN)
    def test_not_wellformed_without_errors_still_warns(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=COMMAND, returncode=0, stdout='{"well-formed": false}', stderr=""
        )
        self.assertEqual(len(FileScraperAdapter().run("/tmp/f").warnings), 1)

    @mock.patch(RUN)
    def test_wellformed_true_and_skipped_check_have_no_warnings(self, mock_run):
        for raw in ('{"well-formed": true}', '{"MIME type": "text/plain"}', "not json", "[]"):
            with self.subTest(raw=raw):
                mock_run.return_value = subprocess.CompletedProcess(args=COMMAND, returncode=0, stdout=raw, stderr="")
                self.assertEqual(FileScraperAdapter().run("/tmp/f").warnings, [])

    @mock.patch(RUN)
    def test_stderr_noise_on_exit_zero_is_only_a_warning(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=COMMAND, returncode=0, stdout="{}", stderr="OSError: Truncated File Read"
        )
        result = FileScraperAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.warnings, ["OSError: Truncated File Read"])  # stderr text only

    @mock.patch(RUN)
    def test_non_zero_exit_maps_to_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            2, COMMAND, stderr="Error: Invalid value for 'FILENAME': File '/tmp/f' does not exist."
        )
        result = FileScraperAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)
        self.assertIn("does not exist", result.errors[0])

    @mock.patch(RUN)
    def test_timeout_maps_to_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=COMMAND, timeout=5)
        self.assertEqual(FileScraperAdapter().run("/tmp/f").status, ToolExecutionStatus.TIMEOUT)

    @mock.patch(RUN)
    def test_missing_binary_maps_to_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no such binary")
        self.assertEqual(FileScraperAdapter().run("/tmp/f").status, ToolExecutionStatus.FAILURE)

    def test_error_classification(self):
        adapter = FileScraperAdapter()
        for message, category in [
            ("OSError: Truncated File Read", ErrorCategory.TRUNCATED_OR_BROKEN),
            ("File is encrypted", ErrorCategory.ENCRYPTED),
            ("Unsupported MIME type", ErrorCategory.WRONG_FORMAT),
            ("something odd", ErrorCategory.UNKNOWN),
        ]:
            with self.subTest(message=message):
                self.assertEqual(adapter.classify_error(message), category)


class FileScraperIntegrationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if shutil.which("scraper") is None:
            raise unittest.SkipTest("the `scraper` command is not installed")

    def test_real_run_on_text_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("hello world\n")
            result = FileScraperAdapter(timeout_seconds=120).run(path)
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS, result.errors)
        document = json.loads(result.raw_output)
        self.assertEqual(document["MIME type"], "text/plain")
        self.assertIn("tool_info", document)
