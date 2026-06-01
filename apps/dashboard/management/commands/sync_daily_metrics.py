"""
sync_daily_metrics — Pull yesterday's (or any single day's) FlatFileAllOrdersReport
from SP-API and write per-day rows into DailyMetric.

Run via cron at, e.g., 03:00 marketplace-local time:
    0 10 * * *  python manage.py sync_daily_metrics  # 03:00 PT == 10:00 UTC

Usage:
    python manage.py sync_daily_metrics                   # yesterday, all configured MPs
    python manage.py sync_daily_metrics --date 2026-05-09 # specific day
    python manage.py sync_daily_metrics --marketplace usa # one MP only
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.dashboard.sync import configured_marketplaces, sync_window


class Command(BaseCommand):
    help = "Sync one day's FlatFileAllOrdersReport into DailyMetric."

    def add_arguments(self, parser):
        parser.add_argument('--date', help='YYYY-MM-DD (defaults to yesterday in marketplace TZ)')
        parser.add_argument('--marketplace', help='Single marketplace code (defaults to all active)')
        parser.add_argument('--max-wait', type=int, default=90,
                            help='Max seconds to poll Amazon for the report (default 90)')

    def handle(self, *args, **opts):
        mps = [opts['marketplace']] if opts['marketplace'] else configured_marketplaces()
        if not mps:
            self.stdout.write(self.style.WARNING('No active SP-API configurations found.'))
            return

        for mp in mps:
            tz_name = settings.AMAZON_MARKETPLACES.get(mp, {}).get('timezone', settings.TIME_ZONE)
            today = datetime.now(tz=ZoneInfo(tz_name)).date()
            if opts['date']:
                day = datetime.strptime(opts['date'], '%Y-%m-%d').date()
            else:
                day = today - timedelta(days=1)

            self.stdout.write(f'[{mp}] syncing {day} …')
            res = sync_window(mp, day, day, max_wait_seconds=opts['max_wait'])
            self.stdout.write(self.style.SUCCESS(
                f'[{mp}] {day}  status={res["status"]}  rows={res["rows"]}  days={res["days_written"]}'
            ))
