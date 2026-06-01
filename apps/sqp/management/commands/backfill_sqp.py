"""
backfill_sqp — Seed SQP history by submitting one report per ISO week.

Unlike the FlatFileAllOrders report (which accepts a multi-week window in one
shot), the SQP report is keyed to a *single* reportPeriod (WEEK/MONTH/QUARTER).
So a 12-week backfill = 12 separate Amazon report jobs per marketplace.

Each report is small (a few hundred rows) so the per-week wait is short
(~30-90s). We submit serially with a small inter-request sleep to respect
createReport's 1/min rate limit.

Usage:
    python manage.py backfill_sqp                                # last 12 weeks, all MPs
    python manage.py backfill_sqp --weeks 26 --marketplace usa
    python manage.py backfill_sqp --from 2026-W10 --to 2026-W19
    python manage.py backfill_sqp --asin B09NHVSLV4 --weeks 6
    python manage.py backfill_sqp --skip-existing                # don't re-fetch weeks already 'done'
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.sqp.models import SQPReport
from apps.sqp.sync import (
    configured_marketplaces,
    iso_week_start,
    last_completed_iso_week,
    sync_sqp_window,
)

ISO_WEEK_RE = re.compile(r'^(\d{4})-W(\d{1,2})$')


def parse_iso_week(s: str) -> date:
    m = ISO_WEEK_RE.match(s)
    if not m:
        raise CommandError(f'Expected YYYY-Wnn, got {s!r}')
    return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)


class Command(BaseCommand):
    help = "Backfill historical SQP weeks (one Amazon report per week per marketplace)."

    def add_arguments(self, parser):
        parser.add_argument('--weeks', type=int, default=12,
                            help='How many weeks back to fill (default 12)')
        parser.add_argument('--from', dest='from_week',
                            help='Start week, e.g. 2026-W10 (overrides --weeks)')
        parser.add_argument('--to',   dest='to_week',
                            help='End week, e.g. 2026-W19 (defaults to last completed)')
        parser.add_argument('--marketplace', help='Single marketplace; defaults to all active')
        parser.add_argument('--asin',        help='ASIN-scoped backfill (blank = brand-level)')
        parser.add_argument('--max-wait',    type=int, default=300,
                            help='Seconds to wait per week (default 300)')
        parser.add_argument('--sleep-between', type=int, default=65,
                            help='Seconds to sleep between report submissions to respect '
                                 'createReport 1/min rate (default 65)')
        parser.add_argument('--skip-existing', action='store_true',
                            help="Don't re-fetch weeks already marked 'done' in SQPReport")
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        mps = [opts['marketplace']] if opts['marketplace'] else configured_marketplaces()
        if not mps:
            self.stdout.write(self.style.WARNING('No active SP-API configurations.'))
            return

        # Resolve week window
        if opts['to_week']:
            end_monday = parse_iso_week(opts['to_week'])
        else:
            end_monday, _ = last_completed_iso_week()
        if opts['from_week']:
            start_monday = parse_iso_week(opts['from_week'])
        else:
            start_monday = end_monday - timedelta(weeks=opts['weeks'] - 1)

        # Build week list
        weeks: list[date] = []
        cur = start_monday
        while cur <= end_monday:
            weeks.append(cur)
            cur += timedelta(weeks=1)

        scope = f'asin={opts["asin"]}' if opts['asin'] else 'brand-level'
        self.stdout.write(
            f'Backfill plan · {len(weeks)} weeks ({start_monday} → {end_monday + timedelta(days=6)}) · '
            f'{len(mps)} marketplaces · {scope}'
        )
        if opts['dry_run']:
            for mp in mps:
                for mon in weeks:
                    self.stdout.write(f'  [DRY-RUN] {mp}: would request {mon} → {mon + timedelta(days=6)}')
            return

        for mp in mps:
            for i, mon in enumerate(weeks, start=1):
                sun = mon + timedelta(days=6)

                if opts['skip_existing']:
                    existing = SQPReport.objects.filter(
                        marketplace=mp, asin=opts['asin'] or '',
                        period_type='WEEK', period_start=mon, status='done',
                    ).first()
                    if existing:
                        self.stdout.write(self.style.NOTICE(
                            f'[{mp}] skip {mon} (already done · rows_loaded={existing.rows_loaded})'
                        ))
                        continue

                self.stdout.write(f'\n[{mp}] week {i}/{len(weeks)} · {mon} → {sun}')

                def progress(msg, _mp=mp):
                    self.stdout.write(self.style.NOTICE(f'[{_mp}]{msg}'))

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
                    f'  → status={res["status"]}  rows_loaded={res["rows_loaded"]}'
                ))

                # Rate-limit: 1 createReport per minute. Skip sleep after the
                # last week of the last marketplace.
                if not (mp == mps[-1] and mon == weeks[-1]):
                    time.sleep(opts['sleep_between'])

        self.stdout.write(self.style.SUCCESS('\nBackfill complete.'))
