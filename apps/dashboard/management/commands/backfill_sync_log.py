"""
apps/dashboard/management/commands/backfill_sync_log.py

Retroactively populates AdsDataSyncLog from the data we already have in the
snapshot tables, so the Hourly Patterns page can render historical days that
were ingested BEFORE the sync-log hooks were wired into the ingestion commands.

Rules (strict — same evidence model as the live sync hooks):

  Orders:
    - If HourlyMetricSnapshot has rows for (mp, date) → log 'ok' with row count.
    - No rows → no log entry (we don't fabricate "empty_from_amazon" — we don't
      actually know whether Amazon returned 0 or we just never asked).

  SP hourly:
    - If PPCCampaignHourlySnapshot has rows for (mp, date) → log 'ok'.
    - This table is brand new, so it'll usually be empty until backfill_sp_hourly
      runs. The command will report 0 days and tell you to run that command.

  SB / SD daily:
    - If PPCCampaignSnapshot has rows for (mp, date, type) → log 'ok'.
    - No rows → no log entry (same reasoning as Orders).

Idempotent: running it again only adds NEW (mp, date, source) entries; existing
'ok' / 'empty_from_amazon' rows are left untouched unless you pass --force.

Usage:
    python manage.py backfill_sync_log                        # last 30 days, all MPs
    python manage.py backfill_sync_log --marketplace usa
    python manage.py backfill_sync_log --days 60
    python manage.py backfill_sync_log --start 2026-05-01 --end 2026-06-08
    python manage.py backfill_sync_log --force                # overwrite existing 'ok' rows
    python manage.py backfill_sync_log --dry-run              # report only, no writes
"""
from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.dashboard.completeness import log_sync, ALL_SOURCES
from apps.dashboard.models import (
    AdsDataSyncLog,
    HourlyMetricSnapshot,
    PPCCampaignHourlySnapshot,
    PPCCampaignSnapshot,
)


class Command(BaseCommand):
    help = ('Backfill AdsDataSyncLog from existing snapshot tables so historical '
            'days are recognized as complete by the Hourly Patterns view.')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace (default: every MP seen in the data)')
        parser.add_argument('--days', type=int, default=30,
                            help='Days back from yesterday (default: 30)')
        parser.add_argument('--start', default=None, help='YYYY-MM-DD start (overrides --days)')
        parser.add_argument('--end',   default=None, help='YYYY-MM-DD end (default: yesterday)')
        parser.add_argument('--force', action='store_true',
                            help='Re-log entries even if a successful one already exists.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would be written; do not change the DB.')

    def handle(self, *args, **opts):
        # ── Resolve window ──────────────────────────────────────────────────
        yesterday = date.today() - timedelta(days=1)
        end_d = date.fromisoformat(opts['end']) if opts['end'] else yesterday
        if opts['start']:
            start_d = date.fromisoformat(opts['start'])
        else:
            start_d = end_d - timedelta(days=opts['days'] - 1)

        # ── Resolve marketplaces ────────────────────────────────────────────
        if opts['marketplace']:
            mps = [opts['marketplace']]
        else:
            mps = self._discover_marketplaces(start_d, end_d)
        if not mps:
            self.stderr.write(self.style.WARNING(
                'No marketplaces found in any snapshot table for that window.'))
            return

        force   = opts['force']
        dry_run = opts['dry_run']

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n📋 Backfilling AdsDataSyncLog  |  {start_d} → {end_d}  |  MPs: {", ".join(mps)}'
            + ('  (DRY-RUN)' if dry_run else '')
        ))

        for mp in mps:
            self.stdout.write(self.style.MIGRATE_LABEL(f'\n  [{mp.upper()}]'))

            stats = {s: {'ok': 0, 'skipped': 0, 'no_data': 0} for s in ALL_SOURCES}

            cur = start_d
            while cur <= end_d:
                self._maybe_log(mp, cur, 'orders',
                                self._orders_rows(mp, cur),
                                stats, force, dry_run)
                self._maybe_log(mp, cur, 'sp_hourly',
                                self._sp_hourly_rows(mp, cur),
                                stats, force, dry_run)
                self._maybe_log(mp, cur, 'sb_daily',
                                self._ppc_daily_rows(mp, cur, 'sb'),
                                stats, force, dry_run)
                self._maybe_log(mp, cur, 'sd_daily',
                                self._ppc_daily_rows(mp, cur, 'sd'),
                                stats, force, dry_run)
                cur += timedelta(days=1)

            # Summary
            for src in ALL_SOURCES:
                s = stats[src]
                self.stdout.write(
                    f'    {src:<10s}  logged={s["ok"]:>3d}   '
                    f'no-data={s["no_data"]:>3d}   '
                    f'already-ok={s["skipped"]:>3d}'
                )

        # ── Helpful follow-up message ───────────────────────────────────────
        self.stdout.write(self.style.SUCCESS('\n✅  Backfill done.'))
        sph_empty = (PPCCampaignHourlySnapshot.objects
                     .filter(date__gte=start_d, date__lte=end_d).count() == 0)
        if sph_empty:
            self.stdout.write(self.style.WARNING(
                '\n⚠  PPCCampaignHourlySnapshot is still empty for the whole window. '
                'Hourly Patterns will continue to show "Core data missing" until you run:\n'
                f'      python manage.py backfill_sp_hourly --marketplace {mps[0]} --days 7\n'
                '   (or whichever window of past days you want to render). Note: Amazon '
                'caps HOURLY data at 30 days of retention.'
            ))

    # ─────────────────────────────────────────────────────────────────────
    def _maybe_log(self, mp: str, d: date, source: str, n_rows: int,
                   stats: dict, force: bool, dry_run: bool):
        if n_rows <= 0:
            stats[source]['no_data'] += 1
            return

        if not force:
            existing = AdsDataSyncLog.objects.filter(
                marketplace=mp, date=d, source=source).first()
            if existing and existing.is_successful:
                stats[source]['skipped'] += 1
                return

        if dry_run:
            stats[source]['ok'] += 1
            return

        log_sync(mp, d, source, 'ok',
                 rows_received=n_rows,
                 error_message='retroactively reconstructed from snapshot tables')
        stats[source]['ok'] += 1

    # ─────────────────────────────────────────────────────────────────────
    def _orders_rows(self, mp: str, d: date) -> int:
        return (HourlyMetricSnapshot.objects
                .filter(marketplace=mp, date=d).count())

    def _sp_hourly_rows(self, mp: str, d: date) -> int:
        return (PPCCampaignHourlySnapshot.objects
                .filter(marketplace=mp, date=d).count())

    def _ppc_daily_rows(self, mp: str, d: date, ctype: str) -> int:
        return (PPCCampaignSnapshot.objects
                .filter(marketplace=mp, date=d, campaign_type=ctype).count())

    def _discover_marketplaces(self, start_d: date, end_d: date) -> list[str]:
        seen: set[str] = set()
        for qs in (
            HourlyMetricSnapshot.objects.filter(date__gte=start_d, date__lte=end_d),
            PPCCampaignSnapshot.objects.filter(date__gte=start_d, date__lte=end_d),
            PPCCampaignHourlySnapshot.objects.filter(date__gte=start_d, date__lte=end_d),
        ):
            seen.update(qs.values_list('marketplace', flat=True).distinct())
        return sorted(seen)
