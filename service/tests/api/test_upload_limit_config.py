from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.api.serializers import validate_files_per_request_config, validate_upload_limit_config


class UploadLimitConfigTests(SimpleTestCase):
    def test_accepts_one_and_default_sized_limits(self):
        for value in (1, 512 * 1024 * 1024):
            with self.subTest(value=value), override_settings(MES_MAX_UPLOAD_BYTES=value):
                validate_upload_limit_config()

    def test_rejects_zero_negative_and_non_integers(self):
        for value in (0, -1, "10", 1.5, None, True):
            with self.subTest(value=value), override_settings(MES_MAX_UPLOAD_BYTES=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_upload_limit_config()


class FilesPerRequestConfigTests(SimpleTestCase):
    def test_accepts_one_and_the_default(self):
        for value in (1, 10):
            with self.subTest(value=value), override_settings(MES_MAX_FILES_PER_REQUEST=value):
                validate_files_per_request_config()

    def test_rejects_zero_negative_and_non_integers(self):
        for value in (0, -1, "10", 1.5, None, True):
            with self.subTest(value=value), override_settings(MES_MAX_FILES_PER_REQUEST=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_files_per_request_config()
