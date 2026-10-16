import io
import os
import tempfile
import time
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.api.job_store import InMemoryJobStore, JobRecord, JobStatus
from apps.api.jobs import JobService
from apps.api.purge import (
    mes_purge_interval_minutes,
    mes_purge_schedule,
    sweep_orphaned_uploads,
)
from tests.api.job_helpers import DeferredExecutor, FakeClock

TTL = 100


class ScheduleTests(SimpleTestCase):
    def test_interval_is_a_quarter_of_the_ttl_in_whole_minutes_between_one_and_sixty(self):
        cases = {
            1: 1,  # below a minute: clamps to the cron granularity
            30: 1,
            60: 1,
            239: 1,  # 59.75 s
            240: 1,
            479: 1,  # 119.75 s, rounded down to a whole minute
            480: 2,
            600: 2,
            14399: 59,
            14400: 60,  # exactly one hour
            14401: 60,
            86400: 60,
            604800: 60,
        }
        for ttl, minutes in cases.items():
            with self.subTest(ttl=ttl):
                self.assertEqual(mes_purge_interval_minutes(ttl), minutes)

    def test_crontab_expressions(self):
        self.assertEqual(mes_purge_schedule(60), "*/1 * * * *")
        self.assertEqual(mes_purge_schedule(600), "*/2 * * * *")
        self.assertEqual(mes_purge_schedule(14399), "*/59 * * * *")
        self.assertEqual(mes_purge_schedule(86400), "0 * * * *")
        self.assertEqual(mes_purge_schedule(604800), "0 * * * *")

    def test_a_non_positive_ttl_is_rejected(self):
        for ttl in (0, -1):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                mes_purge_schedule(ttl)

    def test_there_is_no_separate_purge_cron_setting(self):
        self.assertFalse(hasattr(settings, "MES_JOB_PURGE_CRON"))


def _make_dir(root, name, age_seconds, now):
    path = os.path.join(root, name)
    os.makedirs(path)
    with open(os.path.join(path, "upload.bin"), "wb") as handle:
        handle.write(b"x")
    stamp = now - age_seconds
    os.utime(path, (stamp, stamp))
    return path


class SweepTests(SimpleTestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = root.name
        self.now = 1_000_000.0

    def test_age_boundary_just_under_at_and_over_the_ttl(self):
        for age, removed in ((TTL - 1, False), (TTL, True), (TTL + 1, True)):
            with self.subTest(age=age):
                path = _make_dir(self.root, f"d{age}", age, self.now)
                count = sweep_orphaned_uploads(self.root, set(), TTL, self.now)
                self.assertEqual(count, 1 if removed else 0)
                self.assertEqual(os.path.exists(path), not removed)

    def test_a_directory_a_stored_job_refers_to_is_kept_however_old(self):
        path = _make_dir(self.root, "live", TTL * 10, self.now)
        self.assertEqual(sweep_orphaned_uploads(self.root, {path}, TTL, self.now), 0)
        self.assertTrue(os.path.exists(path))

    def test_plain_files_and_a_missing_root_are_left_alone(self):
        stray = os.path.join(self.root, "notes.txt")
        open(stray, "w").close()
        os.utime(stray, (0, 0))
        self.assertEqual(sweep_orphaned_uploads(self.root, set(), TTL, self.now), 0)
        self.assertTrue(os.path.exists(stray))
        self.assertEqual(sweep_orphaned_uploads(os.path.join(self.root, "x"), set(), TTL, 1), 0)

    def test_running_twice_is_safe(self):
        _make_dir(self.root, "old", TTL * 2, self.now)
        self.assertEqual(sweep_orphaned_uploads(self.root, set(), TTL, self.now), 1)
        self.assertEqual(sweep_orphaned_uploads(self.root, set(), TTL, self.now), 0)


class PurgeCommandTests(TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.upload_dir = root.name
        override = override_settings(MES_JOB_UPLOAD_DIR=self.upload_dir, MES_JOB_TTL_SECONDS=TTL)
        override.enable()
        self.addCleanup(override.disable)
        self.clock = FakeClock()
        self.service = JobService(
            store=InMemoryJobStore(max_jobs=10),
            executor=DeferredExecutor(),
            ttl_seconds=TTL,
            clock=self.clock,
            extract=lambda **kwargs: {},
        )
        patcher = mock.patch(
            "apps.api.management.commands.purge_expired_jobs.get_job_service",
            return_value=self.service,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _job(self, name, created_offset):
        """A stored job (created `created_offset` s after the clock start) with an upload dir."""
        directory = os.path.join(self.upload_dir, name)
        os.makedirs(directory)
        self.service._store.create(
            JobRecord(
                job_id=name,
                request_id="r",
                status=JobStatus.PENDING,
                created_at=self.clock.now + timedelta(seconds=created_offset),
                work_dir=directory,
            )
        )
        return directory

    def _run(self):
        out = io.StringIO()
        with self.assertLogs("apps.api.management.commands.purge_expired_jobs", "INFO") as logs:
            call_command("purge_expired_jobs", stdout=out)
        return out.getvalue(), "\n".join(logs.output)

    def test_removes_expired_jobs_and_their_uploads_but_keeps_fresh_ones(self):
        old = self._job("old", 0)
        fresh = self._job("fresh", 60)
        self.clock.advance(TTL)  # "old" is now TTL old; "fresh" is 40 s old
        output, log = self._run()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))
        self.assertIsNone(self.service._store.get("old"))
        self.assertIsNotNone(self.service._store.get("fresh"))
        self.assertIn("1 expired job", output)
        self.assertIn("1 expired job", log)

    def test_removes_orphaned_uploads_no_job_refers_to(self):
        orphan = os.path.join(self.upload_dir, "orphan")
        os.makedirs(orphan)
        stamp = time.time() - TTL * 5
        os.utime(orphan, (stamp, stamp))
        young = os.path.join(self.upload_dir, "young")
        os.makedirs(young)
        output, _ = self._run()
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.exists(young))
        self.assertIn("1 orphaned upload", output)

    def test_keeps_an_old_directory_that_a_live_job_refers_to(self):
        directory = self._job("live", 0)
        stamp = time.time() - TTL * 5
        os.utime(directory, (stamp, stamp))
        self._run()
        self.assertTrue(os.path.exists(directory))

    def test_is_idempotent_and_reports_zero_the_second_time(self):
        self._job("old", 0)
        self.clock.advance(TTL)
        self._run()
        output, _ = self._run()
        self.assertIn("0 expired jobs", output)
        self.assertIn("0 orphaned uploads", output)

    def test_an_expired_job_is_gone_even_before_the_command_runs(self):
        self._job("old", 0)
        self.clock.advance(TTL)
        self.assertIsNone(self.service.get("old"))  # the access purge, not cron, removed it


class PurgeScheduleCommandTests(SimpleTestCase):
    def test_prints_the_crontab_expression_for_the_configured_ttl(self):
        for ttl, expected in ((600, "*/2 * * * *"), (86400, "0 * * * *")):
            with self.subTest(ttl=ttl), override_settings(MES_JOB_TTL_SECONDS=ttl):
                out = io.StringIO()
                call_command("purge_schedule", stdout=out)
                self.assertEqual(out.getvalue().strip(), expected)

    def test_can_print_the_interval_in_minutes(self):
        with override_settings(MES_JOB_TTL_SECONDS=600):
            out = io.StringIO()
            call_command("purge_schedule", "--minutes", stdout=out)
            self.assertEqual(out.getvalue().strip(), "2")
