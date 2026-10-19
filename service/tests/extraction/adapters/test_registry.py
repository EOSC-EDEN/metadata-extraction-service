from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.extraction.adapters.exiftool import ExifToolAdapter
from apps.extraction.adapters.registry import get_adapter_class, validate_adapter_classes


class GetAdapterClassTests(SimpleTestCase):
    def test_resolves_configured_tool_name(self):
        self.assertIs(get_adapter_class("exiftool"), ExifToolAdapter)

    def test_raises_for_unknown_tool_name(self):
        with self.assertRaises(ImproperlyConfigured):
            get_adapter_class("not-a-configured-tool")


class ValidateAdapterClassesTests(SimpleTestCase):
    def test_passes_for_the_default_configuration(self):
        validate_adapter_classes()  # must not raise

    @override_settings(
        MES_TOOLS={"broken": {"adapter": "apps.extraction.adapters.does_not_exist.NopeAdapter"}}
    )
    def test_raises_for_an_unimportable_adapter_class(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_adapter_classes()


class DefaultToolSetTests(SimpleTestCase):
    """The implemented Requirement Group 4 tools are configured and resolvable (MES-REQ-4-001..006)."""

    EXPECTED = {
        "exiftool": "ExifToolAdapter",
        "mediainfo": "MediaInfoAdapter",
        "ffprobe": "FFprobeAdapter",
        "imagemagick": "ImageMagickAdapter",
        "metaflac": "MetaflacAdapter",
        "file_scraper": "FileScraperAdapter",
        "tika": "TikaAdapter",
    }

    def test_every_required_tool_resolves_to_its_adapter(self):
        for name, class_name in self.EXPECTED.items():
            with self.subTest(tool=name):
                self.assertEqual(get_adapter_class(name).__name__, class_name)

    def test_only_exiftool_and_mediainfo_are_enabled_by_default(self):
        from apps.extraction.tool_config import list_tool_configs

        enabled = {tool.name for tool in list_tool_configs() if tool.enabled}
        self.assertEqual(enabled, {"exiftool", "mediainfo"})
