"""
ingest_brand_analytics — Phase 3 ingestion for Amazon Brand Analytics reports.

Submits and/or resume-polls one of:
  • Search Query Performance (SQP)        → BASearchQueryWeekly
  • Item Comparison                       → BAItemComparisonWeekly
  • Market Basket                         → BAMarketBasketWeekly

Per-ASIN scope is mandatory — Amazon retired the brand-aggregate variant of
each of these reports. The command submits one report per (week × ASIN);
state per (marketplace, week_start, source, asin) is tracked in
AdsDataSyncLog with the same crash-safe semantics as the Ads detail reports.

The list of ASINs to ingest defaults to "all ASINs that sold any unit in the
last 30 days" — submitting reports for SKUs with zero sales burns API quota
and yields empty payloads. Override with `--asins` to scope a manual list.

Usage:
    # Default — last completed week, all marketplaces, all 3 BA reports, top-sellers
    python manage.py ingest_brand_analytics

    # Specific kind only
    python manage.py ingest_brand_analytics --kinds ba_search_query

    # Specific week (must be Sun-Sat)
    python manage.py ingest_brand_analytics --week-start 2026-05-31

    # Specific ASINs (skip the auto-discovery)
    python manage.py ingest_brand_analytics --asins B09ZY2D42V,B07XYZ

    # Rewind: include the last N completed weeks
    python manage.py ingest_brand_analytics --rewind 4
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

logger = logging.getLogger(__name__)


class _BAQuotaStopSweep(Exception):
    """Internal signal — bucket exhausted, abort the rest of this sweep."""


_KIND_TO_MODEL_AND_NORMALIZER = {
    'ba_search_query':     ('BASearchQueryWeekly',     'normalize_sqp_row'),
    'ba_market_basket':    ('BAMarketBasketWeekly',    'normalize_market_basket_row'),
    'ba_repeat_purchase':  ('BARepeatPurchaseWeekly',  'normalize_repeat_purchase_row'),
}

_UPSERT_CONFIG = {
    'BASearchQueryWeekly': dict(
        unique_fields=['marketplace', 'week_start', 'asin', 'search_query_hash'],
        update_fields=['week_end', 'search_query', 'search_query_score',
                       'search_query_volume',
                       'impressions_total', 'impressions_asin_count',
                       'clicks_total', 'cart_adds_total', 'purchases_total',
                       'asin_impression_count', 'asin_click_count',
                       'asin_cart_add_count', 'asin_purchase_count',
                       'brand_impressions_share', 'brand_click_share',
                       'brand_cart_add_share', 'brand_purchase_share',
                       'top_clicked_asins', 'top_converted_asins',
                       'top_purchased_asins'],
    ),
    'BAMarketBasketWeekly': dict(
        unique_fields=['marketplace', 'week_start', 'asin', 'purchased_asin'],
        update_fields=['week_end', 'purchased_title', 'purchased_frequency_rank',
                       'combination_pct'],
    ),
    'BARepeatPurchaseWeekly': dict(
        unique_fields=['marketplace', 'week_start', 'asin'],
        update_fields=['week_end', 'orders', 'unique_customers',
                       'repeat_customers_pct', 'repeat_purchase_revenue',
                       'repeat_purchase_revenue_pct'],
    ),
}


class Command(BaseCommand):
    help = ('Submit / resume / download Brand Analytics weekly reports '
            '(SQP, Item Comparison, Market Basket) per ASIN.')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; default: all active.')
        parser.add_argument('--week-start', default=None,
                            help='YYYY-MM-DD (must be Sunday). Default: last completed Sun-Sat.')
        parser.add_argument('--rewind', type=int, default=0,
                            help='Include the prior N completed weeks too (default 0).')
        parser.add_argument('--kinds', default=None,
                            help='Comma-separated kinds: ba_search_query,ba_item_comparison,ba_market_basket. Default: all.')
        parser.add_argument('--asins', default=None,
                            help='Comma-separated ASINs to scope to. Default: auto-discover top sellers.')
        parser.add_argument('--top-asins', type=int, default=50,
                            help='When auto-discovering, max ASINs to ingest. Default 50.')
        parser.add_argument('--min-units-30d', type=int, default=1,
                            help='Auto-discovery cutoff: min units sold in last 30d (default 1).')
        parser.add_argument('--submit-spacing', type=float, default=65.0,
                            help='Seconds between submits. Amazon BA endpoints document '
                                 '0.0167 req/sec ≈ 1/min — default 65s respects that.')

        parser.add_argument('--max-reports-per-run', type=int, default=0,
                            help='Stop the sweep after this many createReport submits '
                                 'this run (0 = unlimited). Lets you cap a backfill at '
                                 'e.g. 30 reports/run when running on a cron without '
                                 'risking quota exhaustion.')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import SPAPIClient
        from apps.amazon_api.ba_reports import (
            BA_REPORT_CONFIGS, last_completed_sun_sat_week, n_completed_weeks,
        )
        from apps.dashboard import models as dm
        from apps.dashboard.models import AdsDataSyncLog, DailySkuSnapshot
        from apps.dashboard.completeness import log_sync
        from django.db.models import Sum

        # ── 1. Marketplaces ─────────────────────────────────────────────────
        if opts['marketplace']:
            mps = [opts['marketplace']]
        else:
            mps = list(AmazonAPIConfig.objects.filter(is_active=True)
                       .values_list('marketplace', flat=True))
        if not mps:
            self.stderr.write(self.style.WARNING('No active marketplaces.'))
            return

        # ── 2. Report kinds ─────────────────────────────────────────────────
        if opts['kinds']:
            kinds = [k.strip() for k in opts['kinds'].split(',')
                     if k.strip() in BA_REPORT_CONFIGS]
        else:
            kinds = list(BA_REPORT_CONFIGS.keys())
        if not kinds:
            self.stderr.write(self.style.ERROR('No valid kinds requested.'))
            return

        for mp in mps:
            cfg = AmazonAPIConfig.objects.filter(marketplace=mp, is_active=True).first()
            if not cfg:
                continue

            tz = ZoneInfo(settings.AMAZON_MARKETPLACES.get(mp, {})
                          .get('timezone', settings.TIME_ZONE))
            today = datetime.now(tz=tz).date()

            # ── 3. Resolve weeks ────────────────────────────────────────────
            if opts['week_start']:
                ws = date.fromisoformat(opts['week_start'])
                weeks = [(ws, ws + timedelta(days=6))]
                rewind = max(0, opts['rewind'])
                for i in range(1, rewind + 1):
                    prev_sun = ws - timedelta(days=7 * i)
                    weeks.append((prev_sun, prev_sun + timedelta(days=6)))
            else:
                weeks = n_completed_weeks(today, max(1, opts['rewind'] + 1))

            # ── 4. Resolve ASIN list ────────────────────────────────────────
            if opts['asins']:
                asins = [a.strip() for a in opts['asins'].split(',') if a.strip()]
            else:
                # Auto-discover: top ASINs by units sold in last 30 days.
                cutoff = today - timedelta(days=30)
                qs = (DailySkuSnapshot.objects.filter(
                          marketplace=mp, date__gte=cutoff,
                          asin__regex=r'^B[0-9A-Z]{9}$',  # Looks like a real ASIN
                      ).values('asin').annotate(units=Sum('qty'))
                       .filter(units__gte=opts['min_units_30d'])
                       .order_by('-units'))[: opts['top_asins']]
                asins = [r['asin'] for r in qs]

            if not asins:
                self.stderr.write(self.style.WARNING(
                    f'  [{mp}] no ASINs to ingest; skipping.'))
                continue

            # Per-kind: figure out how many submissions we actually plan.
            # Per-ASIN kinds: weeks × ASINs reports.
            # Brand-level kinds (per_asin=False): weeks reports (one per week).
            n_reports = 0
            for k in kinds:
                if BA_REPORT_CONFIGS[k].get('per_asin', True):
                    n_reports += len(weeks) * len(asins)
                else:
                    n_reports += len(weeks)

            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n📊  [{mp.upper()}] ingesting {len(kinds)} BA kind(s) × '
                f'{len(weeks)} week(s) → {n_reports} report(s) total'))

            client = SPAPIClient(cfg)
            spacing = max(0.0, opts['submit_spacing'])

            sweep_stopped = False
            max_per_run  = max(0, opts['max_reports_per_run'])
            state = {'submits_this_run': 0}

            for sun, sat in weeks:
                if sweep_stopped: break
                for kind in kinds:
                    if sweep_stopped: break
                    # Brand-level kinds get ONE submit per week with asin=''.
                    # Per-ASIN kinds iterate the ASIN list.
                    asin_iter = (asins if BA_REPORT_CONFIGS[kind].get('per_asin', True)
                                  else [''])
                    for asin in asin_iter:
                        if max_per_run and state['submits_this_run'] >= max_per_run:
                            self.stdout.write(self.style.WARNING(
                                f'\n🛑  Hit --max-reports-per-run={max_per_run}. '
                                'Stopping. Remaining slots stay in their current '
                                'state and the next run picks them up.\n'))
                            sweep_stopped = True
                            break
                        try:
                            self._process_one(
                                client=client, mp=mp, kind=kind, asin=asin,
                                week_start=sun, week_end=sat,
                                BA_REPORT_CONFIGS=BA_REPORT_CONFIGS,
                                dm=dm, AdsDataSyncLog=AdsDataSyncLog,
                                log_sync=log_sync, submit_spacing=spacing,
                                state=state,
                            )
                        except _BAQuotaStopSweep:
                            self.stdout.write(self.style.WARNING(
                                '\n🚦  Quota exhausted — stopping this sweep. '
                                'Remaining slots stay in their current state '
                                'and the next run picks them up.\n'))
                            sweep_stopped = True
                            break

            self.stdout.write(self.style.HTTP_INFO(
                f'  Submits attempted this run: {state["submits_this_run"]}'))

        self.stdout.write(self.style.SUCCESS('\n✅  BA ingestion sweep complete.\n'))

    # ── per-(mp × week × kind × ASIN) processor ─────────────────────────────
    def _process_one(self, *, client, mp, kind, asin, week_start, week_end,
                     BA_REPORT_CONFIGS, dm, AdsDataSyncLog, log_sync,
                     submit_spacing, state=None):
        from apps.amazon_api import ba_reports as _br

        cfg = BA_REPORT_CONFIGS[kind]
        sync_source = cfg['sync_source']

        # Skip if already settled
        existing = AdsDataSyncLog.objects.filter(
            marketplace=mp, date=week_start, source=sync_source, asin=asin,
        ).first()
        if existing and existing.status in ('ok', 'empty_from_amazon'):
            return

        existing_rid = (existing.report_id
                        if (existing and existing.status == 'pending')
                        else None)

        # Resume or submit
        if existing_rid:
            try:
                meta = client.get_report_status(existing_rid)
            except Exception as e:
                log_sync(mp, week_start, sync_source, 'failed',
                         error_message=f'status check failed: {e}'[:512],
                         report_id=existing_rid, asin=asin)
                return
            status = (meta.get('processingStatus') or '').upper()
        else:
            if submit_spacing:
                time.sleep(submit_spacing)
            from apps.amazon_api.services import SPAPIClient
            # Count this against --max-reports-per-run BEFORE the network call,
            # so a failed createReport (it still hit Amazon, still consumed an
            # attempt) is counted just like a successful one.
            if state is not None:
                state['submits_this_run'] = state.get('submits_this_run', 0) + 1
            # Brand-level reports must pass asin=None (sending '' makes Amazon
            # reject the request); per-ASIN reports send the actual ASIN.
            submit_asin = asin if cfg.get('per_asin', True) else None
            try:
                rid = client.submit_ba_report(
                    report_type=cfg['report_type'],
                    period_start=str(week_start), period_end=str(week_end),
                    period_type='WEEK', asin=submit_asin,
                )
            except SPAPIClient.BAQuotaExceeded as e:
                # Treat as "retry on the next sweep" — don't crash the run.
                self.stdout.write(self.style.WARNING(
                    f'  🚦 [{mp}] {week_start} {kind} {asin}: quota — defer to next sweep'))
                log_sync(mp, week_start, sync_source, 'pending',
                         error_message=str(e)[:512], asin=asin)
                # No bucket left — back off the rest of the sweep for ALL submits.
                # Without this we'd keep banging on 429s for every remaining ASIN.
                # The cron will pick up the deferred slots on the next run.
                raise _BAQuotaStopSweep()
            except Exception as e:
                err = f'{type(e).__name__}: {e}'
                self.stderr.write(self.style.ERROR(
                    f'  ✗ [{mp}] {week_start} {kind} {asin}: {err[:120]}'))
                log_sync(mp, week_start, sync_source, 'failed',
                         error_message=err[:512], asin=asin)
                return
            log_sync(mp, week_start, sync_source, 'pending',
                     report_id=rid, asin=asin)
            self.stdout.write(self.style.HTTP_INFO(
                f'  ⏳ [{mp}] {week_start} {kind} {asin} → pending (rid={rid[:8]})'))
            # Short-poll once to drain quick wins
            time.sleep(5)
            try:
                meta = client.get_report_status(rid)
            except Exception:
                return
            status = (meta.get('processingStatus') or '').upper()
            existing_rid = rid

        if status == 'IN_QUEUE' or status == 'IN_PROGRESS':
            return  # still pending; next sweep picks it up
        if status == 'CANCELLED':
            log_sync(mp, week_start, sync_source, 'failed',
                     error_message='cancelled by Amazon', report_id=existing_rid, asin=asin)
            return
        if status == 'FATAL':
            # FATAL reports STILL write a documentId with errorDetails JSON
            doc_id = meta.get('reportDocumentId')
            err_msg = 'FATAL (no document)'
            if doc_id:
                try:
                    body = client.download_ba_report(doc_id)
                    if isinstance(body, dict) and body.get('errorDetails'):
                        err_msg = f'FATAL: {body["errorDetails"]}'
                except Exception as e:
                    err_msg = f'FATAL (download err: {e})'
            log_sync(mp, week_start, sync_source, 'failed',
                     error_message=err_msg[:512], report_id=existing_rid, asin=asin)
            self.stderr.write(self.style.ERROR(
                f'  ✗ [{mp}] {week_start} {kind} {asin}: {err_msg[:120]}'))
            return

        # status == 'DONE'
        doc_id = meta.get('reportDocumentId')
        if not doc_id:
            log_sync(mp, week_start, sync_source, 'failed',
                     error_message='DONE but no reportDocumentId', report_id=existing_rid, asin=asin)
            return
        try:
            data = client.download_ba_report(doc_id)
        except Exception as e:
            log_sync(mp, week_start, sync_source, 'failed',
                     error_message=f'download failed: {e}'[:512],
                     report_id=existing_rid, asin=asin)
            return
        if isinstance(data, dict) and data.get('errorDetails'):
            log_sync(mp, week_start, sync_source, 'failed',
                     error_message=f'errorDetails: {data["errorDetails"]}'[:512],
                     report_id=existing_rid, asin=asin)
            return

        # ── Find rows ───────────────────────────────────────────────────────
        rows = []
        for key in cfg['data_keys']:
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        if not rows:
            log_sync(mp, week_start, sync_source, 'empty_from_amazon',
                     rows_received=0, report_id=existing_rid, asin=asin)
            self.stdout.write(self.style.WARNING(
                f'  ⚠  [{mp}] {week_start} {kind} {asin} → 0 rows (empty)'))
            return

        # ── Normalize + upsert ──────────────────────────────────────────────
        model_name, norm_fn_name = _KIND_TO_MODEL_AND_NORMALIZER[kind]
        Model = getattr(dm, model_name)
        norm_fn = getattr(_br, norm_fn_name)
        upsert_cfg = _UPSERT_CONFIG[model_name]

        instances = []
        for raw in rows:
            try:
                fields = norm_fn(raw, mp, asin, week_start, week_end)
            except Exception as ex:
                logger.exception('normalize failed: %s', ex)
                continue
            # Decimal coercion for any DecimalField fields
            for k in ('brand_impressions_share', 'brand_click_share',
                       'brand_cart_add_share', 'brand_purchase_share'):
                if k in fields:
                    fields[k] = Decimal(str(fields[k]))
            instances.append(Model(**fields))

        if not instances:
            log_sync(mp, week_start, sync_source, 'empty_from_amazon',
                     rows_received=0, report_id=existing_rid, asin=asin)
            return

        with transaction.atomic():
            Model.objects.bulk_create(
                instances,
                batch_size=1000,
                update_conflicts=True,
                unique_fields=upsert_cfg['unique_fields'],
                update_fields=upsert_cfg['update_fields'],
            )
            log_sync(mp, week_start, sync_source, 'ok',
                     rows_received=len(instances), report_id=existing_rid, asin=asin)

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ [{mp}] {week_start} {kind} {asin} → {len(instances)} rows'))
