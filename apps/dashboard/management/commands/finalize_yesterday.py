"""
finalize_yesterday — Lock yesterday's data into DailyMetric + DailySkuSnapshot.

Runs at 00:45 marketplace-local time each day. After execution, yesterday's
rows have `finalized_at` set and cannot be rewritten by the hourly cron.

PPC fields continue to refresh independently via backfill_ppc (which writes
to PPCCampaignSnapshot / PPCProductSnapshot, not to DailyMetric directly)
for the next 7 days to capture late-attributed conversions.

Usage:
    python manage.py finalize_yesterday                       # all active MPs
    python manage.py finalize_yesterday --marketplace usa
    python manage.py finalize_yesterday --date 2026-06-08     # specific day
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.dashboard.sync import configured_marketplaces, finalize_day, finalize_yesterday


class Command(BaseCommand):
    help = "Finalize yesterday's DailyMetric + DailySkuSnapshot. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', help='Single marketplace (defaults to all active)')
        parser.add_argument('--date',
                            help='YYYY-MM-DD to finalize (defaults to yesterday in marketplace TZ)')
        parser.add_argument('--max-wait', type=int, default=180,
                            help='Max seconds to wait for SP-API report (default 180)')

    def handle(self, *args, **opts):
        mps = [opts['marketplace']] if opts['marketplace'] else configured_marketplaces()
        if not mps:
            self.stdout.write(self.style.WARNING('No active SP-API configurations.'))
            return

        for mp in mps:
            def progress(msg, _mp=mp):
                self.stdout.write(self.style.NOTICE(f'[{_mp}]{msg}'))

            if opts['date']:
                day = datetime.strptime(opts['date'], '%Y-%m-%d').date()
                self.stdout.write(f'[{mp}] finalizing {day} (specified) …')
                res = finalize_day(mp, day,
                                   max_wait_seconds=opts['max_wait'],
                                   progress_cb=progress)
            else:
                tz = settings.AMAZON_MARKETPLACES.get(mp, {}).get('timezone', settings.TIME_ZONE)
                today = datetime.now(tz=ZoneInfo(tz)).date()
                self.stdout.write(f'[{mp}] finalizing yesterday ({today - timedelta(days=1)}) …')
                res = finalize_yesterday(mp, today=today,
                                         max_wait_seconds=opts['max_wait'],
                                         progress_cb=progress)

            ok = res['status'] in ('finalized', 'already_finalized')
            style = self.style.SUCCESS if ok else self.style.WARNING
            self.stdout.write(style(
                f'[{mp}] {res["date"]}  status={res["status"]}  '
                f'days_written={res.get("days_written", 0)}  '
                f'sku_rows={res.get("sku_rows_written", 0)}  '
                f'daily_locked={res.get("daily_rows_locked", 0)}'
            ))
