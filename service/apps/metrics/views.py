import os

from django.http import HttpResponse
from django.views.decorators.http import require_GET
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest
from prometheus_client import multiprocess


@require_GET
def metrics(request):
    """Prometheus scrape endpoint. Aggregates all gunicorn workers when multiprocess mode is on."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
