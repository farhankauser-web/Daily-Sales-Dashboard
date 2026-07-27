"""Detect Walmart-side cancellations on pre-MCF orders (cron: every 15 min).

Walmart does not push cancellations, so an order a customer cancels while it
still sits in NEW/HOLD/VALIDATED/ERROR would otherwise be submitted to Amazon
and shipped. This polls Walmart for those orders and moves fully-cancelled ones
to CANCELLED, which files them under Archive.
"""
import json

from django.core.management.base import BaseCommand

from apps.walmart_mcf.core import JobAlreadyRunning, job_lock
from apps.walmart_mcf.pipeline import sync_walmart_cancellations


class Command(BaseCommand):
    help = 'Sync Walmart-side cancellations for orders not yet fulfilled.'

    def handle(self, **_):
        try:
            with job_lock('sync_cancellations'):
                res = sync_walmart_cancellations()
        except JobAlreadyRunning:
            self.stdout.write('Another cancellation sync is active — skipping.')
            return
        self.stdout.write(json.dumps(res))
