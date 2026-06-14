"""
compute_sku_ppc — Run the SKU-level PPC allocator (§9 of the spec).

Modes:
  • single date            (--date YYYY-MM-DD)
  • window (last N days)   (--days N)
  • range                  (--start YYYY-MM-DD --end YYYY-MM-DD)
  • full backfill (90d)    (--backfill)

Each run executes Steps 1–8 of the spec for every (marketplace, date) in scope:
load signals → Pass 1 → Pass 2 → reconcile → smooth → persist → lock.

Idempotent. Safe to re-run on the same day. Rows older than T-3 that are
already locked are skipped unless --relock is passed.

Usage:
    python manage.py compute_sku_ppc                              # today only
    python manage.py compute_sku_ppc --days 3                     # T-2 .. T
    python manage.py compute_sku_ppc --date 2026-06-08
    python manage.py compute_sku_ppc --start 2026-05-01 --end 2026-05-31
    python manage.py compute_sku_ppc --backfill                   # last 90d
    python manage.py compute_sku_ppc --marketplace usa --dry-run
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Compute SKU-level PPC allocation for one date / window / backfill.'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; default: all with active config')
        parser.add_argument('--date',  default=None,
                            help='Single date YYYY-MM-DD')
        parser.add_argument('--days',  type=int, default=None,
                            help='Last N days back from today (inclusive)')
        parser.add_argument('--start', default=None, help='Range start YYYY-MM-DD')
        parser.add_argument('--end',   default=None, help='Range end YYYY-MM-DD')
        parser.add_argument('--backfill', action='store_true',
                            help='Last 90 days (overrides other window args)')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.dashboard.ppc_allocator import compute_for_day

        # ── Resolve marketplaces ─────────────────────────────────────────
        if opts['marketplace']:
            mps = [opts['marketplace']]
        else:
            mps = list(AmazonAPIConfig.objects
                       .filter(is_active=True).values_list('marketplace', flat=True))
        if not mps:
            self.stderr.write(self.style.ERROR('No active marketplaces.'))
            return

        # ── Resolve date window ──────────────────────────────────────────
        today = date.today()
        if opts['backfill']:
            start_d = today - timedelta(days=90)
            end_d   = today
        elif opts['start'] or opts['end']:
            start_d = date.fromisoformat(opts['start']) if opts['start'] else (today - timedelta(days=7))
            end_d   = date.fromisoformat(opts['end'])   if opts['end']   else today
        elif opts['date']:
            start_d = end_d = date.fromisoformat(opts['date'])
        elif opts['days']:
            end_d   = today
            start_d = today - timedelta(days=opts['days'] - 1)
        else:
            start_d = end_d = today

        if start_d > end_d:
            self.stderr.write(self.style.ERROR(
                f'Empty window: start={start_d} > end={end_d}'))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n📊  SKU PPC allocation  |  {start_d} → {end_d}  |  '
            f'MPs: {", ".join(mps)}' + ('  (DRY-RUN)' if opts['dry_run'] else '')
        ))

        # ── Run ──────────────────────────────────────────────────────────
        for mp in mps:
            self.stdout.write(self.style.MIGRATE_LABEL(f'\n  [{mp.upper()}]'))
            cur = start_d
            mp_total_rows = 0
            while cur <= end_d:
                try:
                    res = compute_for_day(mp, cur, dry_run=opts['dry_run'])
                except Exception as e:
                    self.stderr.write(self.style.ERROR(
                        f'    {cur}: {type(e).__name__}: {e}'))
                    cur += timedelta(days=1)
                    continue

                # One-line status per day
                gap = res['sum_campaign'] - res['sum_alloc']
                marker = '✓' if abs(gap) <= 0.05 else '⚠'
                self.stdout.write(
                    f'    {marker} {res["date"]}  state={res["state_target"]:<11s}  '
                    f'camps={res["campaigns_with_spend"]:>3d}  '
                    f'rows={res["rows_written"]:>5d}  '
                    f'alloc=${res["sum_alloc"]:>10,.2f}  '
                    f'spend=${res["sum_campaign"]:>10,.2f}  '
                    f'gap=${gap:>+8.2f}'
                )
                mp_total_rows += res['rows_written']
                cur += timedelta(days=1)

            self.stdout.write(self.style.SUCCESS(
                f'    ─── [{mp.upper()}] {mp_total_rows} rows written ───'))

        self.stdout.write(self.style.SUCCESS('\n✅  Allocation complete.\n'))
