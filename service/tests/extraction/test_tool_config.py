from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.extraction.exceptions import ToolNotConfigured
from apps.extraction.tool_config import (
    get_default_tool,
    get_tool_config,
    validate_concurrency_config,
    validate_tool_config,
)

_TOOL = {
    "adapter": "apps.extraction.adapters.exiftool.ExifToolAdapter",
    "kind": "subprocess",
    "invocation_command": "/opt/exiftool",
}


class ToolConfigTests(SimpleTestCase):
    @override_settings(MES_TOOLS={"exiftool": {**_TOOL, "extra_args": ["-n"], "mandatory": True}})
    def test_reads_tool_from_settings(self):
        config = get_tool_config("exiftool")
        self.assertEqual(config.invocation_command, "/opt/exiftool")
        self.assertEqual(config.extra_args, ("-n",))
        self.assertTrue(config.is_mandatory)
        self.assertTrue(config.enabled)

    def test_default_tool_is_exiftool_by_default(self):
        self.assertEqual(get_default_tool().name, "exiftool")

    @override_settings(MES_TOOLS={"exiftool": {**_TOOL, "enabled": False}})
    def test_disabled_default_tool_raises(self):
        with self.assertRaises(ToolNotConfigured):
            get_default_tool()

    @override_settings(MES_DEFAULT_TOOL="unknown")
    def test_unknown_default_tool_raises(self):
        with self.assertRaises(ToolNotConfigured):
            get_default_tool()

    @override_settings(MES_TOOLS={"x": {"adapter": "a"}})
    def test_validate_rejects_missing_keys(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_tool_config()

    @override_settings(MES_TOOLS={"x": {**_TOOL, "kind": "carrier-pigeon"}})
    def test_validate_rejects_unknown_kind(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_tool_config()

    @override_settings(MES_TOOLS={"exiftool": {k: v for k, v in _TOOL.items() if k != "invocation_command"}})
    def test_subprocess_invocation_command_is_optional(self):
        validate_tool_config()
        self.assertIsNone(get_tool_config("exiftool").invocation_command)

    @override_settings(MES_TOOLS={"tika": {"adapter": _TOOL["adapter"], "kind": "http"}})
    def test_http_tool_requires_invocation_command(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_tool_config()


class ConcurrencyConfigTests(SimpleTestCase):
    def test_accepts_boundary_and_typical_values(self):
        for value in (1, 4):
            with self.subTest(value=value), override_settings(MES_MAX_CONCURRENT_TOOLS=value):
                validate_concurrency_config()

    def test_rejects_zero_negative_and_non_integers(self):
        for value in (0, -1, "4", 2.5, None, True):
            with self.subTest(value=value), override_settings(MES_MAX_CONCURRENT_TOOLS=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_concurrency_config()
