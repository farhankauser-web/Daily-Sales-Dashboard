"""Poll Amazon fulfillment status + harvest packages (cron: every 15 min)."""
import json

from django.core.management.base import BaseCommand

from apps.walmart_mcf.core import JobAlreadyRunning, job_lock
from apps.walmart_mcf.pipeline import check_status


class Command(BaseCommand):
    help = 'Refresh Amazon MCF fulfillment statuses and collect tracking numbers.'

    def handle(self, **_):
        try:
            with job_lock('check_status'):
                res = check_status()
        except JobAlreadyRunning:
            self.stdout.write('Another status run is active — skipping.')
            return
        self.stdout.write(json.dumps(res))
