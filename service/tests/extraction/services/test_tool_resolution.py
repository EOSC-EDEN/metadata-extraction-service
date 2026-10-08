from django.test import TestCase, override_settings

from apps.extraction.exceptions import FFISUnavailable, ToolNotConfigured
from apps.extraction.services.format_identification import FFISResolution
from apps.extraction.services.tool_resolution import resolve_tools


class FakeFFISClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def identify(self, file_path):
        self.calls.append(file_path)
        if self.error:
            raise self.error
        return self.result


class ResolveToolsTests(TestCase):
    def test_caller_supplied_identifier_never_calls_ffis(self):
        ffis = FakeFFISClient()
        with override_settings(MES_FFIS_ENABLED=True):
            resolution = resolve_tools("/f", puid="fmt/43", ffis_client=ffis)

        self.assertEqual(ffis.calls, [])
        self.assertEqual(resolution.selection_method, "default_tool_caller_identified")
        self.assertEqual(resolution.puid, "fmt/43")
        self.assertEqual([t.name for t in resolution.tools], ["exiftool"])

    @override_settings(MES_FFIS_ENABLED=False)
    def test_ffis_disabled_uses_default_tool_without_calling_ffis(self):
        ffis = FakeFFISClient()

        resolution = resolve_tools("/f", ffis_client=ffis)

        self.assertEqual(ffis.calls, [])
        self.assertEqual(resolution.selection_method, "default_tool_ffis_disabled")
        self.assertEqual(resolution.warnings, [])

    @override_settings(MES_FFIS_ENABLED=True)
    def test_ffis_identifies_records_identifier_and_warnings(self):
        ffis = FakeFFISClient(
            FFISResolution("fmt/43", "image/jpeg", "warning", ["w"], {"outcome": "warning"})
        )

        resolution = resolve_tools("/f", ffis_client=ffis)

        self.assertEqual(ffis.calls, ["/f"])
        self.assertEqual(resolution.selection_method, "default_tool_ffis_identified")
        self.assertEqual((resolution.puid, resolution.mime_type), ("fmt/43", "image/jpeg"))
        self.assertEqual(resolution.warnings, ["w"])
        self.assertEqual(resolution.ffis_provenance, {"outcome": "warning"})
        self.assertEqual([t.name for t in resolution.tools], ["exiftool"])

    @override_settings(MES_FFIS_ENABLED=True)
    def test_ffis_inconclusive_falls_back_with_warning(self):
        resolution = resolve_tools("/f", ffis_client=FakeFFISClient(None))

        self.assertEqual(resolution.selection_method, "default_tool_ffis_inconclusive")
        self.assertTrue(resolution.warnings[0].startswith("ffis_inconclusive"))
        self.assertIsNone(resolution.puid)

    @override_settings(MES_FFIS_ENABLED=True)
    def test_ffis_unreachable_falls_back_with_warning(self):
        ffis = FakeFFISClient(error=FFISUnavailable("down"))

        resolution = resolve_tools("/f", ffis_client=ffis)

        self.assertEqual(resolution.selection_method, "default_tool_ffis_unreachable")
        self.assertIn("ffis_unreachable", resolution.warnings[0])
        self.assertIn("down", resolution.warnings[0])

    @override_settings(MES_FFIS_ENABLED=True, MES_DEFAULT_TOOL="")
    def test_no_default_tool_raises_before_calling_ffis(self):
        ffis = FakeFFISClient()

        with self.assertRaises(ToolNotConfigured):
            resolve_tools("/f", ffis_client=ffis)
        self.assertEqual(ffis.calls, [])
