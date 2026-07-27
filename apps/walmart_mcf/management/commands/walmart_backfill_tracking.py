"""Backfill Walmart tracking from manually-created Amazon MCF orders."""
import json

from django.core.management.base import BaseCommand

from apps.walmart_mcf.core import JobAlreadyRunning, job_lock
from apps.walmart_mcf.pipeline import backfill_manual_tracking


class Command(BaseCommand):
    help = ('Upload tracking to Walmart for Acknowledged orders whose MCF '
            'order was created manually in Seller Central (matched by PO id).')

    def add_arguments(self, parser):
        parser.add_argument('--days-back', type=int, default=30)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, days_back, dry_run, **_):
        try:
            with job_lock('backfill_tracking'):
                res = backfill_manual_tracking(days_back=days_back,
                                               dry_run=dry_run)
        except JobAlreadyRunning:
            self.stdout.write('Another backfill run is active — skipping.')
            return
        self.stdout.write(json.dumps(res, indent=1, default=str))
