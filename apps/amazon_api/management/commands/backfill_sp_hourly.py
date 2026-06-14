"""
apps/amazon_api/management/commands/backfill_sp_hourly.py

T-1 ingestion of Sponsored Products HOURLY campaign data
(Amazon Ads API v3 — timeUnit=HOURLY).

  • Writes one PPCCampaignHourlySnapshot row per
    (marketplace, date, hour, campaign_id)
  • Writes one AdsDataSyncLog row per (marketplace, date, 'sp_hourly')
    with status = 'ok' | 'empty_from_amazon' | 'failed' | 'pending'

Layer:  Ads Layer / SP Hourly (real, not estimated)
Used by: Hourly Patterns page (gates day visibility together with Orders)

NOTE: SB and SD do NOT support timeUnit=HOURLY — they continue to use the
daily backfill_ppc command and the Hourly Patterns view will allocate them
daily÷24 with an "estimated" badge.

Usage:
    # Default: yesterday only
    python manage.py backfill_sp_hourly --marketplace usa

    # Catch up the last N days (Amazon retains 30 days of HOURLY data)
    python manage.py backfill_sp_hourly --marketplace usa --days 7

    # Explicit window
    python manage.py backfill_sp_hourly --marketplace usa \\
        --start 2026-06-01 --end 2026-06-08

    # Resume an in-flight report (status='pending' in AdsDataSyncLog)
    python manage.py backfill_sp_hourly --marketplace usa \\
        --date 2026-06-08 --resume-report-id 1234abcd
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# Accept either "2026-06-08T14:00:00Z" / "2026-06-08 14:00" / "2026-06-08" + hour field
_ISO_HOUR_RX = re.compile(r'(\d{4}-\d{2}-\d{2})[T ](\d{1,2})')


def _parse_date_and_hour(row: dict) -> tuple[date | None, int | None]:
    """
    Extract (date, hour) from one Ads API HOURLY row.

    Amazon's HOURLY reports return the bucket boundary in `date`. Some accounts
    also include a separate `hour` integer column. We accept both shapes.
    """
    # Prefer an explicit hour column if present
    hour_val = row.get('hour')
    if hour_val is not None:
        try:
            hour_int = int(hour_val)
        except (TypeError, ValueError):
            hour_int = None
    else:
        hour_int = None

    raw_date = row.get('date') or row.get('reportDate') or row.get('startDate') or ''
    raw_date = str(raw_date).strip()
    if not raw_date:
        return None, None

    # Case 1: combined timestamp "2026-06-08T14:00:00Z" or "2026-06-08 14:00:00"
    m = _ISO_HOUR_RX.match(raw_date)
    if m:
        d = date.fromisoformat(m.group(1))
        h = int(m.group(2))
        if 0 <= h <= 23:
            return d, h

    # Case 2: pure date "2026-06-08" + separate hour column
    try:
        d = date.fromisoformat(raw_date[:10])
    except ValueError:
        return None, None

    if hour_int is not None and 0 <= hour_int <= 23:
        return d, hour_int

    return d, None  # caller will log/skip


class Command(BaseCommand):
    help = ('Backfill Sponsored Products HOURLY campaign data into '
            'PPCCampaignHourlySnapshot and log to AdsDataSyncLog.')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--days', type=int, default=1,
                            help='Days back from yesterday, inclusive (default: 1 = yesterday only).')
        parser.add_argument('--start', default=None, help='YYYY-MM-DD inclusive')
        parser.add_argument('--end',   default=None, help='YYYY-MM-DD inclusive (defaults to yesterday)')
        parser.add_argument('--date',  default=None,
                            help='Shortcut: single YYYY-MM-DD (overrides --start/--end/--days).')
        parser.add_argument('--resume-report-id', default=None,
                            help='Resume a previously-submitted SP hourly report (requires --date).')
        parser.add_argument('--force', action='store_true',
                            help='Re-fetch even if AdsDataSyncLog already has a successful entry.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Submit + parse, but do not write to the DB.')

    # ─────────────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import AdsAPIClient
        from apps.dashboard.completeness import log_sync

        mp = opts['marketplace']
        cfg = AmazonAPIConfig.objects.filter(marketplace=mp, is_active=True).first()
        if not cfg:
            self.stderr.write(self.style.ERROR(
                f'No active AmazonAPIConfig for marketplace "{mp}".'))
            return
        if not cfg.has_ads_credentials():
            self.stderr.write(self.style.ERROR(
                'Ads API credentials not configured for this marketplace.'))
            return

        # ── Resolve window ──────────────────────────────────────────────
        if opts['date']:
            d = date.fromisoformat(opts['date'])
            window = [d]
        else:
            yesterday = date.today() - timedelta(days=1)
            end_d = date.fromisoformat(opts['end']) if opts['end'] else yesterday
            if opts['start']:
                start_d = date.fromisoformat(opts['start'])
            else:
                start_d = end_d - timedelta(days=opts['days'] - 1)
            if start_d > end_d:
                self.stderr.write(self.style.ERROR(
                    f'Empty window: start={start_d} > end={end_d}'))
                return
            window = []
            cur = start_d
            while cur <= end_d:
                window.append(cur)
                cur += timedelta(days=1)

        # T-0 guard — today's HOURLY data is incomplete; never ingest it
        today = date.today()
        if any(d >= today for d in window):
            self.stderr.write(self.style.WARNING(
                'Skipping T-0 (today) — HOURLY data is only valid up to T-1.'))
            window = [d for d in window if d < today]
        if not window:
            return

        # Resume mode requires a single date
        resume_id = opts['resume_report_id']
        if resume_id and len(window) != 1:
            self.stderr.write(self.style.ERROR(
                '--resume-report-id requires exactly one --date.'))
            return

        client = AdsAPIClient(cfg)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n🕐  SP HOURLY backfill | {mp.upper()} | '
            f'{window[0]} → {window[-1]}  ({len(window)} day(s))'
        ))

        for d in window:
            self._process_day(client, cfg, mp, d, resume_id=resume_id,
                              force=opts['force'], dry_run=opts['dry_run'])

        self.stdout.write(self.style.SUCCESS('\n✅  SP hourly backfill complete.\n'))

    # ─────────────────────────────────────────────────────────────────────
    def _process_day(self, client, cfg, mp: str, d: date,
                     resume_id: str | None,
                     force: bool, dry_run: bool):
        from apps.dashboard.models import AdsDataSyncLog
        from apps.dashboard.completeness import log_sync, log_sync_pending

        self.stdout.write(self.style.MIGRATE_LABEL(f'\n  • {d}'))

        # ── Skip if already successful (unless --force) ─────────────────
        if not force and not resume_id:
            existing = AdsDataSyncLog.objects.filter(
                marketplace=mp, date=d, source='sp_hourly').first()
            if existing and existing.is_successful:
                self.stdout.write(
                    f'    ↳ already successful ({existing.status}, '
                    f'rows={existing.rows_received}) — skipping. Use --force to redo.')
                return

        # ── Submit / resume Amazon report ────────────────────────────────
        try:
            if resume_id:
                self.stdout.write(f'    Resuming report {resume_id}…')
                result = client.submit_sp_hourly_campaigns_report(
                    d, end_date=d, existing_report_id=resume_id)
            else:
                self.stdout.write('    Submitting SP HOURLY report…')
                result = client.submit_sp_hourly_campaigns_report(d, end_date=d)
        except Exception as e:
            err = f'{type(e).__name__}: {e}'
            self.stderr.write(self.style.ERROR(f'    ✗ submit failed: {err}'))
            if not dry_run:
                log_sync(mp, d, 'sp_hourly', 'failed', error_message=err)
            return

        status     = result.get('status')
        report_id  = result.get('report_id') or ''
        rows       = result.get('rows', [])

        # ── Handle non-OK terminal states ────────────────────────────────
        if status == 'pending':
            self.stdout.write(self.style.WARNING(
                f'    ⏳ still PENDING (report_id={report_id}). Re-run with '
                f'--date {d} --resume-report-id {report_id}'))
            if not dry_run:
                log_sync_pending(mp, d, 'sp_hourly', report_id=report_id)
            return
        if status == 'error':
            err = result.get('error', 'unknown')
            self.stderr.write(self.style.ERROR(
                f'    ✗ report failed: {err}'))
            if not dry_run:
                log_sync(mp, d, 'sp_hourly', 'failed',
                         error_message=str(err), report_id=report_id)
            return
        if status != 'ok':
            self.stderr.write(self.style.ERROR(
                f'    ✗ unexpected status from Ads API: {status!r}'))
            if not dry_run:
                log_sync(mp, d, 'sp_hourly', 'failed',
                         error_message=f'unexpected status {status!r}',
                         report_id=report_id)
            return

        # ── status == 'ok' ──────────────────────────────────────────────
        if not rows:
            # Legitimate zero (account paused, no spend that day)
            self.stdout.write(self.style.WARNING(
                '    ⚠ Amazon returned 0 rows — treating as legitimate zero spend.'))
            if not dry_run:
                log_sync(mp, d, 'sp_hourly', 'empty_from_amazon',
                         rows_received=0, report_id=report_id)
            return

        # ── Parse + persist ─────────────────────────────────────────────
        try:
            parsed, skipped = self._build_snapshot_rows(rows, mp, d)
        except Exception as e:
            err = f'parse error: {type(e).__name__}: {e}'
            self.stderr.write(self.style.ERROR(f'    ✗ {err}'))
            if not dry_run:
                log_sync(mp, d, 'sp_hourly', 'failed',
                         error_message=err, report_id=report_id)
            return

        if not parsed:
            self.stderr.write(self.style.WARNING(
                f'    ⚠ all {len(rows)} rows were unparseable (skipped={skipped})'))
            if not dry_run:
                log_sync(mp, d, 'sp_hourly', 'failed',
                         error_message=f'no rows parseable (raw={len(rows)})',
                         report_id=report_id)
            return

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'    (dry-run) parsed {len(parsed)} rows '
                f'({skipped} skipped), report_id={report_id}'))
            return

        with transaction.atomic():
            self._save_snapshots(parsed, mp, d)
            log_sync(mp, d, 'sp_hourly', 'ok',
                     rows_received=len(parsed), report_id=report_id)

        self.stdout.write(self.style.SUCCESS(
            f'    ✓ saved {len(parsed)} (campaign,hour) snapshots '
            f'({skipped} rows skipped)  report_id={report_id}'))

    # ─────────────────────────────────────────────────────────────────────
    def _build_snapshot_rows(self, raw_rows: list[dict], mp: str, expected_date: date):
        """
        Map raw Amazon rows → list of (campaign_id, hour, fields_dict).
        Aggregates by (date, hour, campaign_id) in case Amazon returns
        multiple rows per bucket (it shouldn't, but be defensive).
        """
        agg: dict[tuple[date, int, str], dict] = {}
        skipped = 0

        for r in raw_rows:
            d, h = _parse_date_and_hour(r)
            if d is None or h is None:
                skipped += 1
                continue
            # Hourly report should only return rows for the requested day.
            # If Amazon ever returns an off-by-tz row, keep it bound to expected_date.
            if d != expected_date:
                skipped += 1
                continue
            camp_id = str(r.get('campaignId') or '').strip()
            if not camp_id:
                skipped += 1
                continue
            key = (d, h, camp_id)
            if key not in agg:
                agg[key] = {
                    'campaign_name': (r.get('campaignName') or '')[:256],
                    'spend':         Decimal('0'),
                    'impressions':   0,
                    'clicks':        0,
                    'orders_7d':     0,
                    'sales_7d':      Decimal('0'),
                    'units_7d':      0,
                }
            row = agg[key]
            row['spend']       += Decimal(str(r.get('cost') or 0))
            row['impressions'] += int(r.get('impressions') or 0)
            row['clicks']      += int(r.get('clicks') or 0)
            # HOURLY reports use 1-day attribution (the 7-day window doesn't
            # fit inside an hour). The column names on the snapshot model are
            # still *_7d for backward compatibility — the values here are 1d.
            row['orders_7d']   += int(r.get('purchases1d') or r.get('purchases7d') or 0)
            row['sales_7d']    += Decimal(str(r.get('sales1d') or r.get('sales7d') or 0))
            row['units_7d']    += int(r.get('unitsSoldClicks1d') or r.get('unitsSoldClicks7d') or 0)
            # Keep first non-empty campaign_name we see
            if not row['campaign_name'] and r.get('campaignName'):
                row['campaign_name'] = r['campaignName'][:256]

        return agg, skipped

    def _save_snapshots(self, agg: dict, mp: str, d: date):
        from apps.dashboard.models import PPCCampaignHourlySnapshot

        objs = [
            PPCCampaignHourlySnapshot(
                marketplace   = mp,
                date          = snap_date,
                hour          = hour,
                campaign_id   = camp_id,
                campaign_name = row['campaign_name'],
                campaign_type = 'sp',
                spend         = row['spend'],
                impressions   = row['impressions'],
                clicks        = row['clicks'],
                orders_7d     = row['orders_7d'],
                sales_7d      = row['sales_7d'],
                units_7d      = row['units_7d'],
            )
            for (snap_date, hour, camp_id), row in agg.items()
        ]
        if not objs:
            return
        PPCCampaignHourlySnapshot.objects.bulk_create(
            objs,
            update_conflicts=True,
            update_fields=[
                'campaign_name', 'campaign_type',
                'spend', 'impressions', 'clicks',
                'orders_7d', 'sales_7d', 'units_7d',
            ],
            unique_fields=['marketplace', 'date', 'hour', 'campaign_id'],
        )
