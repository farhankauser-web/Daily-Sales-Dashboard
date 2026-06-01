"""
apps/sqp/views.py — Phase-1 Overview tab + 3 JSON endpoints + on-demand sync.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.amazon_api.models import AmazonAPIConfig
from apps.core.decorators import permission_required

from .models import SQPReport, SQPSnapshot
from .serializers import kpi_strip, snapshot_to_row
from .sync import (
    iso_week_start,
    last_completed_iso_week,
    sync_sqp_window,
)

logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────
def _resolve_marketplace(request) -> str:
    mp = request.GET.get('mp') or 'usa'
    if not request.user.can_access_marketplace(mp):
        mp = (request.user.allowed_marketplaces or ['usa'])[0]
    return mp


def _resolve_week(request) -> tuple[date, date]:
    """?week=2026-W19 → (Monday, Sunday). Default = last completed."""
    raw = request.GET.get('week')
    if raw:
        try:
            year, wk = raw.split('-W')
            mon = date.fromisocalendar(int(year), int(wk), 1)
            return mon, mon + timedelta(days=6)
        except Exception:
            pass
    return last_completed_iso_week()


def _base_qs(marketplace: str, period_start: date, asin: str | None = None):
    qs = SQPSnapshot.objects.filter(
        marketplace = marketplace,
        period_type = 'WEEK',
        period_start = period_start,
    ).select_related('query')
    if asin:
        qs = qs.filter(asin=asin.upper())
    return qs


# ═══════════════════════════════════════════════════════════════════════════
# Page view
# ═══════════════════════════════════════════════════════════════════════════
@login_required
@permission_required('can_view_dashboard')
def overview(request):
    marketplace = _resolve_marketplace(request)
    cfg = AmazonAPIConfig.objects.filter(marketplace=marketplace, is_active=True).first()

    # Build a small "available weeks" list for the picker (most recent first)
    available_weeks = list(
        SQPReport.objects
            .filter(marketplace=marketplace, period_type='WEEK', status__in=['done', 'empty'])
            .values_list('period_start', flat=True)
            .order_by('-period_start')[:26]
    )

    ctx = {
        'marketplace':           marketplace,
        'available_weeks':       available_weeks,
        'last_completed_week':   last_completed_iso_week()[0],
        'has_config':            bool(cfg and cfg.has_sp_api_credentials()),
        'allowed_marketplaces':  request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys()),
    }
    return render(request, 'sqp/overview.html', ctx)


# ═══════════════════════════════════════════════════════════════════════════
# JSON endpoints
# ═══════════════════════════════════════════════════════════════════════════
@login_required
@permission_required('can_view_dashboard')
def api_overview(request):
    """KPI strip + headline counts for a single (marketplace, week)."""
    marketplace = _resolve_marketplace(request)
    mon, sun    = _resolve_week(request)
    asin        = request.GET.get('asin') or None

    qs = _base_qs(marketplace, mon, asin=asin)
    agg = qs.aggregate(
        impressions = Sum('impressions_total'),
        clicks      = Sum('clicks_total'),
        atc         = Sum('atc_total'),
        purchases   = Sum('purchases_total'),
        queries     = Count('id', distinct=False),
    )

    report = (SQPReport.objects
                       .filter(marketplace=marketplace, period_type='WEEK',
                               period_start=mon, asin=asin or '')
                       .first())

    return JsonResponse({
        'marketplace':  marketplace,
        'period_start': mon.isoformat(),
        'period_end':   sun.isoformat(),
        'asin_scope':   asin or None,
        'kpi':          kpi_strip(agg),
        'report': {
            'status':      report.status if report else 'not_synced',
            'rows_loaded': report.rows_loaded if report else 0,
            'synced_at':   report.completed_at.isoformat() if (report and report.completed_at) else None,
        },
    })


@login_required
@permission_required('can_view_dashboard')
def api_queries(request):
    """
    Top-N queries table for a (marketplace, week).
    Pagination: ?page=1&page_size=50  (page_size capped at 200)
    Sorting:    ?sort=volume|impressions|clicks|ctr|cvr|purchases  (prefix with `-` for desc)
    """
    marketplace = _resolve_marketplace(request)
    mon, _      = _resolve_week(request)
    asin        = request.GET.get('asin') or None
    qs          = _base_qs(marketplace, mon, asin=asin)

    SORT_MAP = {
        'volume':      'search_query_volume',
        'impressions': 'impressions_total',
        'clicks':      'clicks_total',
        'ctr':         'click_rate',
        'atc':         'atc_total',
        'cvr':         'purchase_rate',
        'purchases':   'purchases_total',
        'rank':        'search_query_score',
    }
    sort_raw   = request.GET.get('sort', '-volume')
    desc       = sort_raw.startswith('-')
    sort_field = SORT_MAP.get(sort_raw.lstrip('-'), 'search_query_volume')
    order_by   = f'{"-" if desc else ""}{sort_field}'
    qs = qs.order_by(order_by, '-search_query_volume')

    # Pagination
    try:
        page      = max(1, int(request.GET.get('page', 1)))
        page_size = max(1, min(int(request.GET.get('page_size', 50)), 200))
    except ValueError:
        page, page_size = 1, 50
    total = qs.count()
    offset = (page - 1) * page_size
    rows = [snapshot_to_row(s) for s in qs[offset:offset + page_size]]

    return JsonResponse({
        'marketplace':  marketplace,
        'period_start': mon.isoformat(),
        'asin_scope':   asin or None,
        'page':         page,
        'page_size':    page_size,
        'total':        total,
        'sort':         sort_raw,
        'rows':         rows,
    })


@login_required
@permission_required('can_view_dashboard')
def api_trends(request):
    """
    Per-week trend series (last N weeks) for the chart on the Overview tab.
    Aggregates over all queries in the (marketplace, asin?) scope.
    ?weeks=12  (default 12, capped 52)
    """
    marketplace = _resolve_marketplace(request)
    asin        = request.GET.get('asin') or None
    try:
        weeks_back = max(1, min(int(request.GET.get('weeks', 12)), 52))
    except ValueError:
        weeks_back = 12

    end_mon, _ = last_completed_iso_week()
    start_mon  = end_mon - timedelta(weeks=weeks_back - 1)

    qs = SQPSnapshot.objects.filter(
        marketplace = marketplace,
        period_type = 'WEEK',
        period_start__gte = start_mon,
        period_start__lte = end_mon,
    )
    if asin:
        qs = qs.filter(asin=asin.upper())

    weekly = (qs.values('period_start')
                .annotate(
                    impressions = Sum('impressions_total'),
                    clicks      = Sum('clicks_total'),
                    atc         = Sum('atc_total'),
                    purchases   = Sum('purchases_total'),
                )
                .order_by('period_start'))

    by_week = {w['period_start']: w for w in weekly}

    dates, impressions, clicks, ctr, atc_rate, cvr, purchases = [], [], [], [], [], [], []
    cur = start_mon
    while cur <= end_mon:
        w = by_week.get(cur)
        i  = int(w['impressions']) if w and w['impressions'] else 0
        c  = int(w['clicks'])      if w and w['clicks']      else 0
        a  = int(w['atc'])         if w and w['atc']         else 0
        p  = int(w['purchases'])   if w and w['purchases']   else 0
        dates.append(cur.isoformat())
        impressions.append(i)
        clicks.append(c)
        ctr.append(round((c / i * 100) if i else 0, 2))
        atc_rate.append(round((a / c * 100) if c else 0, 2))
        purchases.append(p)
        cvr.append(round((p / c * 100) if c else 0, 2))
        cur += timedelta(weeks=1)

    return JsonResponse({
        'marketplace':  marketplace,
        'asin_scope':   asin or None,
        'weeks_back':   weeks_back,
        'dates':        dates,
        'impressions':  impressions,
        'clicks':       clicks,
        'ctr':          ctr,
        'atc_rate':     atc_rate,
        'purchases':    purchases,
        'cvr':          cvr,
    })


# ═══════════════════════════════════════════════════════════════════════════
# On-demand sync (button in the UI)
# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# AI Insights — Phase A
# ═══════════════════════════════════════════════════════════════════════════
@login_required
@permission_required('can_view_dashboard')
@require_POST
def api_ai_asin_analysis(request):
    """
    POST /sqp/api/ai/asin/
      mp           — marketplace (default usa)
      asin         — child ASIN (blank/'BRAND' for brand-level)
      compare      — wow | mom | yoy   (default wow)
      force        — '1' to bypass cache
      with_context — '1' to also return the aggregated metric dict
    """
    from .ai_serializers import insight_to_api
    from .ai_tasks import enqueue_asin_analysis

    marketplace = _resolve_marketplace(request)
    asin        = (request.POST.get('asin') or '').strip().upper()
    if asin in ('', 'BRAND'):
        asin = None
    compare      = (request.POST.get('compare') or 'wow').lower()
    force        = request.POST.get('force') == '1'
    with_context = request.POST.get('with_context') == '1'

    if compare not in ('wow', 'mom', 'yoy'):
        return JsonResponse({'ok': False, 'error': f"compare must be wow|mom|yoy, got {compare!r}"},
                            status=400)

    try:
        result = enqueue_asin_analysis(
            marketplace, asin, comparison=compare,
            user=request.user, force_refresh=force,
        )
    except Exception as exc:
        logger.exception('AI ASIN analysis failed')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)

    body = insight_to_api(result, include_context=with_context)
    body['ok']       = True
    body['scope']    = {'marketplace': marketplace, 'asin': asin or 'BRAND', 'compare': compare}
    return JsonResponse(body)


@login_required
@permission_required('can_view_dashboard')
@require_POST
def sync_latest_week(request):
    """Trigger a synchronous sync for the most recently completed ISO week."""
    marketplace = _resolve_marketplace(request)
    mon, sun = last_completed_iso_week()
    asin = (request.POST.get('asin') or '').strip().upper() or None
    try:
        res = sync_sqp_window(
            marketplace      = marketplace,
            period_start     = mon,
            period_end       = sun,
            period_type      = 'WEEK',
            asin             = asin,
            max_wait_seconds = int(request.POST.get('max_wait', 90)),
            triggered_by     = request.user,
        )
        return JsonResponse({
            'ok':          res['status'] in ('FRESH', 'CACHED', 'OK'),
            'status':      res['status'],
            'rows_loaded': res['rows_loaded'],
            'report_id':   res.get('report_id'),
            'period_start': mon.isoformat(),
            'period_end':   sun.isoformat(),
        })
    except Exception as exc:
        logger.error('SQP on-demand sync failed: %s', exc, exc_info=True)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)
