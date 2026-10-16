import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.api.jobs import get_job_service
from apps.api.purge import sweep_orphaned_uploads

logger = logging.getLogger(__name__)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


class Command(BaseCommand):
    help = (
        "Delete asynchronous jobs older than MES_JOB_TTL_SECONDS with their uploads, and upload "
        "directories that no job refers to any more. Safe to run repeatedly."
    )

    def handle(self, *args, **options):
        service = get_job_service()
        jobs = service.purge_expired()
        orphans = sweep_orphaned_uploads(
            settings.MES_JOB_UPLOAD_DIR,
            service.live_work_dirs(),
            settings.MES_JOB_TTL_SECONDS,
            time.time(),
        )
        message = f"Purged {_count(jobs, 'expired job')} and {_count(orphans, 'orphaned upload')}."
        logger.info(message)
        self.stdout.write(message)
