"""
apps/dashboard/completeness.py — Layered data-completeness checks.

Enforces the contract:

  CORE LAYER (gates whether a day appears on Hourly Patterns at all):
      - sp_hourly  must have a successful AdsDataSyncLog entry
      - orders     must have a successful AdsDataSyncLog entry

  ADS LAYER (gates whether SB/SD numbers appear for that day):
      - sb_daily   independently checked
      - sd_daily   independently checked

Each source's sync log distinguishes:
  - 'ok'                → rows received from Amazon
  - 'empty_from_amazon' → Amazon returned 0 rows (legitimate zero)
  - 'failed'            → sync errored, treat as missing
  - 'pending'           → sync in flight, treat as missing

`is_successful` (ok | empty_from_amazon) = "we know the answer."
Anything else = "we don't yet."
"""
from __future__ import annotations

from datetime import date as date_cls, timedelta
from typing import Iterable

from django.utils import timezone

from .models import AdsDataSyncLog


CORE_SOURCES = ('sp_hourly', 'orders')
ADS_SOURCES  = ('sb_daily', 'sd_daily')
# Phase 1 — detail reports (search-term, targeting, advertised-product,
# placement, ad-group across SP/SB/SD). Names match
# `ads_detail_reports.REPORT_KIND_TO_SYNC_SOURCE`.
DETAIL_SOURCES = (
    'sp_search_term_daily',         'sb_search_term_daily',
    'sp_targeting_daily',           'sb_targeting_daily',           'sd_targeting_daily',
    'sp_advertised_product_daily',  'sb_advertised_product_daily',  'sd_advertised_product_daily',
    'sp_placement_daily',           'sb_placement_daily',
    'sp_adgroup_daily',             'sb_adgroup_daily',             'sd_adgroup_daily',
)
# Phase 3 — Brand Analytics (weekly cadence). ba_item_comparison_weekly is
# kept in the list so historical 'failed' rows still pass validation, but the
# report type has been DEPRECATED by Amazon and is no longer in BA_REPORT_CONFIGS.
BRAND_ANALYTICS_SOURCES = (
    'ba_search_query_weekly',
    'ba_item_comparison_weekly',
    'ba_market_basket_weekly',
    'ba_repeat_purchase_weekly',
)
ALL_SOURCES  = CORE_SOURCES + ADS_SOURCES + DETAIL_SOURCES + BRAND_ANALYTICS_SOURCES

_SUCCESS_STATUSES = ('ok', 'empty_from_amazon')


# ─────────────────────────────────────────────────────────────────────────────
# WRITE — every ingestion command must call exactly one of these per (date, source)
# ─────────────────────────────────────────────────────────────────────────────
def log_sync(
    marketplace:    str,
    date:           date_cls,
    source:         str,
    status:         str,
    rows_received:  int = 0,
    error_message:  str = '',
    report_id:      str = '',
    asin:           str = '',
) -> AdsDataSyncLog:
    """
    Upsert a row in AdsDataSyncLog. Idempotent — repeated calls overwrite.

    Status values: 'ok' | 'empty_from_amazon' | 'failed' | 'pending'
    Sources:        'sp_hourly' | 'sb_daily' | 'sd_daily' | 'orders' | etc.

    `asin` is empty for everything except Phase 3 Brand Analytics, where each
    SP-API report submission is scoped to one ASIN — the unique key on
    AdsDataSyncLog includes asin to keep BA rows per-ASIN.
    """
    if source not in ALL_SOURCES:
        raise ValueError(f'Unknown source {source!r}, expected one of {ALL_SOURCES}')
    valid = ('ok', 'empty_from_amazon', 'failed', 'pending')
    if status not in valid:
        raise ValueError(f'Unknown status {status!r}, expected one of {valid}')

    # SQLite serialises writes via a single global lock; if another process
    # (e.g. the dev server, a parallel cron) holds the writer at the same
    # moment, we get OperationalError("database is locked"). The retry
    # absorbs that brief contention; if it's still locked after 3 backoffs,
    # the original error is re-raised so the caller still sees the failure.
    import time as _time
    from django.db.utils import OperationalError as _OperationalError
    last_err = None
    for delay in (0, 1, 2, 4):
        if delay:
            _time.sleep(delay)
        try:
            obj, _ = AdsDataSyncLog.objects.update_or_create(
                marketplace=marketplace, date=date, source=source, asin=asin,
                defaults={
                    'status':        status,
                    'rows_received': int(rows_received),
                    'error_message': error_message,
                    'report_id':     report_id,
                },
            )
            return obj
        except _OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            last_err = e
    raise last_err


def log_sync_pending(marketplace: str, date: date_cls, source: str,
                     report_id: str = '') -> AdsDataSyncLog:
    """Mark a sync as in-flight while we wait for Amazon to build the report."""
    return log_sync(marketplace, date, source, 'pending', report_id=report_id)


# ─────────────────────────────────────────────────────────────────────────────
# READ — completeness queries used by the Hourly Patterns view and tests
# ─────────────────────────────────────────────────────────────────────────────
def _successful_sources(marketplace: str, date: date_cls) -> set[str]:
    """Returns the set of sources that have a successful sync for (mp, date)."""
    return set(
        AdsDataSyncLog.objects
        .filter(marketplace=marketplace, date=date, status__in=_SUCCESS_STATUSES)
        .values_list('source', flat=True)
    )


def day_core_complete(marketplace: str, date: date_cls) -> bool:
    """
    True if BOTH sp_hourly AND orders have successful syncs for that day.
    Core completeness is what makes the day renderable on Hourly Patterns.
    """
    return set(CORE_SOURCES).issubset(_successful_sources(marketplace, date))


def day_ads_complete(marketplace: str, date: date_cls) -> dict[str, bool]:
    """
    Per-ad-source flags for the day.
    Returns: {'sp_hourly': bool, 'sb_daily': bool, 'sd_daily': bool, 'orders': bool}
    """
    successes = _successful_sources(marketplace, date)
    return {s: (s in successes) for s in ALL_SOURCES}


def get_renderable_dates(
    marketplace: str,
    start_date:  date_cls,
    end_date:    date_cls,
) -> list[date_cls]:
    """
    Returns the list of dates in [start_date, end_date] (inclusive) that
    have CORE completeness (sp_hourly + orders both OK).

    Days outside this list are *hidden entirely* on the Hourly Patterns page —
    excluded from aggregates, heatmap cells, KPI averages, weekday pattern.
    """
    if end_date < start_date:
        return []
    qs = (
        AdsDataSyncLog.objects
        .filter(marketplace=marketplace,
                date__gte=start_date, date__lte=end_date,
                source__in=CORE_SOURCES,
                status__in=_SUCCESS_STATUSES)
        .values('date', 'source')
    )
    seen: dict[date_cls, set[str]] = {}
    for r in qs:
        seen.setdefault(r['date'], set()).add(r['source'])
    renderable = [d for d, srcs in seen.items()
                  if set(CORE_SOURCES).issubset(srcs)]
    return sorted(renderable)


def get_incomplete_dates(
    marketplace: str,
    start_date:  date_cls,
    end_date:    date_cls,
    source:      str = None,
) -> list[date_cls]:
    """
    Returns the list of dates in [start_date, end_date] that are NOT successful
    for the given source. If `source` is None, returns dates where ANY source is
    missing.

    Used for the UI's "Avg PPC includes only X of Y days" footnote.
    """
    if end_date < start_date:
        return []

    cur = start_date
    all_dates: set[date_cls] = set()
    while cur <= end_date:
        all_dates.add(cur)
        cur += timedelta(days=1)

    qs = AdsDataSyncLog.objects.filter(
        marketplace=marketplace,
        date__gte=start_date, date__lte=end_date,
        status__in=_SUCCESS_STATUSES,
    )
    if source:
        qs = qs.filter(source=source)
        successful_dates = set(qs.values_list('date', flat=True))
    else:
        # Aggregate per day — date is "complete" only if every source is successful
        successes_by_date: dict[date_cls, set[str]] = {}
        for r in qs.values('date', 'source'):
            successes_by_date.setdefault(r['date'], set()).add(r['source'])
        successful_dates = {
            d for d, srcs in successes_by_date.items()
            if set(ALL_SOURCES).issubset(srcs)
        }
    return sorted(all_dates - successful_dates)


def days_complete_for_source(
    marketplace: str,
    start_date:  date_cls,
    end_date:    date_cls,
    source:      str,
) -> list[date_cls]:
    """Returns the list of dates with successful sync for one specific source."""
    return sorted(
        AdsDataSyncLog.objects
        .filter(marketplace=marketplace,
                date__gte=start_date, date__lte=end_date,
                source=source,
                status__in=_SUCCESS_STATUSES)
        .values_list('date', flat=True)
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE — bulk metadata block for the UI
# ─────────────────────────────────────────────────────────────────────────────
def completeness_report(
    marketplace: str,
    start_date:  date_cls,
    end_date:    date_cls,
) -> dict:
    """
    Returns a structured report:
        {
            'renderable':       [date, …],            # core-complete days
            'incomplete':       [date, …],            # any source missing
            'per_source':       {
                'sp_hourly': [date, …],   # days with successful sync
                'orders':    [date, …],
                'sb_daily':  [date, …],
                'sd_daily':  [date, …],
            },
            'totals': {
                'days_in_window':     int,
                'days_renderable':    int,
                'days_with_full_ads': int,
                'days_sp_only':       int,    # core OK but SB/SD missing
            }
        }

    Used by the Hourly Patterns view to build the response envelope.
    """
    renderable = set(get_renderable_dates(marketplace, start_date, end_date))
    per_source = {
        s: days_complete_for_source(marketplace, start_date, end_date, s)
        for s in ALL_SOURCES
    }
    sb_set = set(per_source['sb_daily'])
    sd_set = set(per_source['sd_daily'])
    days_full_ads = sorted(renderable & sb_set & sd_set)
    days_sp_only  = sorted(renderable - (sb_set & sd_set))

    days_in_window = (end_date - start_date).days + 1 if end_date >= start_date else 0

    return {
        'renderable':       sorted(renderable),
        'incomplete':       get_incomplete_dates(marketplace, start_date, end_date),
        'per_source':       per_source,
        'totals': {
            'days_in_window':     days_in_window,
            'days_renderable':    len(renderable),
            'days_with_full_ads': len(days_full_ads),
            'days_sp_only':       len(days_sp_only),
        },
    }
