"""Nightly reconciliation: auto-complete, stuck-order + error summary."""
import json

from django.core.management.base import BaseCommand

from apps.walmart_mcf.core import JobAlreadyRunning, job_lock
from apps.walmart_mcf.pipeline import reconcile


class Command(BaseCommand):
    help = 'Reconcile Walmart↔MCF orders and alert on stuck/error states.'

    def add_arguments(self, parser):
        parser.add_argument('--stuck-after-hours', type=int, default=24)

    def handle(self, stuck_after_hours, **_):
        try:
            with job_lock('reconcile'):
                res = reconcile(stuck_after_hours=stuck_after_hours)
        except JobAlreadyRunning:
            self.stdout.write('Another reconcile run is active — skipping.')
            return
        self.stdout.write(json.dumps(res))
