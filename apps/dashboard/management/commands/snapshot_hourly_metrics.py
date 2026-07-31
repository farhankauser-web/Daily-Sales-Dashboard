"""
snapshot_hourly_metrics — Hourly cron job.

Pulls today's FlatFileAllOrdersReport for every active marketplace,
buckets the orders into 24 per-hour rows in marketplace local TZ,
and upserts into HourlyMetricSnapshot + HourlySkuSnapshot.

Idempotent: running it twice in the same hour just overwrites the
hour's bucket with the latest cumulative state from the report.

Usage:
    python manage.py snapshot_hourly_metrics
    python manage.py snapshot_hourly_metrics --marketplace usa
    python manage.py snapshot_hourly_metrics --date 2026-06-28
    python manage.py snapshot_hourly_metrics --dry-run
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.dashboard.sync import (
    configured_marketplaces, finalize_yesterday, is_finalized, sync_hourly_window,
)
from apps.dashboard.completeness import log_sync


# Map sync_hourly_window status → AdsDataSyncLog status
def _map_sync_status(res: dict) -> str:
    s    = res.get('status', '')
    rows = int(res.get('rows', 0) or 0)
    # finalize_day() — reached via the yesterday catch-up — reports success as
    # 'finalized' (just locked it) or 'already_finalized' (someone else did).
    # Both mean the day's order data is written and frozen; it reports genuine
    # failure as 'sync_failed:<status>'. Neither success value carries a 'rows'
    # count, so don't judge them on row count. Missing these here logged a
    # perfectly good day as failed, and the Hourly Patterns completeness gate
    # then hid it from aggregates as "core missing (Orders)".
    if s in ('finalized', 'already_finalized'):
        return 'ok'
    if s in ('OK', 'FRESH', 'CACHED'):
        return 'ok' if rows > 0 else 'empty_from_amazon'
    return 'failed'


class Command(BaseCommand):
    help = "Snapshot one day's per-hour metrics for every active marketplace."

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', help='Single marketplace; defaults to all active')
        parser.add_argument('--date',
                            help='YYYY-MM-DD (defaults to today in marketplace TZ)')
        parser.add_argument('--max-wait', type=int, default=90,
                            help='Max seconds to wait for SP-API report (default 90)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would run without calling SP-API')

    def handle(self, *args, **opts):
        mps = [opts['marketplace']] if opts['marketplace'] else configured_marketplaces()
        if not mps:
            self.stdout.write(self.style.WARNING('No active SP-API configurations.'))
            return

        for mp in mps:
            tz_name = settings.AMAZON_MARKETPLACES.get(mp, {}).get('timezone', settings.TIME_ZONE)
            today = datetime.now(tz=ZoneInfo(tz_name)).date()
            day = (datetime.strptime(opts['date'], '%Y-%m-%d').date()
                   if opts['date'] else today)

            if opts['dry_run']:
                self.stdout.write(f'[{mp}] DRY-RUN — would snapshot {day} (TZ={tz_name})')
                continue

            # ── Catch-up: finalize yesterday if the 00:45 cron missed ────────
            # This kicks in when --date is not supplied (i.e. normal hourly run).
            if not opts['date']:
                from datetime import timedelta as _td
                yest = today - _td(days=1)
                if not is_finalized(mp, yest):
                    self.stdout.write(self.style.NOTICE(
                        f'[{mp}] yesterday ({yest}) not finalized — running catch-up …'
                    ))
                    fres = finalize_yesterday(mp, today=today,
                                              max_wait_seconds=opts['max_wait'])
                    self.stdout.write(self.style.NOTICE(
                        f'[{mp}] catch-up {fres["date"]}  status={fres["status"]}  '
                        f'sku_rows={fres.get("sku_rows_written", 0)}'
                    ))
                    # Log completeness for yesterday's orders (T-1).
                    try:
                        log_sync(mp, yest, 'orders', _map_sync_status(fres),
                                 rows_received=int(fres.get('rows', 0) or 0),
                                 error_message=str(fres.get('error', '') or ''))
                    except Exception as e:
                        self.stderr.write(self.style.WARNING(
                            f'[{mp}] AdsDataSyncLog write (orders/{yest}) failed: {e}'))

            self.stdout.write(f'[{mp}] snapshotting {day} ({tz_name}) …')
            res = sync_hourly_window(mp, day, max_wait_seconds=opts['max_wait'])
            ok = res['status'] in ('OK', 'CACHED', 'FRESH')
            style = self.style.SUCCESS if ok else self.style.WARNING
            self.stdout.write(style(
                f'[{mp}] {day}  status={res["status"]}  '
                f'rows={res["rows"]}  metric_hours={res["metric_rows_written"]}  '
                f'sku_rows={res["sku_rows_written"]}  '
                + (f'hours_with_orders={res["hours_with_orders"]}'
                   if 'hours_with_orders' in res else '')
            ))

            # ── AdsDataSyncLog: only log past-days (T-0 is still in flight) ──
            if day < today:
                try:
                    log_sync(mp, day, 'orders', _map_sync_status(res),
                             rows_received=int(res.get('rows', 0) or 0),
                             error_message=str(res.get('error', '') or ''))
                except Exception as e:
                    self.stderr.write(self.style.WARNING(
                        f'[{mp}] AdsDataSyncLog write (orders/{day}) failed: {e}'))
