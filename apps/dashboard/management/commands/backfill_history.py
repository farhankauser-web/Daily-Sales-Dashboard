"""
backfill_history — Seed DailyMetric with historical days.

Strategy: ONE FlatFileAllOrdersReport call covers the whole window
(much cheaper than N daily reports). Then aggregate per day in our code.

Usage:
    python manage.py backfill_history                 # last 30 days, all MPs
    python manage.py backfill_history --days 90       # last 90 days
    python manage.py backfill_history --start 2026-01-01 --end 2026-05-10
    python manage.py backfill_history --marketplace usa
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.dashboard.sync import configured_marketplaces, sync_window


class Command(BaseCommand):
    help = "Backfill DailyMetric for a historical window using one bulk SP-API report per marketplace."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='How many days back from yesterday (default 30)')
        parser.add_argument('--start', help='YYYY-MM-DD (overrides --days)')
        parser.add_argument('--end',   help='YYYY-MM-DD (defaults to yesterday)')
        parser.add_argument('--marketplace', help='Single marketplace (defaults to all active)')
        parser.add_argument('--max-wait', type=int, default=900,
                            help='Max seconds to wait for the bulk report (default 900 = 15 min). '
                                 'Long ranges (90D, YTD) sometimes need 20+ min — re-run if PENDING.')

    def handle(self, *args, **opts):
        mps = [opts['marketplace']] if opts['marketplace'] else configured_marketplaces()
        if not mps:
            self.stdout.write(self.style.WARNING('No active SP-API configurations found.'))
            return

        for mp in mps:
            tz_name = settings.AMAZON_MARKETPLACES.get(mp, {}).get('timezone', settings.TIME_ZONE)
            today = datetime.now(tz=ZoneInfo(tz_name)).date()

            end = (datetime.strptime(opts['end'], '%Y-%m-%d').date()
                   if opts['end'] else today - timedelta(days=1))
            if opts['start']:
                start = datetime.strptime(opts['start'], '%Y-%m-%d').date()
            else:
                start = end - timedelta(days=opts['days'] - 1)

            self.stdout.write(f'[{mp}] backfilling {start} → {end}  ({(end - start).days + 1} days)  '
                              f'(waiting up to {opts["max_wait"]}s for Amazon to build the report) …')

            def progress(msg):
                self.stdout.write(self.style.NOTICE(f'[{mp}]{msg}'))

            res = sync_window(mp, start, end,
                              max_wait_seconds=opts['max_wait'],
                              progress_cb=progress)

            ok = res['status'] in ('OK', 'CACHED', 'FRESH')
            style = self.style.SUCCESS if ok else self.style.WARNING
            self.stdout.write(style(
                f'[{mp}] {start}→{end}  status={res["status"]}  '
                f'rows={res["rows"]}  days_written={res["days_written"]}'
                + (f"  days_with_orders={res['days_with_orders']}" if 'days_with_orders' in res else '')
            ))
            if not ok:
                rid = res.get('report_id')
                self.stdout.write(self.style.NOTICE(
                    f"   Report not ready yet (id={rid}). "
                    "Re-run this exact command in 2-5 min — the cached reportId will be polled "
                    "and downloaded as soon as Amazon finishes building it."
                ))
