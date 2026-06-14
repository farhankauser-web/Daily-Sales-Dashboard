"""
ingest_ads_detail_reports — Phase 1 ingestion for the Amazon Ads detail reports.

Submits and/or resume-polls one or more report_kinds (search-term, targeting,
advertised-product, placement, ad-group across SP/SB/SD) for the given date(s)
and upserts the rows into the corresponding `Ads*DailySnapshot` table.

State machine (per marketplace × date × report_kind, recorded in AdsDataSyncLog):

    none              → SUBMIT  → 'pending' (saves Amazon's report_id)
    'pending' (w/ id) → RESUME  → 'ok' / 'pending' / 'failed'
    'ok'              → SKIP
    'failed'          → SUBMIT  (treat like none)
    'empty_from_amazon' → SKIP   (Amazon returned 0 rows; we know the answer)

The command is fully idempotent — safe to call from cron every 15 min.
Each call either submits new work or resumes pending work, never both for the
same (marketplace, date, kind).

Usage:
    # Default: yesterday, all active marketplaces, all report kinds
    python manage.py ingest_ads_detail_reports

    # Specific date, all kinds
    python manage.py ingest_ads_detail_reports --date 2026-06-10

    # Specific marketplaces and/or report kinds
    python manage.py ingest_ads_detail_reports --marketplace usa \\
        --report-kinds sp_search_term,sp_advertised_product

    # Late-attribution re-pull window — pull all kinds for the last 7 days
    python manage.py ingest_ads_detail_reports --rewind 7

Notes:
  - Submitting reports is rate-limited by Amazon (~1/sec). The command sleeps
    1 s between submissions to stay polite.
  - SD is silently skipped for search-term and placement (Amazon does not
    publish those report types for SD).
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
from django.utils import timezone

logger = logging.getLogger(__name__)


# Mapping from report_kind → model class (resolved lazily inside handle()).
def _kind_to_model() -> dict[str, str]:
    """report_kind → ('apps.dashboard.models', '<ModelName>') string pair so we
    can import lazily once Django apps are ready."""
    return {
        # adgroup kinds dropped — see ads_detail_reports.py (no spAdGroups
        # reportTypeId in Ads API v3). Aggregate at read time from
        # AdsAdvertisedProductDailySnapshot if ad-group rollup is ever needed.
        # targeting
        'sp_targeting': 'AdsTargetingDailySnapshot',
        'sb_targeting': 'AdsTargetingDailySnapshot',
        'sd_targeting': 'AdsTargetingDailySnapshot',
        # search-term
        'sp_search_term': 'AdsSearchTermDailySnapshot',
        'sb_search_term': 'AdsSearchTermDailySnapshot',
        # advertised-product
        'sp_advertised_product': 'AdsAdvertisedProductDailySnapshot',
        'sb_advertised_product': 'AdsAdvertisedProductDailySnapshot',
        'sd_advertised_product': 'AdsAdvertisedProductDailySnapshot',
        # placement
        'sp_placement': 'AdsPlacementDailySnapshot',
        'sb_placement': 'AdsPlacementDailySnapshot',
    }


# Per-model: which fields go in `unique_fields` and which go in `update_fields`
# for bulk_create's update_conflicts upsert path.
_UPSERT_CONFIG: dict[str, dict] = {
    'AdsAdGroupDailySnapshot': dict(
        unique_fields=['marketplace', 'date', 'source_ad_type',
                       'campaign_id', 'ad_group_id'],
        update_fields=['ad_group_name', 'impressions', 'clicks', 'spend',
                       'orders_7d', 'sales_7d', 'units_7d',
                       'acos', 'roas', 'ctr', 'cvr', 'cpc'],
    ),
    'AdsTargetingDailySnapshot': dict(
        unique_fields=['marketplace', 'date', 'source_ad_type',
                       'campaign_id', 'ad_group_id', 'target_id'],
        update_fields=['target_type', 'expression', 'match_type',
                       'impressions', 'clicks', 'spend',
                       'orders_7d', 'sales_7d', 'units_7d',
                       'acos', 'roas', 'ctr', 'cvr', 'cpc'],
    ),
    'AdsSearchTermDailySnapshot': dict(
        unique_fields=['marketplace', 'date', 'source_ad_type',
                       'campaign_id', 'ad_group_id', 'target_id',
                       'search_term_hash'],
        update_fields=['match_type', 'search_term',
                       'impressions', 'clicks', 'spend',
                       'orders_7d', 'sales_7d', 'units_7d',
                       'acos', 'roas', 'ctr', 'cvr', 'cpc'],
    ),
    'AdsAdvertisedProductDailySnapshot': dict(
        unique_fields=['marketplace', 'date', 'source_ad_type',
                       'campaign_id', 'asin', 'advertised_sku'],
        update_fields=['ad_group_id',
                       'impressions', 'clicks', 'spend',
                       'orders_7d', 'sales_7d', 'units_7d'],
    ),
    'AdsPlacementDailySnapshot': dict(
        unique_fields=['marketplace', 'date', 'source_ad_type',
                       'campaign_id', 'placement'],
        update_fields=['impressions', 'clicks', 'spend',
                       'orders_7d', 'sales_7d', 'units_7d',
                       'acos', 'roas'],
    ),
}


class Command(BaseCommand):
    help = ('Submit and/or resume-poll Amazon Ads detail reports '
            '(search-term, targeting, advertised-product, placement, ad-group) '
            'and upsert rows into the corresponding snapshot tables.')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; default: all active.')
        parser.add_argument('--date', default=None,
                            help='YYYY-MM-DD (single day, marketplace local TZ). '
                                 'Default: yesterday.')
        parser.add_argument('--rewind', type=int, default=0,
                            help='Pull --date back N days too '
                                 '(late-attribution re-pull). 0 = single day.')
        parser.add_argument('--report-kinds', default=None,
                            help='Comma-separated list. Default: all 13 kinds.')
        parser.add_argument('--submit-spacing', type=float, default=1.0,
                            help='Seconds to sleep between submissions (default 1.0).')

    # ── handle() ────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import AdsAPIClient
        from apps.amazon_api.ads_detail_reports import (
            REPORT_CONFIGS, REPORT_KIND_TO_SYNC_SOURCE, normalize_row,
        )
        from apps.dashboard import models as dm
        from apps.dashboard.completeness import log_sync
        from apps.dashboard.models import AdsDataSyncLog

        # ── Marketplaces ────────────────────────────────────────────────────
        if opts['marketplace']:
            mps = [opts['marketplace']]
        else:
            mps = list(AmazonAPIConfig.objects.filter(is_active=True)
                       .values_list('marketplace', flat=True))
        if not mps:
            self.stderr.write(self.style.WARNING('No active marketplaces.'))
            return

        # ── Report kinds ────────────────────────────────────────────────────
        if opts['report_kinds']:
            kinds = [k.strip() for k in opts['report_kinds'].split(',')
                     if k.strip() in REPORT_CONFIGS]
            if not kinds:
                self.stderr.write(self.style.ERROR(
                    f"None of {opts['report_kinds']!r} are valid report kinds."))
                return
        else:
            kinds = list(REPORT_CONFIGS.keys())

        # ── Date range ──────────────────────────────────────────────────────
        anchor_str = opts['date']
        rewind     = max(0, opts['rewind'])
        submit_spacing = max(0.0, opts['submit_spacing'])

        kind_to_model = _kind_to_model()

        for mp in mps:
            cfg = AmazonAPIConfig.objects.filter(marketplace=mp, is_active=True).first()
            if not cfg or not cfg.has_ads_credentials():
                self.stderr.write(self.style.WARNING(
                    f'  [{mp}] no Ads credentials — skipping.'))
                continue

            tz   = ZoneInfo(settings.AMAZON_MARKETPLACES.get(mp, {})
                            .get('timezone', settings.TIME_ZONE))
            if anchor_str:
                anchor = date.fromisoformat(anchor_str)
            else:
                anchor = (datetime.now(tz=tz).date() - timedelta(days=1))

            dates = [anchor - timedelta(days=i) for i in range(rewind + 1)]

            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n📡  [{mp.upper()}] ingesting {len(kinds)} report kind(s) '
                f'across {len(dates)} day(s)'
            ))

            client = AdsAPIClient(cfg)

            for d in sorted(dates):
                for kind in kinds:
                    src = REPORT_KIND_TO_SYNC_SOURCE[kind]
                    self._process_one(
                        client      = client,
                        mp          = mp,
                        d           = d,
                        report_kind = kind,
                        sync_source = src,
                        model_name  = kind_to_model[kind],
                        normalize   = normalize_row,
                        dm          = dm,
                        log_sync    = log_sync,
                        AdsDataSyncLog = AdsDataSyncLog,
                        submit_spacing = submit_spacing,
                    )

        self.stdout.write(self.style.SUCCESS('\n✅  Detail-report ingestion sweep complete.\n'))

    # ── per-(mp, date, kind) processor ──────────────────────────────────────
    def _process_one(self, *, client, mp, d, report_kind, sync_source,
                     model_name, normalize, dm, log_sync, AdsDataSyncLog,
                     submit_spacing):
        # Skip if already done
        existing = AdsDataSyncLog.objects.filter(
            marketplace=mp, date=d, source=sync_source).first()
        if existing and existing.status in ('ok', 'empty_from_amazon'):
            return

        existing_report_id = (existing.report_id
                              if (existing and existing.status == 'pending')
                              else None)

        try:
            if existing_report_id:
                # RESUME — Amazon dedup keeps this fast.
                result = client.submit_detail_report(
                    report_kind, start_date=d, end_date=d,
                    existing_report_id=existing_report_id,
                )
            else:
                # SUBMIT new
                if submit_spacing:
                    time.sleep(submit_spacing)
                result = client.submit_detail_report(
                    report_kind, start_date=d, end_date=d,
                )
        except Exception as e:
            err = f'{type(e).__name__}: {e}'
            self.stderr.write(self.style.ERROR(
                f'  ✗ [{mp}] {d} {report_kind}: {err}'))
            log_sync(mp, d, sync_source, 'failed', error_message=err[:512])
            return

        status = result.get('status', '?')
        rid    = result.get('report_id') or ''

        if status == 'pending':
            log_sync(mp, d, sync_source, 'pending', report_id=rid)
            self.stdout.write(self.style.HTTP_INFO(
                f'  ⏳ [{mp}] {d} {report_kind} → pending (report_id={rid[:8]})'))
            return

        if status == 'error':
            err = result.get('error', 'unknown')
            log_sync(mp, d, sync_source, 'failed',
                     error_message=str(err)[:512], report_id=rid)
            self.stderr.write(self.style.ERROR(
                f'  ✗ [{mp}] {d} {report_kind} → failed: {err}'))
            return

        # status == 'ok'
        rows = result.get('rows') or []
        if not rows:
            log_sync(mp, d, sync_source, 'empty_from_amazon',
                     rows_received=0, report_id=rid)
            self.stdout.write(self.style.WARNING(
                f'  ⚠  [{mp}] {d} {report_kind} → 0 rows (Amazon returned empty)'))
            return

        Model = getattr(dm, model_name)
        upsert_cfg = _UPSERT_CONFIG[model_name]

        instances = []
        date_str = str(d)
        for raw in rows:
            try:
                fields = normalize(report_kind, raw, mp, date_str)
            except Exception as ex:
                logger.exception('normalize_row failed: %s', ex)
                continue
            # Convert decimals to Decimal for DecimalField columns
            for k in ('spend', 'sales_7d'):
                if k in fields:
                    fields[k] = Decimal(str(fields[k]))
            for k in ('ctr', 'cvr', 'cpc', 'acos', 'roas'):
                if k in fields:
                    fields[k] = Decimal(str(fields[k]))
            instances.append(Model(**fields))

        if not instances:
            log_sync(mp, d, sync_source, 'empty_from_amazon',
                     rows_received=0, report_id=rid)
            return

        with transaction.atomic():
            # bulk_create with update_conflicts upsert.
            Model.objects.bulk_create(
                instances,
                batch_size=1000,
                update_conflicts=True,
                unique_fields=upsert_cfg['unique_fields'],
                update_fields=upsert_cfg['update_fields'],
            )
            log_sync(mp, d, sync_source, 'ok',
                     rows_received=len(instances), report_id=rid)

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ [{mp}] {d} {report_kind} → {len(instances)} rows'))
