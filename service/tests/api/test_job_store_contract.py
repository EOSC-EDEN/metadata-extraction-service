"""One contract, run against every job store implementation.

Two store instances on one backend simulate two gunicorn workers sharing a Redis.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import fakeredis
from django.test import SimpleTestCase

from apps.api.job_store import (
    BatchRecord,
    InMemoryJobStore,
    JobRecord,
    JobStatus,
    JobStoreFull,
    RedisJobStore,
)

TTL = 100
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record(job_id="j1", status=JobStatus.PENDING, created_at=T0, size_bytes=0, **kwargs):
    return JobRecord(
        job_id=job_id,
        request_id="req-" + job_id,
        status=status,
        created_at=created_at,
        size_bytes=size_bytes,
        **kwargs,
    )


def _batch(batch_id="b1", job_ids=("j1",), created_at=T0):
    return BatchRecord(
        batch_id=batch_id,
        request_id="req-" + batch_id,
        created_at=created_at,
        jobs=[{"index": i, "filename": f"f{i}.jpg", "job_id": job_id} for i, job_id in enumerate(job_ids)],
    )


class JobRecordSerialisationTests(SimpleTestCase):
    def test_a_batch_record_round_trips(self):
        batch = _batch("b1", ["j1", "j2"])
        self.assertEqual(BatchRecord.from_dict(batch.to_dict()), batch)

    def test_a_job_remembers_its_batch(self):
        record = _record(batch_id="b1")
        self.assertEqual(JobRecord.from_dict(record.to_dict()), record)
        self.assertEqual(JobRecord.from_dict(record.to_dict()).batch_id, "b1")

    def test_a_record_stored_before_batches_existed_has_no_batch(self):
        data = _record().to_dict()
        del data["batch_id"]
        self.assertIsNone(JobRecord.from_dict(data).batch_id)

    def test_round_trip_of_a_new_job(self):
        record = _record(work_dir="/tmp/x", size_bytes=7)
        self.assertEqual(JobRecord.from_dict(record.to_dict()), record)

    def test_round_trip_of_a_finished_job_with_result_and_errors(self):
        record = _record(
            status=JobStatus.SUCCEEDED,
            finished_at=T0 + timedelta(seconds=5),
            result={"results": [{"status": "success", "raw_output": "é\u0000"}], "n": 1},
            errors=["a", "b"],
        )
        self.assertEqual(JobRecord.from_dict(record.to_dict()), record)

    def test_dict_is_json_serialisable_with_plain_values(self):
        import json

        data = _record(status=JobStatus.FAILED, finished_at=T0).to_dict()
        json.dumps(data)
        self.assertEqual(data["status"], "failed")
        self.assertIsInstance(data["created_at"], str)

    def test_naive_datetimes_are_not_silently_accepted(self):
        data = _record().to_dict()
        data["created_at"] = "2026-01-01T00:00:00"
        with self.assertRaises(ValueError):
            JobRecord.from_dict(data)


class JobStoreContract:
    """Mixin: subclasses set `make_store(max_jobs)` and `make_peer(store)`."""

    def make_store(self, max_jobs=10):
        raise NotImplementedError

    def make_peer(self, store):
        """A second instance on the same backend (another worker), or None if not applicable."""
        return None

    def test_create_then_get_returns_an_equal_copy(self):
        store = self.make_store()
        record = _record(work_dir="/w", size_bytes=3)
        store.create(record)
        fetched = store.get("j1")
        self.assertEqual(fetched, record)
        fetched.status = JobStatus.FAILED  # a copy: mutating it must not change the store
        self.assertEqual(store.get("j1").status, JobStatus.PENDING)

    def test_get_unknown_is_none(self):
        self.assertIsNone(self.make_store().get("nope"))

    def test_update_saves_a_known_job(self):
        store = self.make_store()
        store.create(_record())
        record = store.get("j1")
        record.status = JobStatus.SUCCEEDED
        record.result = {"ok": True}
        record.finished_at = T0 + timedelta(seconds=1)
        self.assertTrue(store.update(record))
        self.assertEqual(store.get("j1"), record)

    def test_update_of_an_unknown_job_stores_nothing(self):
        store = self.make_store()
        self.assertFalse(store.update(_record("ghost")))
        self.assertIsNone(store.get("ghost"))

    def test_update_of_a_purged_job_returns_false(self):
        store = self.make_store()
        store.create(_record())
        record = store.get("j1")
        store.delete_expired(T0 + timedelta(seconds=TTL), TTL)
        self.assertFalse(store.update(record))
        self.assertIsNone(store.get("j1"))

    def test_cap_rejects_the_job_after_the_last_free_slot(self):
        store = self.make_store(max_jobs=2)
        store.create(_record("a"))
        store.create(_record("b"))  # exactly at the cap: still accepted
        with self.assertRaises(JobStoreFull):
            store.create(_record("c"))
        self.assertIsNone(store.get("c"))

    def test_cap_of_one(self):
        store = self.make_store(max_jobs=1)
        store.create(_record("a"))
        with self.assertRaises(JobStoreFull):
            store.create(_record("b"))

    def test_a_purged_job_frees_its_slot(self):
        store = self.make_store(max_jobs=1)
        store.create(_record("a"))
        store.delete_expired(T0 + timedelta(seconds=TTL), TTL)
        store.create(_record("b"))

    def test_delete_expired_boundary_just_under_at_and_over_the_ttl(self):
        for offset, expected in ((-1, 0), (0, 1), (1, 1)):
            with self.subTest(offset=offset):
                store = self.make_store()
                store.create(_record())
                expired = store.delete_expired(T0 + timedelta(seconds=TTL + offset), TTL)
                self.assertEqual(len(expired), expected)
                self.assertEqual(store.get("j1") is None, bool(expected))

    def test_delete_expired_returns_the_records_it_removed(self):
        store = self.make_store()
        store.create(_record(work_dir="/w/1"))
        expired = store.delete_expired(T0 + timedelta(seconds=TTL), TTL)
        self.assertEqual([r.work_dir for r in expired], ["/w/1"])

    def test_age_counts_from_finishing_not_from_creation(self):
        store = self.make_store()
        store.create(_record())
        record = store.get("j1")
        record.status = JobStatus.SUCCEEDED
        record.finished_at = T0 + timedelta(seconds=50)
        store.update(record)
        self.assertEqual(store.delete_expired(T0 + timedelta(seconds=TTL), TTL), [])
        self.assertEqual(len(store.delete_expired(T0 + timedelta(seconds=50 + TTL), TTL)), 1)

    def test_only_expired_jobs_are_removed(self):
        store = self.make_store()
        store.create(_record("old", created_at=T0))
        store.create(_record("new", created_at=T0 + timedelta(seconds=60)))
        expired = store.delete_expired(T0 + timedelta(seconds=TTL), TTL)
        self.assertEqual([r.job_id for r in expired], ["old"])
        self.assertIsNotNone(store.get("new"))

    def test_delete_expired_is_idempotent(self):
        store = self.make_store()
        store.create(_record())
        now = T0 + timedelta(seconds=TTL)
        self.assertEqual(len(store.delete_expired(now, TTL)), 1)
        self.assertEqual(store.delete_expired(now, TTL), [])

    def test_waiting_counts_only_pending_jobs_and_their_bytes(self):
        store = self.make_store()
        store.create(_record("a", size_bytes=10))
        store.create(_record("b", size_bytes=25))
        store.create(_record("c", status=JobStatus.RUNNING, size_bytes=99))
        store.create(_record("d", status=JobStatus.SUCCEEDED, size_bytes=99))
        self.assertEqual(store.waiting(), (2, 35))

    def test_waiting_of_an_empty_store(self):
        self.assertEqual(self.make_store().waiting(), (0, 0))

    def test_work_dirs_lists_the_dirs_of_stored_jobs(self):
        store = self.make_store()
        store.create(_record("a", work_dir="/w/a"))
        store.create(_record("b", work_dir=None))
        store.create(_record("c", work_dir="/w/c"))
        self.assertEqual(store.work_dirs(), {"/w/a", "/w/c"})

    def test_work_dirs_forgets_purged_jobs(self):
        store = self.make_store()
        store.create(_record("a", work_dir="/w/a"))
        store.delete_expired(T0 + timedelta(seconds=TTL), TTL)
        self.assertEqual(store.work_dirs(), set())

    def test_concurrent_creates_never_exceed_the_cap(self):
        store = self.make_store(max_jobs=5)
        peer = self.make_peer(store) or store
        barrier = threading.Barrier(20)

        def attempt(i):
            barrier.wait()
            try:
                (store if i % 2 else peer).create(_record(f"job-{i}"))
                return True
            except JobStoreFull:
                return False

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(attempt, range(20)))
        self.assertEqual(sum(outcomes), 5)
        self.assertEqual(store.waiting()[0], 5)

    def test_two_workers_see_the_same_jobs(self):
        store = self.make_store()
        peer = self.make_peer(store)
        if peer is None:
            self.skipTest("single-process store")
        store.create(_record("a", size_bytes=4, work_dir="/w/a"))
        self.assertEqual(peer.get("a"), store.get("a"))
        record = peer.get("a")
        record.status = JobStatus.RUNNING
        self.assertTrue(peer.update(record))
        self.assertEqual(store.get("a").status, JobStatus.RUNNING)
        self.assertEqual(peer.waiting(), (0, 0))
        self.assertEqual(peer.work_dirs(), {"/w/a"})
        self.assertEqual(len(peer.delete_expired(T0 + timedelta(seconds=TTL), TTL)), 1)
        self.assertIsNone(store.get("a"))

    def test_two_workers_purging_at_once_report_each_job_once(self):
        store = self.make_store()
        peer = self.make_peer(store)
        if peer is None:
            self.skipTest("single-process store")
        for i in range(10):
            store.create(_record(f"j{i}"))
        now = T0 + timedelta(seconds=TTL)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(lambda s: s.delete_expired(now, TTL), (store, peer))
        self.assertEqual(len(first) + len(second), 10)

    # --- batches (step 24) ---

    def test_create_many_stores_every_job(self):
        store = self.make_store()
        store.create_many([_record("a"), _record("b"), _record("c")])
        self.assertEqual([store.get(i).job_id for i in "abc"], ["a", "b", "c"])

    def _create_many_with_free_slots(self, free, count):
        """Offer `count` jobs to a store (cap 5) with `free` slots left; return (store, offered)."""
        store = self.make_store(max_jobs=5)
        store.create_many([_record(f"old{n}") for n in range(5 - free)])
        offered = [_record(f"new{n}", work_dir=f"/w/new{n}") for n in range(count)]
        return store, offered

    def assertAccepted(self, free, count):
        store, offered = self._create_many_with_free_slots(free, count)
        store.create_many(offered)
        self.assertTrue(all(store.get(r.job_id) for r in offered))

    def assertRefusedWholly(self, free, count):
        store, offered = self._create_many_with_free_slots(free, count)
        with self.assertRaises(JobStoreFull):
            store.create_many(offered)
        self.assertTrue(all(store.get(r.job_id) is None for r in offered))
        self.assertTrue(store.work_dirs().isdisjoint({r.work_dir for r in offered}))

    def test_create_many_fits_exactly_the_free_slots(self):
        self.assertAccepted(free=3, count=3)

    def test_create_many_one_over_the_free_slots_creates_nothing(self):
        self.assertRefusedWholly(free=3, count=4)

    def test_create_many_with_room_for_n_minus_one_creates_nothing(self):
        self.assertRefusedWholly(free=2, count=3)

    def test_create_many_of_one_into_the_last_slot(self):
        self.assertAccepted(free=1, count=1)

    def test_create_many_into_a_full_store_creates_nothing(self):
        self.assertRefusedWholly(free=0, count=1)

    def test_create_many_of_nothing_is_a_no_op(self):
        self.make_store(max_jobs=1).create_many([])

    def test_a_batch_round_trips(self):
        store = self.make_store()
        batch = _batch("b1", ["j1", "j2"])
        store.create_batch(batch)
        self.assertEqual(store.get_batch("b1"), batch)

    def test_get_unknown_batch_is_none(self):
        self.assertIsNone(self.make_store().get_batch("nope"))

    def test_batches_lists_every_stored_batch(self):
        store = self.make_store()
        store.create_batch(_batch("b1", ["j1"]))
        store.create_batch(_batch("b2", ["j2"]))
        self.assertEqual({b.batch_id for b in store.batches()}, {"b1", "b2"})

    def test_delete_batch_removes_only_that_batch(self):
        store = self.make_store()
        store.create_batch(_batch("b1", ["j1"]))
        store.create_batch(_batch("b2", ["j2"]))
        store.delete_batch("b1")
        store.delete_batch("never-existed")
        self.assertIsNone(store.get_batch("b1"))
        self.assertIsNotNone(store.get_batch("b2"))

    def test_delete_returns_the_removed_job_and_frees_its_slot(self):
        store = self.make_store(max_jobs=1)
        record = _record("a", work_dir="/w/a")
        store.create(record)
        self.assertEqual(store.delete("a"), record)
        self.assertIsNone(store.get("a"))
        self.assertIsNone(store.delete("a"))  # already gone
        store.create(_record("b"))

    def test_delete_expired_never_removes_a_job_of_a_batch(self):
        store = self.make_store()
        store.create_many([_record("solo"), _record("grouped", batch_id="b1")])

        removed = store.delete_expired(T0 + timedelta(seconds=TTL * 10), TTL)

        self.assertEqual([r.job_id for r in removed], ["solo"])
        self.assertIsNotNone(store.get("grouped"))

    def test_batches_created_before_boundary(self):
        store = self.make_store()
        store.create_batch(_batch("old", ["j1"], created_at=T0))
        store.create_batch(_batch("new", ["j2"], created_at=T0 + timedelta(seconds=10)))
        # at the cutoff counts (a batch whose age equals the TTL is a candidate), one second before does not
        cases = ((-1, set()), (0, {"old"}), (9, {"old"}), (10, {"old", "new"}))
        for offset, expected in cases:
            with self.subTest(offset=offset):
                found = store.batches_created_before(T0 + timedelta(seconds=offset))
                self.assertEqual({b.batch_id for b in found}, expected)

    def test_the_other_worker_sees_a_batch(self):
        store = self.make_store()
        peer = self.make_peer(store)
        if peer is None:
            self.skipTest("single-process store")
        store.create_batch(_batch("b1", ["j1"]))
        self.assertEqual(peer.get_batch("b1"), _batch("b1", ["j1"]))


class InMemoryJobStoreContractTests(JobStoreContract, SimpleTestCase):
    def make_store(self, max_jobs=10):
        return InMemoryJobStore(max_jobs=max_jobs)


class RedisJobStoreContractTests(JobStoreContract, SimpleTestCase):
    def setUp(self):
        self.server = fakeredis.FakeServer()

    def _client(self):
        return fakeredis.FakeRedis(server=self.server, decode_responses=True)

    def make_store(self, max_jobs=10):
        return RedisJobStore(self._client(), max_jobs=max_jobs, ttl_seconds=TTL)

    def make_peer(self, store):
        return RedisJobStore(self._client(), max_jobs=store.max_jobs, ttl_seconds=TTL)

    def test_a_job_key_gets_a_safety_net_expiry_longer_than_the_ttl(self):
        client = self._client()
        RedisJobStore(client, max_jobs=10, ttl_seconds=TTL).create(_record())
        ttl = client.ttl("mes:job:j1")
        self.assertGreater(ttl, TTL)

    def test_records_survive_a_new_store_instance_on_the_same_server(self):
        self.make_store().create(_record(result=None, work_dir="/w"))
        self.assertEqual(self.make_store().get("j1").work_dir, "/w")

    def test_a_job_whose_key_expired_in_redis_is_gone_and_its_slot_is_freed(self):
        client = self._client()
        store = RedisJobStore(client, max_jobs=1, ttl_seconds=TTL)
        store.create(_record("a"))
        client.delete("mes:job:a")  # as if Redis expired the key
        self.assertIsNone(store.get("a"))
        self.assertEqual(store.waiting(), (0, 0))
        store.create(_record("b"))  # the stale index entry must not block new jobs

    def test_bytes_clients_work_too(self):
        client = fakeredis.FakeRedis(server=self.server)  # no decode_responses
        store = RedisJobStore(client, max_jobs=10, ttl_seconds=TTL)
        store.create(_record(work_dir="/w"))
        self.assertEqual(store.get("j1").work_dir, "/w")
        self.assertEqual(store.work_dirs(), {"/w"})
