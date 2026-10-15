"""Test doubles for the async job service."""

from concurrent.futures import Future
from datetime import datetime, timedelta, timezone


class ImmediateExecutor:
    """Runs submitted work synchronously, so a job is finished when `submit` returns."""

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # mirrors ThreadPoolExecutor: errors live on the future
            future.set_exception(exc)
        return future


class DeferredExecutor:
    """Holds submitted work until `run_all`, so tests can observe pending/running jobs."""

    def __init__(self):
        self.pending = []

    def submit(self, fn, *args, **kwargs):
        self.pending.append((fn, args, kwargs))
        return Future()

    def run_all(self):
        while self.pending:
            fn, args, kwargs = self.pending.pop(0)
            fn(*args, **kwargs)


class FakeClock:
    def __init__(self, start=None):
        self.now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
