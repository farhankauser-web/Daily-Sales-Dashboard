"""
apps/dashboard/manual_hourly_importer.py — Wraps the parser with DB writes.

Shared by `apps/dashboard/views.py::upload_manual_hourly` (UI) and
`management/commands/import_hourly_csv.py` (CLI). Both paths produce identical
audit rows, same sync-log entries, same upserts.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls
from decimal import Decimal

from django.db import transaction

from .completeness import log_sync
from .manual_hourly_parser import ParseResult, parse_hourly_csv

logger = logging.getLogger(__name__)


MAX_DAYS_PER_FILE = 14   # contract: max 14-day window per upload (GUI limit)


# Map UI ad_type → AdsDataSyncLog source
_SOURCE_PER_AD_TYPE = {
    'sp': 'sp_hourly',
    'sb': 'sb_daily',
    'sd': 'sd_daily',
}


class ImportError(Exception):
    """Raised for validation failures (bad ad type, file too wide, etc.)."""


def _norm_name(name: str) -> str:
    """Normalize a campaign name for cross-source matching."""
    if not name:
        return ''
    # Collapse whitespace, lowercase. Don't touch punctuation — it carries
    # meaning in Amazon campaign names (e.g. "(5AM-3PM PDT)" vs "5AM-3PM PDT").
    return ' '.join(name.lower().split())


def _build_name_to_id_map(marketplace: str, parsed_rows) -> dict[str, str]:
    """
    For each unique campaign_name in the parsed CSV, find the matching real
    numeric campaign_id by joining on `Campaign` (preferred, since it's the
    canonical dimension) and falling back to `PPCCampaignSnapshot`.
    """
    from .models import Campaign, PPCCampaignSnapshot

    wanted = {_norm_name(r.campaign_name) for r in parsed_rows if r.campaign_name}
    if not wanted:
        return {}

    mapping: dict[str, str] = {}
    # Campaign dim is small (~hundreds) — load all and match in Python
    for c in Campaign.objects.filter(marketplace=marketplace).values_list(
            'campaign_id', 'campaign_name'):
        key = _norm_name(c[1])
        if key in wanted and key not in mapping:
            mapping[key] = c[0]
    # Fill gaps from the (larger) PPCCampaignSnapshot rollup
    still_missing = wanted - set(mapping.keys())
    if still_missing:
        for cid, cname in PPCCampaignSnapshot.objects.filter(
                marketplace=marketplace,
            ).values_list('campaign_id', 'campaign_name').distinct():
            key = _norm_name(cname)
            if key in still_missing and key not in mapping:
                mapping[key] = cid
    return mapping


def import_hourly_csv_bytes(
    *,
    marketplace:       str,
    ad_type:           str,                       # 'sp' | 'sb' | 'sd'
    file_bytes:        bytes,
    original_filename: str,
    uploaded_by_user=None,
) -> dict:
    """
    Parse + upsert a manual hourly CSV.

    Returns:
        {
          'status':          'ok' | 'failed',
          'error':           str,                          # only when failed
          'parse_result':    ParseResult,                  # diagnostics for the UI
          'upload_audit_id': int,                          # AdsManualHourlyUpload.id
          'rows_imported':   int,
          'days_covered':    int,
          'date_range':      (date_min, date_max) | None,
        }
    """
    from .models import AdsManualHourlyUpload, PPCCampaignHourlySnapshot

    if ad_type not in _SOURCE_PER_AD_TYPE:
        raise ImportError(f'Unknown ad_type {ad_type!r}, expected sp|sb|sd')

    # ── Parse ─────────────────────────────────────────────────────────────
    parse_result = parse_hourly_csv(file_bytes, filename=original_filename)
    if parse_result.errors or not parse_result.rows:
        audit = AdsManualHourlyUpload.objects.create(
            marketplace       = marketplace,
            ad_type           = ad_type,
            uploaded_by       = uploaded_by_user,
            original_filename = original_filename[:256],
            rows_in_file      = parse_result.rows_in_file,
            rows_imported     = 0,
            days_covered      = parse_result.days_covered,
            date_range_start  = parse_result.date_min,
            date_range_end    = parse_result.date_max,
            status            = 'failed',
            error_message     = ' · '.join(parse_result.errors)
                                or 'No parseable rows in file.',
        )
        return {
            'status':          'failed',
            'error':           audit.error_message,
            'parse_result':    parse_result,
            'upload_audit_id': audit.id,
            'rows_imported':   0,
            'days_covered':    parse_result.days_covered,
            'date_range':      None,
        }

    # ── Validate the 14-day window ────────────────────────────────────────
    if parse_result.days_covered > MAX_DAYS_PER_FILE:
        err = (f'File spans {parse_result.days_covered} days, max allowed is '
               f'{MAX_DAYS_PER_FILE}. Split the file and re-upload.')
        audit = AdsManualHourlyUpload.objects.create(
            marketplace       = marketplace,
            ad_type           = ad_type,
            uploaded_by       = uploaded_by_user,
            original_filename = original_filename[:256],
            rows_in_file      = parse_result.rows_in_file,
            rows_imported     = 0,
            days_covered      = parse_result.days_covered,
            date_range_start  = parse_result.date_min,
            date_range_end    = parse_result.date_max,
            status            = 'failed',
            error_message     = err,
        )
        return {
            'status':          'failed',
            'error':           err,
            'parse_result':    parse_result,
            'upload_audit_id': audit.id,
            'rows_imported':   0,
            'days_covered':    parse_result.days_covered,
            'date_range':      (parse_result.date_min, parse_result.date_max),
        }

    # ── Translate campaign NAMES → real numeric IDs ───────────────────────
    # Seller Central CSV exports have no campaign_id column, so the parser
    # slugs the name as a placeholder. Map each slugged id back to the real
    # AMS/Ads-API numeric id by joining on campaign_name. Anything we can't
    # match keeps its slugged id (better than dropping the row).
    name_to_real_id = _build_name_to_id_map(marketplace, parse_result.rows)
    unmatched_names: set[str] = set()

    objs = []
    for pr in parse_result.rows:
        real_cid = name_to_real_id.get(_norm_name(pr.campaign_name))
        if not real_cid:
            unmatched_names.add(pr.campaign_name)
        objs.append(PPCCampaignHourlySnapshot(
            marketplace   = marketplace,
            date          = pr.date,
            hour          = pr.hour,
            campaign_id   = real_cid or pr.campaign_id,
            campaign_name = pr.campaign_name,
            campaign_type = ad_type,
            source        = 'manual',
            spend         = pr.spend,
            impressions   = pr.impressions,
            clicks        = pr.clicks,
            orders_7d     = pr.orders,
            sales_7d      = pr.sales,
            units_7d      = pr.units,
        ))
    if unmatched_names:
        logger.warning('manual_hourly_upload: %d campaign names unmatched to '
                       'numeric IDs (will use slugged fallback): %s',
                       len(unmatched_names),
                       sorted(unmatched_names)[:5])

    with transaction.atomic():
        PPCCampaignHourlySnapshot.objects.bulk_create(
            objs,
            update_conflicts=True,
            update_fields=[
                'campaign_name', 'source',
                'spend', 'impressions', 'clicks',
                'orders_7d', 'sales_7d', 'units_7d',
            ],
            unique_fields=['marketplace', 'date', 'hour',
                           'campaign_id', 'campaign_type'],
        )

        # Per-day sync-log entries — gives the dashboard the "complete" flag
        # so the heatmap can render these dates.
        log_source = _SOURCE_PER_AD_TYPE[ad_type]
        dates_seen = sorted({r.date for r in parse_result.rows})
        for d in dates_seen:
            n_for_day = sum(1 for r in parse_result.rows if r.date == d)
            log_sync(marketplace, d, log_source, 'ok',
                     rows_received=n_for_day,
                     error_message='manual upload')

        audit = AdsManualHourlyUpload.objects.create(
            marketplace       = marketplace,
            ad_type           = ad_type,
            uploaded_by       = uploaded_by_user,
            original_filename = original_filename[:256],
            rows_in_file      = parse_result.rows_in_file,
            rows_imported     = len(objs),
            days_covered      = parse_result.days_covered,
            date_range_start  = parse_result.date_min,
            date_range_end    = parse_result.date_max,
            status            = 'ok',
        )

    return {
        'status':          'ok',
        'error':           '',
        'parse_result':    parse_result,
        'upload_audit_id': audit.id,
        'rows_imported':   len(objs),
        'days_covered':    parse_result.days_covered,
        'date_range':      (parse_result.date_min, parse_result.date_max),
    }
