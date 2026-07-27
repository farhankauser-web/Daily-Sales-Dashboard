"""Validate + submit NEW/HOLD Walmart orders to Amazon MCF (cron: every 10 min)."""
import json

from django.core.management.base import BaseCommand

from apps.walmart_mcf.core import JobAlreadyRunning, job_lock
from apps.walmart_mcf.pipeline import submit_orders


class Command(BaseCommand):
    help = ('Validate SKU mappings + inventory and create Amazon MCF orders '
            'with Blank Box / Block-AMZL feature constraints.')

    def handle(self, **_):
        try:
            with job_lock('submit_mcf'):
                res = submit_orders()
        except JobAlreadyRunning:
            self.stdout.write('Another submit run is active — skipping.')
            return
        self.stdout.write(json.dumps(res))
