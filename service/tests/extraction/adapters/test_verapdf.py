"""Tests for the veraPDF adapter (feature extraction only; validation is always off)."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.extraction.adapters.base import ErrorCategory, ToolExecutionStatus
from apps.extraction.adapters.verapdf import DEFAULT_FEATURES, VeraPdfAdapter

RUN = "apps.extraction.adapters.base.subprocess.run"
COMMAND = ["verapdf", "--off", "--extract", DEFAULT_FEATURES, "/tmp/f"]
REPORT = "<report><jobs><job><featuresReport><informationDict/></featuresReport></job></jobs></report>"


class VeraPdfAdapterTests(SimpleTestCase):
    def test_metadata(self):
        adapter = VeraPdfAdapter()
        self.assertEqual(adapter.tool_name, "verapdf")
        self.assertEqual(adapter.invocation_command, "verapdf")
        self.assertEqual(adapter.raw_output_format, "xml")

    def test_default_features_are_the_technical_metadata_set(self):
        self.assertEqual(
            DEFAULT_FEATURES,
            "informationDict,metadata,lowLevelInfo,page,font,iccProfile,colorSpace,outputIntent,"
            "embeddedFile,imageXobject",
        )

    def test_build_command(self):
        self.assertEqual(VeraPdfAdapter().build_command("/tmp/f"), COMMAND)

    @override_settings(MES_VERAPDF_FEATURES="informationDict,metadata")
    def test_features_come_from_settings(self):
        command = VeraPdfAdapter().build_command("/tmp/f")
        self.assertEqual(command, ["verapdf", "--off", "--extract", "informationDict,metadata", "/tmp/f"])

    @override_settings(MES_VERAPDF_FEATURES="")
    def test_blank_setting_falls_back_to_the_default(self):
        self.assertIn(DEFAULT_FEATURES, VeraPdfAdapter().build_command("/tmp/f"))

    def test_validation_is_always_off_and_no_validation_options_are_passed(self):
        for features in ("informationDict", DEFAULT_FEATURES):
            with self.subTest(features=features), override_settings(MES_VERAPDF_FEATURES=features):
                command = VeraPdfAdapter().build_command("/tmp/f")
                self.assertIn("--off", command)
                for option in ("--flavour", "-f", "--profile", "--defaultflavour", "--format"):
                    self.assertNotIn(option, command)

    def test_extra_args_precede_the_file_path(self):
        command = VeraPdfAdapter(extra_args=["--password", "x"]).build_command("/tmp/f")
        self.assertEqual(command[-3:], ["--password", "x", "/tmp/f"])

    @mock.patch(RUN)
    def test_success_returns_unmodified_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(COMMAND, 0, stdout=REPORT, stderr="")
        result = VeraPdfAdapter().run("/tmp/f")
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertEqual(result.raw_output, REPORT)
        self.assertEqual(result.raw_output_format, "xml")

    @mock.patch(RUN)
    def test_non_pdf_maps_to_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, COMMAND, stderr="File is not a PDF")
        self.assertEqual(VeraPdfAdapter().run("/tmp/f").status, ToolExecutionStatus.FAILURE)

    @mock.patch(RUN)
    def test_timeout_maps_to_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=COMMAND, timeout=5)
        self.assertEqual(VeraPdfAdapter().run("/tmp/f").status, ToolExecutionStatus.TIMEOUT)

    @mock.patch(RUN)
    def test_missing_binary_maps_to_failure(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no such binary")
        self.assertEqual(VeraPdfAdapter().run("/tmp/f").status, ToolExecutionStatus.FAILURE)

    def test_classify_error(self):
        adapter = VeraPdfAdapter()
        self.assertEqual(adapter.classify_error("The PDF is encrypted, password required"), ErrorCategory.ENCRYPTED)
        self.assertEqual(adapter.classify_error("something odd"), ErrorCategory.UNKNOWN)


@unittest.skipUnless(shutil.which("verapdf"), "verapdf is not installed")
class VeraPdfIntegrationTests(SimpleTestCase):
    MINIMAL_PDF = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 10 10]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )

    def test_real_run_gives_features_and_no_validation_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.pdf"
            path.write_bytes(self.MINIMAL_PDF)
            result = VeraPdfAdapter(timeout_seconds=120).run(path)
        self.assertEqual(result.status, ToolExecutionStatus.SUCCESS)
        self.assertIn("featuresReport", result.raw_output)
        self.assertNotIn("validationReport", result.raw_output)
