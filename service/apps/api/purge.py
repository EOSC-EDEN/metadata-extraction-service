"""Retention helpers for asynchronous jobs: the purge schedule and the orphaned-upload sweep."""

import os
import shutil

_MIN_MINUTES = 1
_MAX_MINUTES = 60


def mes_purge_interval_minutes(ttl_seconds: int) -> int:
    """How often to purge, in whole minutes: a quarter of the TTL, between 1 minute (cron's
    granularity) and 1 hour. Expired data is therefore removed at most about a quarter of the TTL
    late; it is already unreachable (404) before that."""
    if ttl_seconds < 1:
        raise ValueError("The TTL must be at least 1 second.")
    return max(_MIN_MINUTES, min(_MAX_MINUTES, ttl_seconds // 4 // 60))


def mes_purge_schedule(ttl_seconds: int) -> str:
    """The crontab time expression for `mes_purge_interval_minutes`."""
    minutes = mes_purge_interval_minutes(ttl_seconds)
    return "0 * * * *" if minutes == _MAX_MINUTES else f"*/{minutes} * * * *"


def sweep_orphaned_uploads(upload_dir: str, referenced: set[str], ttl_seconds: int, now: float) -> int:
    """Remove upload directories at least `ttl_seconds` old that no stored job refers to.

    These are left behind when a worker dies mid-job or mid-request. Returns how many were removed.
    """
    try:
        entries = list(os.scandir(upload_dir))
    except FileNotFoundError:
        return 0
    referenced = {os.path.abspath(path) for path in referenced}
    removed = 0
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False) or os.path.abspath(entry.path) in referenced:
            continue
        try:
            age = now - entry.stat(follow_symlinks=False).st_mtime
        except FileNotFoundError:  # removed meanwhile, e.g. by another purge run
            continue
        if age >= ttl_seconds:
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
    return removed
