"""
apps/sqp/sync.py — Parse a Brand-Analytics SQP report payload and persist it.

Pure functions; callers (management commands, on-demand sync view) own the
SP-API client and timing decisions.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.amazon_api.models import AmazonAPIConfig
from apps.amazon_api.services import SPAPIClient
from .models import SQPQuery, SQPReport, SQPSnapshot

logger = logging.getLogger(__name__)


# ── ISO-week helpers (Phase 1 supports WEEK only) ─────────────────────────────
def iso_week_start(d: date_cls) -> date_cls:
    """Monday of d's ISO week."""
    return d - timedelta(days=d.weekday())


def iso_week_end(d: date_cls) -> date_cls:
    """Sunday of d's ISO week."""
    return iso_week_start(d) + timedelta(days=6)


def last_completed_iso_week(today: date_cls = None) -> tuple[date_cls, date_cls]:
    """The most recent Mon-Sun whose Sunday is strictly before today."""
    today = today or date_cls.today()
    this_monday = iso_week_start(today)
    last_monday = this_monday - timedelta(days=7)
    return last_monday, last_monday + timedelta(days=6)


# ── Payload extraction ────────────────────────────────────────────────────────
def _dec(value, places: int = 6) -> Decimal:
    """Safe conversion to Decimal — handles None and int/float/str inputs."""
    if value is None or value == '':
        return Decimal('0')
    try:
        return Decimal(str(value)).quantize(Decimal('1.' + ('0' * places)))
    except Exception:
        return Decimal('0')


def _int(value) -> int:
    if value is None or value == '':
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _price(value) -> Decimal | None:
    """Extract amount from {'amount': X, 'currencyCode': 'USD'} blocks."""
    if not value:
        return None
    if isinstance(value, dict):
        amt = value.get('amount')
    else:
        amt = value
    if amt is None or amt == '':
        return None
    try:
        return Decimal(str(amt)).quantize(Decimal('0.01'))
    except Exception:
        return None


def iter_sqp_rows(payload: dict) -> Iterable[dict]:
    """
    Yield one dict per (asin, query) row from the SQP report JSON.

    Amazon's SQP payload top-level array is keyed either `dataByAsin`
    (when reportOptions.asin was set) or `dataByDepartmentAndSearchTerm`
    in some older variants. The fields inside each row are consistent.
    """
    candidates = (
        payload.get('dataByAsin')
        or payload.get('dataByDepartmentAndSearchTerm')
        or payload.get('searchQueryData')   # very old shape
        or []
    )
    for row in candidates:
        yield row


def normalise_row(row: dict, marketplace: str, period_type: str) -> dict:
    """Flatten one SQP row into the shape SQPSnapshot expects."""
    sq      = row.get('searchQueryData') or {}
    impr    = row.get('impressionData')  or {}
    click   = row.get('clickData')       or {}
    cart    = row.get('cartAddData')     or {}
    purch   = row.get('purchaseData')    or {}

    start = row.get('startDate') or row.get('searchQueryDate') or ''
    end   = row.get('endDate')   or start
    return {
        'marketplace':            marketplace,
        'asin':                   (row.get('asin') or '').strip().upper(),
        'period_type':            period_type,
        'period_start':           datetime.strptime(start[:10], '%Y-%m-%d').date(),
        'period_end':             datetime.strptime(end[:10],   '%Y-%m-%d').date(),

        'query_text':             (sq.get('searchQuery') or '').strip(),
        'search_query_score':     _int(sq.get('searchQueryScore')),
        'search_query_volume':    _int(sq.get('searchQueryVolume')),

        'impressions_total':      _int(impr.get('totalCount')         or impr.get('totalQueryImpressionCount')),
        'impressions_asin_count': _int(impr.get('asinCount')          or impr.get('asinImpressionCount')),
        'impressions_asin_share': _dec(impr.get('asinShare')          or impr.get('asinImpressionShare')),

        'clicks_total':           _int(click.get('totalCount')        or click.get('totalClickCount')),
        'clicks_asin_count':      _int(click.get('asinCount')         or click.get('asinClickCount')),
        'clicks_asin_share':      _dec(click.get('asinShare')         or click.get('asinClickShare')),
        'click_rate':             _dec(click.get('clickRate')         or click.get('totalClickRate')),
        'clicks_median_price':    _price((click.get('priceData') or {}).get('medianPrice')
                                         or click.get('medianPrice')),

        'atc_total':              _int(cart.get('totalCount')         or cart.get('totalCartAddCount')),
        'atc_asin_count':         _int(cart.get('asinCount')          or cart.get('asinCartAddCount')),
        'atc_asin_share':         _dec(cart.get('asinShare')          or cart.get('asinCartAddShare')),
        'atc_rate':               _dec(cart.get('cartAddRate')        or cart.get('totalCartAddRate')),
        'atc_median_price':       _price((cart.get('priceData') or {}).get('medianPrice')
                                         or cart.get('medianPrice')),

        'purchases_total':        _int(purch.get('totalCount')        or purch.get('totalPurchaseCount')),
        'purchases_asin_count':   _int(purch.get('asinCount')         or purch.get('asinPurchaseCount')),
        'purchases_asin_share':   _dec(purch.get('asinShare')         or purch.get('asinPurchaseShare')),
        'purchase_rate':          _dec(purch.get('purchaseRate')      or purch.get('totalPurchaseRate')),
        'purchases_median_price': _price((purch.get('priceData') or {}).get('medianPrice')
                                         or purch.get('medianPrice')),
    }


# ── Persistence ───────────────────────────────────────────────────────────────
@transaction.atomic
def persist_payload(
    payload:      dict,
    marketplace:  str,
    period_start: date_cls,
    period_end:   date_cls,
    period_type:  str = 'WEEK',
    asin_scope:   str = '',
    sp_report_id: str = '',
    triggered_by=None,
) -> SQPReport:
    """
    Save a fully parsed SQP payload into SQPQuery + SQPSnapshot, and write a
    SQPReport row marking the (marketplace, asin_scope, period) as completed.

    Idempotent: re-persisting the same payload overwrites the existing rows
    via update_or_create. Snapshot rows outside this (marketplace, period,
    asin_scope) tuple are never touched.
    """
    report, _ = SQPReport.objects.update_or_create(
        marketplace  = marketplace,
        asin         = asin_scope or '',
        period_type  = period_type,
        period_start = period_start,
        defaults={
            'period_end':   period_end,
            'sp_report_id': sp_report_id,
            'status':       'in_progress',
            'error_message': '',
            'triggered_by': triggered_by,
        },
    )

    rows_loaded = 0
    for raw_row in iter_sqp_rows(payload):
        row = normalise_row(raw_row, marketplace, period_type)
        if not row['query_text']:
            continue

        # Query lookup / running totals
        query, _ = SQPQuery.objects.get_or_create(
            text=row['query_text'],
            defaults={
                'text_lower':  row['query_text'].lower(),
                'first_seen':  row['period_start'],
                'last_seen':   row['period_end'],
            },
        )
        # Update running totals
        SQPQuery.objects.filter(pk=query.pk).update(
            last_seen=max(query.last_seen, row['period_end']),
            first_seen=min(query.first_seen, row['period_start']),
        )

        SQPSnapshot.objects.update_or_create(
            marketplace  = row['marketplace'],
            asin         = row['asin'] or asin_scope or '',
            query        = query,
            period_type  = row['period_type'],
            period_start = row['period_start'],
            defaults={
                'period_end':              row['period_end'],
                'report':                  report,
                'search_query_score':      row['search_query_score'],
                'search_query_volume':     row['search_query_volume'],
                'impressions_total':       row['impressions_total'],
                'impressions_asin_count':  row['impressions_asin_count'],
                'impressions_asin_share':  row['impressions_asin_share'],
                'clicks_total':            row['clicks_total'],
                'clicks_asin_count':       row['clicks_asin_count'],
                'clicks_asin_share':       row['clicks_asin_share'],
                'click_rate':              row['click_rate'],
                'clicks_median_price':     row['clicks_median_price'],
                'atc_total':               row['atc_total'],
                'atc_asin_count':          row['atc_asin_count'],
                'atc_asin_share':          row['atc_asin_share'],
                'atc_rate':                row['atc_rate'],
                'atc_median_price':        row['atc_median_price'],
                'purchases_total':         row['purchases_total'],
                'purchases_asin_count':    row['purchases_asin_count'],
                'purchases_asin_share':    row['purchases_asin_share'],
                'purchase_rate':           row['purchase_rate'],
                'purchases_median_price':  row['purchases_median_price'],
            },
        )
        rows_loaded += 1

    report.rows_loaded  = rows_loaded
    report.status       = 'done' if rows_loaded else 'empty'
    report.completed_at = timezone.now()
    report.save(update_fields=['rows_loaded', 'status', 'completed_at'])
    return report


# ── High-level convenience wrapper used by commands + the on-demand view ──────
def sync_sqp_window(
    marketplace:  str,
    period_start: date_cls,
    period_end:   date_cls,
    period_type:  str = 'WEEK',
    asin:         str = None,
    max_wait_seconds: int = 300,
    progress_cb=None,
    triggered_by=None,
) -> dict:
    """
    Submit + poll + download + persist a SQP report.
    Returns {'status', 'rows_loaded', 'report_id', 'report'}.
    """
    cfg = AmazonAPIConfig.objects.filter(marketplace=marketplace, is_active=True).first()
    if not cfg or not cfg.has_sp_api_credentials():
        return {'status': 'NO_CONFIG', 'rows_loaded': 0, 'report_id': None, 'report': None}

    client = SPAPIClient(cfg)
    result = client.fetch_sqp_report_sync(
        period_start.isoformat(),
        period_end.isoformat(),
        period_type=period_type,
        asin=asin,
        max_wait_seconds=max_wait_seconds,
        progress_cb=progress_cb,
    )

    if result['data'] is None:
        # Mark or create a pending report row so we can resume
        rpt, _ = SQPReport.objects.update_or_create(
            marketplace  = marketplace,
            asin         = asin or '',
            period_type  = period_type,
            period_start = period_start,
            defaults={
                'period_end':   period_end,
                'sp_report_id': result.get('report_id') or '',
                'status':       'failed' if result['status'].startswith(('CANCELLED', 'FATAL', 'CREATE_FAILED'))
                                else 'pending',
                'error_message': result['status'],
                'triggered_by': triggered_by,
            },
        )
        return {
            'status':      result['status'],
            'rows_loaded': 0,
            'report_id':   result.get('report_id'),
            'report':      rpt,
        }

    rpt = persist_payload(
        result['data'], marketplace, period_start, period_end,
        period_type=period_type,
        asin_scope=asin or '',
        sp_report_id=result.get('report_id', ''),
        triggered_by=triggered_by,
    )
    return {
        'status':      result['status'],
        'rows_loaded': rpt.rows_loaded,
        'report_id':   result.get('report_id'),
        'report':      rpt,
    }


def configured_marketplaces() -> list[str]:
    """Marketplaces with active SP-API credentials — same helper exists in dashboard.sync."""
    return list(
        AmazonAPIConfig.objects
        .filter(is_active=True)
        .values_list('marketplace', flat=True)
    )
