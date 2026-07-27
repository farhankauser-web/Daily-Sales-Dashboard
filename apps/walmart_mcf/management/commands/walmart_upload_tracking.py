"""Upload new tracking numbers to Walmart (cron: every 15 min)."""
import json

from django.core.management.base import BaseCommand

from apps.walmart_mcf.core import JobAlreadyRunning, job_lock
from apps.walmart_mcf.pipeline import upload_tracking


class Command(BaseCommand):
    help = 'Upload not-yet-uploaded shipment packages to Walmart (deduped).'

    def handle(self, **_):
        try:
            with job_lock('upload_tracking'):
                res = upload_tracking()
        except JobAlreadyRunning:
            self.stdout.write('Another upload run is active — skipping.')
            return
        self.stdout.write(json.dumps(res))
