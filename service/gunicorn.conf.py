"""Gunicorn settings. Also wires prometheus_client's multiprocess mode across workers."""

import os
import shutil

bind = "0.0.0.0:8000"
workers = 2
timeout = 120

_MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")


def on_starting(server):
    # Drop stale metric files from a previous run so counters start from zero.
    if _MULTIPROC_DIR:
        shutil.rmtree(_MULTIPROC_DIR, ignore_errors=True)
        os.makedirs(_MULTIPROC_DIR, exist_ok=True)


def child_exit(server, worker):
    if _MULTIPROC_DIR:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
