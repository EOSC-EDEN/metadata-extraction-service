import os

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest
from prometheus_client import multiprocess

from apps.api.jobs import get_job_service
from apps.metrics.metrics import JobsWaitingCollector


@require_GET
def metrics(request):
    """Prometheus scrape endpoint. Aggregates all gunicorn workers when multiprocess mode is on."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    # The waiting jobs describe this moment, so they are read from the job store now (dropping
    # expired jobs first) instead of being kept as per-process gauges.
    waiting = get_job_service().waiting if settings.MES_ASYNC_ENABLED else (lambda: (0, 0))
    jobs_registry = CollectorRegistry()
    jobs_registry.register(JobsWaitingCollector(waiting))
    body = generate_latest(registry) + generate_latest(jobs_registry)
    return HttpResponse(body, content_type=CONTENT_TYPE_LATEST)
