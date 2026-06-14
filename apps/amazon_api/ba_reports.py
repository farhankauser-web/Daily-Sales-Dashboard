"""
ba_reports — Phase 3 Brand Analytics report configs + row normalizers.

Houses the per-kind config that drives `ingest_brand_analytics` and translates
each Amazon BA report's JSON shape into the field names expected by our
BA*Weekly models.

Key constraints we learned the hard way:
  • Sunday-Saturday weeks. Amazon's BA reports reject any week boundary that
    isn't Sun-Sat ("dataStartTime must be a Sunday when reportPeriod=WEEK").
  • Per-ASIN scope is mandatory for SQP / Item Comparison / Market Basket —
    Amazon retired the brand-aggregate variant. Each report submission is
    one (marketplace × week × ASIN) tuple.
  • Data lands ~3-5 days after the week ends; the cron submits on Wednesdays.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any


# ── BA report kinds ─────────────────────────────────────────────────────────

BA_REPORT_CONFIGS: dict[str, dict] = {
    'ba_search_query': dict(
        report_type='GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT',
        sync_source='ba_search_query_weekly',
        per_asin=True,   # one report per (week × ASIN)
        data_keys=('dataByAsin',),
    ),
    # GET_BRAND_ANALYTICS_ITEM_COMPARISON_REPORT is DEPRECATED as of 2026.
    # Amazon does not produce data for it anymore. Surfacing competitor-by-ASIN
    # comparison data is no longer possible via BA — the Phase 3 Competitor
    # Analysis page will need to derive that signal elsewhere (e.g. spAdvertised
    # Product purchasedAsin) or be dropped.
    'ba_market_basket': dict(
        report_type='GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT',
        sync_source='ba_market_basket_weekly',
        per_asin=False,  # ONE report per (week) covers all our ASINs
        data_keys=('dataByAsin',),
    ),
    'ba_repeat_purchase': dict(
        report_type='GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT',
        sync_source='ba_repeat_purchase_weekly',
        per_asin=False,  # ONE report per (week) covers all our ASINs
        data_keys=('dataByAsin',),
    ),
}


def last_completed_sun_sat_week(today: date) -> tuple[date, date]:
    """
    Returns (sun, sat) of the most recent COMPLETED Sun-Sat week.

    Amazon BA weekly reports use Sunday as the first day of the week. If
    today is Saturday, "this week" is still in progress — we return last week.
    """
    # weekday(): Mon=0 ... Sat=5, Sun=6
    days_since_sat = (today.weekday() - 5) % 7
    if days_since_sat == 0:
        # today is Saturday — wait until tomorrow to consider this week complete
        days_since_sat = 7
    sat = today - timedelta(days=days_since_sat)
    sun = sat - timedelta(days=6)
    return sun, sat


def n_completed_weeks(today: date, n: int) -> list[tuple[date, date]]:
    """Returns the last N completed Sun-Sat windows, most recent first."""
    weeks = []
    sun, sat = last_completed_sun_sat_week(today)
    for _ in range(n):
        weeks.append((sun, sat))
        sat = sun - timedelta(days=1)
        sun = sat - timedelta(days=6)
    return weeks


# ── Row normalizers ────────────────────────────────────────────────────────

def _f(d: dict, *path, default=0):
    """Safe nested dict get — `_f(row, 'clickData', 'asinShare', default=0)`."""
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def normalize_sqp_row(raw_row: dict, marketplace: str, asin: str,
                       week_start: date, week_end: date) -> dict:
    """
    Translate one row from the SQP report's `dataByAsin` array into the
    BASearchQueryWeekly field dict.

    Amazon's real schema (confirmed live for the USA marketplace, 2026-06):
        searchQueryData.searchQuery / searchQueryScore / searchQueryVolume
        impressionData.totalQueryImpressionCount / asinImpressionCount / asinImpressionShare
        clickData.totalClickCount        / asinClickCount    / asinClickShare
        cartAddData.totalCartAddCount    / asinCartAddCount  / asinCartAddShare
        purchaseData.totalPurchaseCount  / asinPurchaseCount / asinPurchaseShare

    Share columns are returned as PERCENTAGES (0-100, not 0-1).
    """
    sq = _f(raw_row, 'searchQueryData', 'searchQuery', default='') or ''
    return {
        'marketplace':            marketplace,
        'week_start':             week_start,
        'week_end':               week_end,
        'asin':                   asin,
        'search_query':           sq[:512],
        'search_query_hash':      hashlib.sha1(sq.lower().encode('utf-8')).hexdigest(),
        'search_query_score':     int(_f(raw_row, 'searchQueryData', 'searchQueryScore')),
        'search_query_volume':    int(_f(raw_row, 'searchQueryData', 'searchQueryVolume')),

        'impressions_total':      int(_f(raw_row, 'impressionData', 'totalQueryImpressionCount')),
        'impressions_asin_count': int(_f(raw_row, 'impressionData', 'asinImpressionCount')),
        'clicks_total':           int(_f(raw_row, 'clickData',      'totalClickCount')),
        'cart_adds_total':        int(_f(raw_row, 'cartAddData',    'totalCartAddCount')),
        'purchases_total':        int(_f(raw_row, 'purchaseData',   'totalPurchaseCount')),

        'asin_impression_count':  int(_f(raw_row, 'impressionData', 'asinImpressionCount')),
        'asin_click_count':       int(_f(raw_row, 'clickData',      'asinClickCount')),
        'asin_cart_add_count':    int(_f(raw_row, 'cartAddData',    'asinCartAddCount')),
        'asin_purchase_count':    int(_f(raw_row, 'purchaseData',   'asinPurchaseCount')),

        # Shares are percentages (0-100). DecimalField stores them verbatim.
        'brand_impressions_share': round(float(_f(raw_row, 'impressionData', 'asinImpressionShare')), 4),
        'brand_click_share':       round(float(_f(raw_row, 'clickData',      'asinClickShare')),      4),
        'brand_cart_add_share':    round(float(_f(raw_row, 'cartAddData',    'asinCartAddShare')),    4),
        'brand_purchase_share':    round(float(_f(raw_row, 'purchaseData',   'asinPurchaseShare')),   4),

        # SQP does not include competitor top-3 ASINs at the query level.
        # May be populated later via aggregation of Item Comparison data.
        'top_clicked_asins':       [],
        'top_converted_asins':     [],
        'top_purchased_asins':     [],
    }


def normalize_market_basket_row(raw_row: dict, marketplace: str, asin_arg,
                                 week_start: date, week_end: date) -> dict:
    """
    Translate one row from the Market Basket report (`dataByAsin` array) into
    BAMarketBasketWeekly field dict.

    Each row of `dataByAsin` already contains its own `asin` (one of our ASINs)
    and a co-purchased pairing, so `asin_arg` is ignored — we read from the row.
    Confirmed live shape:
        startDate, endDate, asin, purchasedWithAsin, purchasedWithRank, combinationPct
    """
    return {
        'marketplace':              marketplace,
        'week_start':               week_start,
        'week_end':                 week_end,
        'asin':                     (raw_row.get('asin') or '')[:16],
        'purchased_asin':           (raw_row.get('purchasedWithAsin') or '')[:16],
        'purchased_title':          (raw_row.get('purchasedWithProductTitle') or
                                      raw_row.get('purchasedProductTitle') or '')[:512],
        'purchased_frequency_rank': int(raw_row.get('purchasedWithRank') or
                                         raw_row.get('rank') or 0),
        'combination_pct':          round(float(raw_row.get('combinationPct') or 0), 6),
    }


def normalize_repeat_purchase_row(raw_row: dict, marketplace: str, asin_arg,
                                   week_start: date, week_end: date) -> dict:
    """
    Translate one row from the Repeat Purchase report. Brand-level — each row
    is one of our ASINs with retention metrics.

    Confirmed live shape:
        startDate, endDate, asin, orders, uniqueCustomers,
        repeatCustomersPctTotal, repeatPurchaseRevenue {amount, currencyCode},
        repeatPurchaseRevenuePctTotal
    """
    rev_obj = raw_row.get('repeatPurchaseRevenue') or {}
    return {
        'marketplace':                    marketplace,
        'week_start':                     week_start,
        'week_end':                       week_end,
        'asin':                           (raw_row.get('asin') or '')[:16],
        'orders':                         int(raw_row.get('orders') or 0),
        'unique_customers':               int(raw_row.get('uniqueCustomers') or 0),
        'repeat_customers_pct':           round(float(raw_row.get('repeatCustomersPctTotal') or 0), 6),
        'repeat_purchase_revenue':        round(float(rev_obj.get('amount') or 0), 2),
        'repeat_purchase_revenue_pct':    round(float(raw_row.get('repeatPurchaseRevenuePctTotal') or 0), 6),
    }
