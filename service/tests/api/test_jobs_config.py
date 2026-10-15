from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.api.jobs import validate_jobs_config

SETTINGS = ("MES_ASYNC_MAX_WORKERS", "MES_ASYNC_MAX_JOBS", "MES_JOB_TTL_SECONDS")


class JobsConfigTests(SimpleTestCase):
    def test_accepts_one_and_defaults(self):
        validate_jobs_config()
        for name in SETTINGS:
            with self.subTest(name=name), override_settings(**{name: 1}):
                validate_jobs_config()

    def test_rejects_zero_negative_and_non_integers(self):
        for name in SETTINGS:
            for value in (0, -1, "10", 1.5, None, True):
                with self.subTest(name=name, value=value), override_settings(**{name: value}):
                    with self.assertRaises(ImproperlyConfigured):
                        validate_jobs_config()
