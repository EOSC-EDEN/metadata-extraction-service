"""Tests for the JHOVE adapter (technical metadata only; validity status is never an MES verdict)."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from apps.extraction.adapters.base import ErrorCategory, ToolExecutionStatus
from apps.extraction.adapters.jhove import JhoveAdapter

RUN = "apps.extraction.adapters.base.subprocess.run"
COMMAND = ["jhove", "-h", "xml", "/tmp/f"]

REPORT_INVALID = (
    '<?xml version="1.0"?><jhove xmlns="http://schema.openpreservation.org/ois/xml/ns/jhove">'
    "<repInfo uri='/tmp/f'><format>JPEG</format><status>Not well-formed</status>"
    "<messages><message severity='error'>Premature EOF</message></messages>"
    "<properties><property><name>Image</name></property></properties></repInfo></jhove>"
)


class JhoveAdapterTests(SimpleTestCase):
    def test_metadata(self):
        adapter = JhoveAdapter()
        self.assertEqual(adapter.tool_name, "jhove")
        self.assertEqual(adapter.invocation_command, "jhove")
        self.assertEqual(adapter.raw_output_format, "xml")

    def test_build_command_uses_xml_handler_without_checksums(self):
        command = JhoveAdapter().build_command("/tmp/f")
        self.assertEqual(command, COMMAND)
        self.assertNotIn("-k", command)

    def test_extra_args_precede_the_file_path(self):
        command = JhoveAdapter(extra_args=["-m", "PDF-hul"]).build_command("/tmp/f")
        self.assertEqual(command, ["jhove", "-h", "xml", "-m", "PDF-hul", "/tmp/f"])

    @mock.patch(RUN)
    def test_success_returns_unmodified_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(COMMAND, 0, stdout=REPORT_INVALID, stderr="")
        result = JhoveAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, REPORT_INVALID)
        self.assertEqual(result.raw_output_format, "xml")

    @mock.patch(RUN)
    def test_not_well_formed_status_is_not_an_error_or_warning(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(COMMAND, 0, stdout=REPORT_INVALID, stderr="")
        result = JhoveAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])

    @mock.patch(RUN)
    def test_non_zero_exit_with_a_report_is_still_success(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, COMMAND, output=REPORT_INVALID, stderr="")
        result = JhoveAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, REPORT_INVALID)
        self.assertEqual(result.errors, [])

    @mock.patch(RUN)
    def test_non_zero_exit_without_a_report_is_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(2, COMMAND, output="", stderr="usage: jhove")
        result = JhoveAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.FAILURE)

    @mock.patch(RUN)
    def test_timeout_maps_to_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=COMMAND, timeout=5)
        self.assertEqual(JhoveAdapter().run("/tmp/f").status, ToolExecutionStatus.TIMEOUT)

    @mock.patch(RUN)
    def test_missing_binary_maps_to_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no such binary")
        self.assertEqual(JhoveAdapter().run("/tmp/f").status, ToolExecutionStatus.FAILURE)

    def test_classify_error(self):
        adapter = JhoveAdapter()
        self.assertEqual(adapter.classify_error("PDF is encrypted"), ErrorCategory.ENCRYPTED)
        self.assertEqual(adapter.classify_error("something odd"), ErrorCategory.UNKNOWN)


@unittest.skipUnless(shutil.which("jhove"), "jhove is not installed")
class JhoveIntegrationTests(SimpleTestCase):
    def test_real_run_on_a_text_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.txt"
            path.write_text("hello\n")
            result = JhoveAdapter(timeout_seconds=60).run(path)
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertIn("<jhove", result.raw_output)
