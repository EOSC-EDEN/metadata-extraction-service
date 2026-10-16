from django.conf import settings
from django.core.management.base import BaseCommand

from apps.api.purge import mes_purge_interval_minutes, mes_purge_schedule


class Command(BaseCommand):
    help = "Print how often purge_expired_jobs should run, derived from MES_JOB_TTL_SECONDS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes", action="store_true", help="print the interval in minutes, not a crontab time"
        )

    def handle(self, *args, **options):
        ttl = settings.MES_JOB_TTL_SECONDS
        self.stdout.write(
            str(mes_purge_interval_minutes(ttl)) if options["minutes"] else mes_purge_schedule(ttl)
        )
