from django.test import TestCase, override_settings

from apps.extraction.exceptions import FFISUnavailable, ToolNotConfigured
from apps.extraction.services.format_identification import FFISResolution
from apps.extraction.services.tool_resolution import resolve_tools


class FakeFFISClient:
    base_url = "http://ffis.test"

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
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "mediainfo"])

    @override_settings(MES_FFIS_ENABLED=False)
    def test_ffis_disabled_uses_default_tool_without_calling_ffis(self):
        ffis = FakeFFISClient()

        resolution = resolve_tools("/f", ffis_client=ffis)

        self.assertEqual(ffis.calls, [])
        self.assertEqual(resolution.selection_method, "default_tool_ffis_disabled")
        self.assertEqual(resolution.warnings, [])
        self.assertIsNone(resolution.identification_url)

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
        self.assertEqual(resolution.identification_url, "http://ffis.test")
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "mediainfo"])

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


_TOOL = {"adapter": "x.Adapter", "kind": "subprocess"}


class ResolveAllEnabledToolsTests(TestCase):
    @override_settings(
        MES_FFIS_ENABLED=False,
        MES_DEFAULT_TOOL="b",
        MES_TOOLS={
            "a": _TOOL,
            "b": _TOOL,
            "c": {**_TOOL, "enabled": False},
            "d": {**_TOOL, "enabled": True},
        },
    )
    def test_runs_all_enabled_tools_default_first_and_skips_disabled(self):
        resolution = resolve_tools("/f")

        self.assertEqual([t.name for t in resolution.tools], ["b", "a", "d"])


_RULES_TOOLS = {
    name: {
        "adapter": f"apps.extraction.adapters.{name}.{cls}",
        "kind": "subprocess",
        "enabled": enabled,
    }
    for name, cls, enabled in (
        ("exiftool", "ExifToolAdapter", True),
        ("mediainfo", "MediaInfoAdapter", True),
        ("ffprobe", "FFprobeAdapter", True),
        ("imagemagick", "ImageMagickAdapter", False),
    )
}


def _rules_file(testcase, content):
    import os
    import tempfile

    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    handle.write(content)
    handle.close()
    testcase.addCleanup(os.unlink, handle.name)
    return handle.name


class LocalRuleResolutionTests(TestCase):
    def resolve(self, rules, *args, **kwargs):
        path = _rules_file(self, rules)
        with override_settings(MES_TOOLS=_RULES_TOOLS, MES_TOOL_RULES_FILE=path):
            return resolve_tools("/f", *args, **kwargs)

    def test_no_rules_file_changes_nothing(self):
        with override_settings(MES_TOOL_RULES_FILE="/nonexistent.yaml"):
            resolution = resolve_tools("/f", puid="fmt/43", filename="a.jpg")
        self.assertEqual(resolution.selection_method, "default_tool_caller_identified")
        self.assertEqual(resolution.matched_rules, [])

    def test_puid_rule_limits_tools_to_default_plus_matched(self):
        resolution = self.resolve(
            "rules:\n- {name: jpeg, puids: [fmt/43], tools: [ffprobe]}\n", puid="fmt/43"
        )
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "ffprobe"])
        self.assertEqual(resolution.selection_method, "local_rule")
        self.assertEqual(resolution.matched_rules, ["jpeg"])
        self.assertEqual(resolution.puid, "fmt/43")

    def test_extension_rule_uses_filename_extension_case_insensitively(self):
        resolution = self.resolve(
            "rules:\n- {name: wav, extensions: ['.WAV'], tools: [mediainfo]}\n",
            filename="Sound.Wav",
        )
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "mediainfo"])
        self.assertEqual(resolution.matched_rules, ["wav"])

    def test_no_match_falls_through_to_all_enabled_tools(self):
        resolution = self.resolve(
            "rules:\n- {name: jpeg, puids: [fmt/43], tools: [ffprobe]}\n",
            puid="fmt/1",
            filename="a.bin",
        )
        self.assertEqual(resolution.selection_method, "default_tool_caller_identified")
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "mediainfo", "ffprobe"])
        self.assertEqual(resolution.matched_rules, [])

    def test_or_and_match_all_semantics(self):
        rules = (
            "rules:\n"
            "- {name: either, puids: [fmt/43], extensions: [jpg], tools: [mediainfo]}\n"
            "- {name: both, puids: [fmt/43], extensions: [jpg], tools: [ffprobe], match: all}\n"
        )
        only_ext = self.resolve(rules, puid="fmt/1", filename="a.jpg")
        self.assertEqual(only_ext.matched_rules, ["either"])
        both = self.resolve(rules, puid="fmt/43", filename="a.jpg")
        self.assertEqual(both.matched_rules, ["either", "both"])

    def test_several_rules_union_deduplicated_default_first(self):
        resolution = self.resolve(
            "rules:\n"
            "- {name: a, puids: [fmt/43], tools: [ffprobe, exiftool]}\n"
            "- {name: b, extensions: [jpg], tools: [ffprobe, mediainfo]}\n",
            puid="fmt/43",
            filename="a.jpg",
        )
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "ffprobe", "mediainfo"])
        self.assertEqual(resolution.matched_rules, ["a", "b"])

    def test_default_tool_kept_and_first_when_rule_omits_it(self):
        resolution = self.resolve(
            "rules:\n- {name: a, puids: [fmt/43], tools: [mediainfo]}\n", puid="fmt/43"
        )
        self.assertEqual(resolution.tools[0].name, "exiftool")

    def test_disabled_tool_is_skipped_with_a_warning(self):
        resolution = self.resolve(
            "rules:\n- {name: a, puids: [fmt/43], tools: [imagemagick, mediainfo]}\n", puid="fmt/43"
        )
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "mediainfo"])
        self.assertEqual(len(resolution.warnings), 1)
        self.assertTrue(resolution.warnings[0].startswith("tool_rule_disabled_tool"))
        self.assertIn("imagemagick", resolution.warnings[0])
        self.assertIn("a", resolution.warnings[0])

    def test_rule_wins_even_when_it_names_only_disabled_tools(self):
        resolution = self.resolve(
            "rules:\n- {name: a, puids: [fmt/43], tools: [imagemagick]}\n", puid="fmt/43"
        )
        self.assertEqual([t.name for t in resolution.tools], ["exiftool"])
        self.assertEqual(resolution.selection_method, "local_rule")

    def test_ffis_supplied_puid_matches_and_marks_via_ffis(self):
        ffis = FakeFFISClient(FFISResolution("fmt/43", "image/jpeg", "ok", ["w"], {"outcome": "ok"}))
        path = _rules_file(self, "rules:\n- {name: a, puids: [fmt/43], tools: [ffprobe]}\n")
        with override_settings(MES_TOOLS=_RULES_TOOLS, MES_TOOL_RULES_FILE=path, MES_FFIS_ENABLED=True):
            resolution = resolve_tools("/f", ffis_client=ffis)
        self.assertEqual(ffis.calls, ["/f"])
        self.assertEqual(resolution.selection_method, "local_rule_via_ffis")
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "ffprobe"])
        self.assertEqual(resolution.warnings, ["w"])
        self.assertEqual(resolution.ffis_provenance, {"outcome": "ok"})
        self.assertEqual(resolution.identification_url, "http://ffis.test")

    def test_caller_puid_never_calls_ffis_even_with_rules(self):
        ffis = FakeFFISClient()
        path = _rules_file(self, "rules:\n- {name: a, puids: [fmt/43], tools: [ffprobe]}\n")
        with override_settings(MES_TOOLS=_RULES_TOOLS, MES_TOOL_RULES_FILE=path, MES_FFIS_ENABLED=True):
            resolve_tools("/f", puid="fmt/43", ffis_client=ffis)
        self.assertEqual(ffis.calls, [])

    def test_extension_rule_still_runs_ffis_when_nothing_supplied(self):
        ffis = FakeFFISClient(error=FFISUnavailable("down"))
        path = _rules_file(self, "rules:\n- {name: a, extensions: [jpg], tools: [ffprobe]}\n")
        with override_settings(MES_TOOLS=_RULES_TOOLS, MES_TOOL_RULES_FILE=path, MES_FFIS_ENABLED=True):
            resolution = resolve_tools("/f", ffis_client=ffis, filename="a.jpg")
        self.assertEqual(ffis.calls, ["/f"])
        self.assertEqual(resolution.selection_method, "local_rule")
        self.assertEqual([t.name for t in resolution.tools], ["exiftool", "ffprobe"])
        self.assertTrue(any(w.startswith("ffis_unreachable") for w in resolution.warnings))

    def test_rules_take_precedence_over_a_mime_only_caller_identification(self):
        resolution = self.resolve(
            "rules:\n- {name: a, extensions: [jpg], tools: [ffprobe]}\n",
            mime_type="image/jpeg",
            filename="a.jpg",
        )
        self.assertEqual(resolution.selection_method, "local_rule")
