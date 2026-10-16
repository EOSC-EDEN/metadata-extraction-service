import os
import tempfile
import unittest
from types import SimpleNamespace

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.api.job_store import InMemoryJobStore, RedisJobStore, build_job_store
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


class JobStoreConfigTests(SimpleTestCase):
    def test_memory_and_redis_are_accepted(self):
        with override_settings(MES_JOB_STORE="memory", MES_REDIS_URL=""):
            validate_jobs_config()
        with override_settings(MES_JOB_STORE="redis", MES_REDIS_URL="redis://redis:6379/0"):
            validate_jobs_config()

    def test_unknown_store_is_rejected(self):
        for value in ("", "Redis", "sqlite", None, 1):
            with self.subTest(value=value), override_settings(MES_JOB_STORE=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_jobs_config()

    def test_redis_needs_a_url(self):
        for value in ("", "   ", None):
            with self.subTest(value=value), override_settings(
                MES_JOB_STORE="redis", MES_REDIS_URL=value
            ):
                with self.assertRaises(ImproperlyConfigured):
                    validate_jobs_config()

    def test_the_url_is_not_needed_for_memory(self):
        with override_settings(MES_JOB_STORE="memory", MES_REDIS_URL=None):
            validate_jobs_config()


class UploadDirConfigTests(SimpleTestCase):
    def test_a_missing_directory_is_created(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "a", "uploads")
            with override_settings(MES_JOB_UPLOAD_DIR=target):
                validate_jobs_config()
            self.assertTrue(os.path.isdir(target))

    def test_an_existing_writable_directory_is_accepted(self):
        with tempfile.TemporaryDirectory() as root, override_settings(MES_JOB_UPLOAD_DIR=root):
            validate_jobs_config()

    def test_a_file_in_place_of_the_directory_is_rejected(self):
        with tempfile.NamedTemporaryFile() as handle, override_settings(
            MES_JOB_UPLOAD_DIR=handle.name
        ):
            with self.assertRaises(ImproperlyConfigured):
                validate_jobs_config()

    @unittest.skipIf(os.geteuid() == 0, "root can write anywhere")
    def test_an_unwritable_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o500)
            try:
                with override_settings(MES_JOB_UPLOAD_DIR=root):
                    with self.assertRaises(ImproperlyConfigured):
                        validate_jobs_config()
            finally:
                os.chmod(root, 0o700)

    @unittest.skipIf(os.geteuid() == 0, "root can write anywhere")
    def test_a_directory_that_cannot_be_created_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o500)
            try:
                with override_settings(MES_JOB_UPLOAD_DIR=os.path.join(root, "sub")):
                    with self.assertRaises(ImproperlyConfigured):
                        validate_jobs_config()
            finally:
                os.chmod(root, 0o700)

    def test_blank_or_non_string_is_rejected(self):
        for value in ("", "  ", None, 5):
            with self.subTest(value=value), override_settings(MES_JOB_UPLOAD_DIR=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_jobs_config()


class BuildJobStoreTests(SimpleTestCase):
    def test_memory_is_the_default_store(self):
        store = build_job_store(
            SimpleNamespace(MES_JOB_STORE="memory", MES_ASYNC_MAX_JOBS=3, MES_JOB_TTL_SECONDS=60)
        )
        self.assertIsInstance(store, InMemoryJobStore)
        self.assertEqual(store.max_jobs, 3)

    def test_redis_store_is_built_from_the_url(self):
        conf = SimpleNamespace(
            MES_JOB_STORE="redis",
            MES_REDIS_URL="redis://example.invalid:6380/2",
            MES_ASYNC_MAX_JOBS=7,
            MES_JOB_TTL_SECONDS=60,
        )
        store = build_job_store(conf)  # connects lazily, so no server is needed
        self.assertIsInstance(store, RedisJobStore)
        self.assertEqual(store.max_jobs, 7)
        kwargs = store._client.connection_pool.connection_kwargs
        self.assertEqual((kwargs["host"], kwargs["port"], kwargs["db"]), ("example.invalid", 6380, 2))
        self.assertTrue(kwargs["decode_responses"])
