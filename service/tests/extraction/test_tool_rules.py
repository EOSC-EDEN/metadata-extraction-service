import os
import tempfile

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.extraction.tool_rules import (
    ToolRule,
    load_tool_rules,
    match_rules,
    normalise_extension,
    normalise_puid,
    validate_tool_rules,
)


def _write(testcase, content) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    handle.write(content)
    handle.close()
    testcase.addCleanup(os.unlink, handle.name)
    return handle.name


class LoadToolRulesTests(SimpleTestCase):
    def test_missing_file_means_no_rules(self):
        with override_settings(MES_TOOL_RULES_FILE="/nonexistent/tool_rules.yaml"):
            self.assertEqual(load_tool_rules(), ())
            validate_tool_rules()

    def test_empty_file_and_empty_list_mean_no_rules(self):
        for content in ("", "rules: []\n", "rules:\n"):
            with override_settings(MES_TOOL_RULES_FILE=_write(self, content)):
                self.assertEqual(load_tool_rules(), (), content)

    def test_shipped_example_file_is_valid(self):
        with override_settings(MES_TOOL_RULES_FILE="config/tool_rules.yaml"):
            validate_tool_rules()

    def test_loads_and_normalises_a_rule(self):
        path = _write(
            self,
            """
rules:
  - name: Audio
    puids: [" FMT/279 ", "x-fmt/2"]
    extensions: [".FLAC", "Wav"]
    tools: [exiftool, mediainfo]
""",
        )
        with override_settings(MES_TOOL_RULES_FILE=path):
            (rule,) = load_tool_rules()
        self.assertEqual(rule.name, "Audio")
        self.assertEqual(rule.puids, ("fmt/279", "x-fmt/2"))
        self.assertEqual(rule.extensions, ("flac", "wav"))
        self.assertEqual(rule.tools, ("exiftool", "mediainfo"))
        self.assertEqual(rule.match, "any")

    def test_rule_with_only_extensions_or_only_puids_is_valid(self):
        path = _write(
            self,
            "rules:\n- {name: a, puids: [fmt/1], tools: [exiftool]}\n"
            "- {name: b, extensions: [jpg], tools: [exiftool]}\n",
        )
        with override_settings(MES_TOOL_RULES_FILE=path):
            self.assertEqual([r.name for r in load_tool_rules()], ["a", "b"])

    def test_relative_path_resolves_against_base_dir_not_cwd(self):
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        with override_settings(MES_TOOL_RULES_FILE="config/tool_rules.yaml"):
            from_project_dir = load_tool_rules()
            os.chdir(tempfile.gettempdir())
            validate_tool_rules()
            self.assertEqual(load_tool_rules(), from_project_dir)

    def test_reloads_when_the_file_changes(self):
        path = _write(self, "rules: []\n")
        with override_settings(MES_TOOL_RULES_FILE=path):
            self.assertEqual(load_tool_rules(), ())
            with open(path, "w") as handle:
                handle.write("rules:\n- {name: a, puids: [fmt/1], tools: [exiftool]}\n")
            os.utime(path, ns=(1, 2_000_000_000))
            self.assertEqual(len(load_tool_rules()), 1)

    def test_invalid_files_fail_validation(self):
        bad = {
            "invalid yaml": "rules: [unclosed",
            "top level is a list": "- name: a\n",
            "rules not a list": "rules: {}\n",
            "unknown top-level key": "rules: []\nextra: 1\n",
            "rule not a mapping": "rules: [a]\n",
            "missing name": "rules:\n- {puids: [fmt/1], tools: [exiftool]}\n",
            "blank name": "rules:\n- {name: ' ', puids: [fmt/1], tools: [exiftool]}\n",
            "duplicate name": "rules:\n- {name: a, puids: [fmt/1], tools: [exiftool]}\n"
            "- {name: a, puids: [fmt/2], tools: [exiftool]}\n",
            "no puids or extensions": "rules:\n- {name: a, tools: [exiftool]}\n",
            "empty puids and extensions": "rules:\n- {name: a, puids: [], extensions: [], tools: [exiftool]}\n",
            "missing tools": "rules:\n- {name: a, puids: [fmt/1]}\n",
            "empty tools": "rules:\n- {name: a, puids: [fmt/1], tools: []}\n",
            "unknown tool": "rules:\n- {name: a, puids: [fmt/1], tools: [nosuchtool]}\n",
            "unknown key": "rules:\n- {name: a, puids: [fmt/1], tools: [exiftool], colour: red}\n",
            "puids not a list": "rules:\n- {name: a, puids: fmt/1, tools: [exiftool]}\n",
            "puid not a string": "rules:\n- {name: a, puids: [1], tools: [exiftool]}\n",
            "blank extension": "rules:\n- {name: a, extensions: ['.'], tools: [exiftool]}\n",
            "bad match": "rules:\n- {name: a, puids: [fmt/1], tools: [exiftool], match: some}\n",
            "match all needs both": "rules:\n- {name: a, puids: [fmt/1], tools: [exiftool], match: all}\n",
        }
        for label, content in bad.items():
            with self.subTest(label), override_settings(MES_TOOL_RULES_FILE=_write(self, content)):
                with self.assertRaises(ImproperlyConfigured):
                    validate_tool_rules()

    def test_unreadable_path_fails_validation(self):
        with override_settings(MES_TOOL_RULES_FILE=tempfile.gettempdir()):  # a directory
            with self.assertRaises(ImproperlyConfigured):
                validate_tool_rules()

    def test_blank_setting_fails_validation(self):
        for value in ("", None, 5):
            with self.subTest(value), override_settings(MES_TOOL_RULES_FILE=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_tool_rules()


class NormaliseTests(SimpleTestCase):
    def test_extension(self):
        self.assertEqual(normalise_extension(".JPG"), "jpg")
        self.assertEqual(normalise_extension("jpg"), "jpg")
        self.assertEqual(normalise_extension(" Jpg "), "jpg")
        self.assertIsNone(normalise_extension(""))
        self.assertIsNone(normalise_extension("."))
        self.assertIsNone(normalise_extension(None))

    def test_puid(self):
        self.assertEqual(normalise_puid(" FMT/43 "), "fmt/43")
        self.assertIsNone(normalise_puid(""))
        self.assertIsNone(normalise_puid(None))


def _rule(name, puids=(), extensions=(), tools=("mediainfo",), match="any"):
    return ToolRule(name, tuple(puids), tuple(extensions), tuple(tools), match)


class MatchRulesTests(SimpleTestCase):
    def test_puid_only_rule(self):
        rules = [_rule("a", puids=["fmt/43"])]
        self.assertEqual(match_rules(rules, "FMT/43", None), rules)
        self.assertEqual(match_rules(rules, "fmt/44", "jpg"), [])
        self.assertEqual(match_rules(rules, None, "jpg"), [])

    def test_extension_only_rule(self):
        rules = [_rule("a", extensions=["jpg"])]
        self.assertEqual(match_rules(rules, None, "JPG"), rules)
        self.assertEqual(match_rules(rules, "fmt/43", "png"), [])
        self.assertEqual(match_rules(rules, "fmt/43", None), [])

    def test_any_matches_on_either(self):
        rules = [_rule("a", puids=["fmt/43"], extensions=["jpg"])]
        self.assertEqual(match_rules(rules, "fmt/43", "png"), rules)
        self.assertEqual(match_rules(rules, "fmt/1", "jpg"), rules)
        self.assertEqual(match_rules(rules, "fmt/1", "png"), [])

    def test_all_requires_both(self):
        rules = [_rule("a", puids=["fmt/43"], extensions=["jpg"], match="all")]
        self.assertEqual(match_rules(rules, "fmt/43", "jpg"), rules)
        self.assertEqual(match_rules(rules, "fmt/43", "png"), [])
        self.assertEqual(match_rules(rules, "fmt/1", "jpg"), [])
        self.assertEqual(match_rules(rules, None, "jpg"), [])
        self.assertEqual(match_rules(rules, "fmt/43", None), [])

    def test_several_rules_match_in_file_order(self):
        rules = [
            _rule("first", puids=["fmt/43"]),
            _rule("nope", puids=["fmt/1"]),
            _rule("third", extensions=["jpg"]),
        ]
        self.assertEqual([r.name for r in match_rules(rules, "fmt/43", "jpg")], ["first", "third"])
