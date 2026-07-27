"""Import released Walmart orders (cron: every 5 minutes)."""
import json

from django.core.management.base import BaseCommand

from apps.walmart_mcf.core import JobAlreadyRunning, job_lock
from apps.walmart_mcf.pipeline import import_orders


class Command(BaseCommand):
    help = 'Import released Walmart Marketplace orders and acknowledge them.'

    def add_arguments(self, parser):
        parser.add_argument('--days-back', type=int, default=7)

    def handle(self, days_back, **_):
        try:
            with job_lock('import_orders'):
                res = import_orders(days_back=days_back)
        except JobAlreadyRunning:
            self.stdout.write('Another import run is active — skipping.')
            return
        self.stdout.write(json.dumps(res))
