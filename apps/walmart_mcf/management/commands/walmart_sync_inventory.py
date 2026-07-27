"""Daily Amazon → Walmart inventory sync (cron: daily 06:30)."""
import json

from django.core.management.base import BaseCommand

from apps.walmart_mcf.core import JobAlreadyRunning, job_lock
from apps.walmart_mcf.pipeline import sync_inventory


class Command(BaseCommand):
    help = ('Push Amazon blank-box-fulfillable stock to Walmart for every '
            'enabled SKU mapping.')

    def add_arguments(self, parser):
        parser.add_argument('--buffer', type=int, default=0,
                            help='Percent held back, e.g. 10 → push 90%% of stock')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, buffer, dry_run, **_):
        try:
            with job_lock('sync_inventory'):
                res = sync_inventory(buffer_pct=buffer, dry_run=dry_run)
        except JobAlreadyRunning:
            self.stdout.write('Another inventory sync is active — skipping.')
            return
        self.stdout.write(json.dumps(res, indent=1))
