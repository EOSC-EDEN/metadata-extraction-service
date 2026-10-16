"""Gunicorn settings. Also wires prometheus_client's multiprocess mode across workers."""

import os
import shutil

bind = "0.0.0.0:8000"
# With MES_JOB_STORE=memory (the default) asynchronous jobs live in one process, so more than one
# worker process makes /api/v1/jobs/ answer "no such job" whenever a request lands on another
# worker: keep WEB_CONCURRENCY=1 and use GUNICORN_THREADS for request concurrency. With
# MES_JOB_STORE=redis the jobs are shared and WEB_CONCURRENCY can be raised.
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
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
