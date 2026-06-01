"""
apps/sqp/serializers.py — JSON shaping for the API endpoints.
Plain dicts (no DRF dependency). Centralised so the same row shape is used by
overview/queries/trends and by future export endpoints.
"""
from __future__ import annotations


def snapshot_to_row(snap) -> dict:
    """SQPSnapshot → flat dict suitable for the front-end table."""
    return {
        'asin':                snap.asin or None,
        'query':               snap.query.text,
        'period_start':        snap.period_start.isoformat(),
        'period_end':          snap.period_end.isoformat(),
        'search_query_score':  snap.search_query_score,
        'search_query_volume': snap.search_query_volume,

        'impressions':         snap.impressions_total,
        'impressions_share':   round(snap.impressions_share_pct, 2),

        'clicks':              snap.clicks_total,
        'clicks_share':        round(snap.clicks_share_pct, 2),
        'ctr':                 round(snap.ctr_pct, 2),
        'click_price_median':  float(snap.clicks_median_price) if snap.clicks_median_price is not None else None,

        'atc':                 snap.atc_total,
        'atc_rate':            round(snap.atc_rate_pct, 2),

        'purchases':           snap.purchases_total,
        'purchases_share':     round(snap.purchases_share_pct, 2),
        'cvr':                 round(snap.cvr_pct, 2),
        'purchase_price_median': float(snap.purchases_median_price) if snap.purchases_median_price is not None else None,
    }


def kpi_strip(agg: dict) -> dict:
    """Aggregate dict (from .aggregate(...)) → KPI strip payload."""
    impressions = agg.get('impressions') or 0
    clicks      = agg.get('clicks')      or 0
    atc         = agg.get('atc')         or 0
    purchases   = agg.get('purchases')   or 0
    return {
        'impressions':  int(impressions),
        'clicks':       int(clicks),
        'ctr':          round((clicks / impressions * 100) if impressions else 0, 2),
        'atc':          int(atc),
        'atc_rate':     round((atc / clicks * 100) if clicks else 0, 2),
        'purchases':    int(purchases),
        'cvr':          round((purchases / clicks * 100) if clicks else 0, 2),
        'sqp_queries':  int(agg.get('queries') or 0),
    }
