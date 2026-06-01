"""
sync_sqp_week — Pull the most recently completed ISO week's SQP report.

Brand-level by default (one report per marketplace). Pass --asin to scope to
a single child ASIN.

Cron suggestion (Amazon publishes SQP ~3–7 days after the week ends, run weekly):
    # Tuesdays 11:00 UTC = Mon-completed week typically available
    0 11 * * 2  python manage.py sync_sqp_week  >> /tmp/ix_sqp_sync.log 2>&1

Usage:
    python manage.py sync_sqp_week
    python manage.py sync_sqp_week --marketplace usa
    python manage.py sync_sqp_week --week 2026-W19
    python manage.py sync_sqp_week --asin B09NHVSLV4 --marketplace usa
    python manage.py sync_sqp_week --dry-run
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.sqp.sync import (
    configured_marketplaces,
    iso_week_start,
    last_completed_iso_week,
    sync_sqp_window,
)


ISO_WEEK_RE = re.compile(r'^(\d{4})-W(\d{1,2})$')


def parse_iso_week(s: str) -> tuple[date, date]:
    """'2026-W19' → (Mon, Sun) of ISO week 19, 2026."""
    m = ISO_WEEK_RE.match(s)
    if not m:
        raise CommandError(f'--week must be like 2026-W19, got {s!r}')
    year, wk = int(m.group(1)), int(m.group(2))
    # Use ISO week to anchor a Monday
    monday = date.fromisocalendar(year, wk, 1)
    return monday, monday + timedelta(days=6)


class Command(BaseCommand):
    help = "Sync one ISO week of Brand-Analytics SQP data into SQPSnapshot."

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', help='Single marketplace code; defaults to all active')
        parser.add_argument('--week',        help='ISO week like 2026-W19; defaults to last completed')
        parser.add_argument('--asin',        help='ASIN-scoped report (one per ASIN); blank = brand-level')
        parser.add_argument('--max-wait',    type=int, default=300,
                            help='Seconds to wait for the report (default 300)')
        parser.add_argument('--dry-run',     action='store_true',
                            help='Print what would be fetched without calling SP-API')

    def handle(self, *args, **opts):
        mps = [opts['marketplace']] if opts['marketplace'] else configured_marketplaces()
        if not mps:
            self.stdout.write(self.style.WARNING('No active SP-API configurations.'))
            return

        if opts['week']:
            mon, sun = parse_iso_week(opts['week'])
        else:
            mon, sun = last_completed_iso_week()

        scope = f'asin={opts["asin"]}' if opts['asin'] else 'brand-level'
        self.stdout.write(f'SQP sync · ISO week {mon} → {sun} · {scope}')

        if opts['dry_run']:
            for mp in mps:
                self.stdout.write(f'  [DRY-RUN] {mp}: would request SQP for {mon}→{sun}')
            return

        for mp in mps:
            self.stdout.write(f'\n[{mp}] requesting …')

            def progress(msg):
                self.stdout.write(self.style.NOTICE(f'[{mp}]{msg}'))

            res = sync_sqp_window(
                marketplace      = mp,
                period_start     = mon,
                period_end       = sun,
                period_type      = 'WEEK',
                asin             = opts['asin'],
                max_wait_seconds = opts['max_wait'],
                progress_cb      = progress,
            )
            ok = res['status'] in ('FRESH', 'CACHED', 'OK')
            style = self.style.SUCCESS if ok else self.style.WARNING
            self.stdout.write(style(
                f'[{mp}] {mon}→{sun}  status={res["status"]}  '
                f'rows_loaded={res["rows_loaded"]}  reportId={res.get("report_id") or "-"}'
            ))
            if not ok and res.get('report_id'):
                self.stdout.write(self.style.NOTICE(
                    f"   Report not ready. Re-run the same command later — the persisted "
                    f"SQPReport row (status={res.get('status')}) lets you resume."
                ))
