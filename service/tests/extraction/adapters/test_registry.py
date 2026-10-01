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
        MES_ADAPTER_CLASSES={"broken": "apps.extraction.adapters.does_not_exist.NopeAdapter"}
    )
    def test_raises_for_an_unimportable_adapter_class(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_adapter_classes()
