from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.extraction.exceptions import ToolNotConfigured
from apps.extraction.tool_config import get_default_tool, get_tool_config, validate_tool_config

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
