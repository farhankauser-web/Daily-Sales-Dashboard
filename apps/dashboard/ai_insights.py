"""
ai_insights — Phase 4 — Anthropic-backed analysis helpers.

This module bundles three responsibilities:

  1. `gather_briefing_data(marketplace, target_date)` — pull a compact, fully
     numeric briefing dict from yesterday's P&L, top/worst campaigns, best/worst
     SKUs, alerts, BA share signal, and basket signal. The dict is what every
     Phase 4 prompt is built from — keep it stable and serialisable.

  2. `call_anthropic(...)` — single point of entry for the Claude REST call.
     Reads credentials from `AIProviderConfig('anthropic')` → `AnthropicConfig`
     → settings fallback (matching the existing summary_stream priority).

  3. `gather_*` per-scope helpers (campaign / sku) — the same data shape, but
     scoped narrower so per-page commentary widgets get only what they need.

All gather_* functions return plain JSON-serialisable dicts so the call sites
can include them in prompts directly via json.dumps().
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ── Credential resolution ────────────────────────────────────────────────────

def get_anthropic_credentials() -> tuple[str | None, str]:
    """
    Returns (api_key, model) following the same priority order as the existing
    summary_stream view: AIProviderConfig('anthropic') → AnthropicConfig →
    settings.ANTHROPIC_API_KEY / ANTHROPIC_MODEL.
    """
    from apps.amazon_api.models import AIProviderConfig, AnthropicConfig

    ai_prov = AIProviderConfig.get_for('anthropic')
    legacy  = AnthropicConfig.get_active()

    if ai_prov:
        return ai_prov.api_key, (ai_prov.get_model() or settings.ANTHROPIC_MODEL)
    if legacy:
        return (legacy.api_key or settings.ANTHROPIC_API_KEY,
                legacy.model   or settings.ANTHROPIC_MODEL)
    return settings.ANTHROPIC_API_KEY, settings.ANTHROPIC_MODEL


def call_anthropic(
    system: str,
    user_message: str,
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 90,
) -> dict:
    """
    One-shot non-streaming Claude REST call.

    Returns a dict {'ok': bool, 'text': str, 'error': str|None,
                    'model': str, 'usage': {...}}. Never raises — callers can
    branch on `ok` and surface `error` in the UI.
    """
    api_key, default_model = get_anthropic_credentials()
    if not api_key:
        return {'ok': False, 'text': '', 'error':
            'Anthropic API key not configured. Go to API Config → Anthropic '
            'to add your key.', 'model': model or default_model, 'usage': {}}

    use_model = model or default_model
    body = {
        'model':      use_model,
        'max_tokens': max_tokens,
        'system':     system,
        'messages':   [{'role': 'user', 'content': user_message}],
    }
    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key':         api_key,
                'anthropic-version': '2023-06-01',
                'Content-Type':      'application/json',
            },
            json=body,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return {'ok': False, 'text': '', 'error': f'network: {e}',
                'model': use_model, 'usage': {}}

    if not r.ok:
        try:
            detail = r.json()
            err = (detail.get('error', {}) or {}).get('message') or str(detail)[:300]
        except Exception:
            err = r.text[:300]
        return {'ok': False, 'text': '', 'error': f'http {r.status_code}: {err}',
                'model': use_model, 'usage': {}}

    payload = r.json() or {}
    blocks = payload.get('content') or []
    # Concatenate any text-type blocks (Claude can emit thinking / tool blocks,
    # we ignore those and keep the actual text).
    text = '\n'.join(b.get('text', '') for b in blocks
                     if isinstance(b, dict) and b.get('type') == 'text')
    return {
        'ok':    True,
        'text':  text.strip(),
        'error': None,
        'model': payload.get('model') or use_model,
        'usage': payload.get('usage') or {},
    }


# ── Briefing data gathering ─────────────────────────────────────────────────

def _safe_decimal(v) -> Decimal:
    try:
        return Decimal(v) if v is not None else Decimal('0')
    except Exception:
        return Decimal('0')


def _day_pl(marketplace: str, d: date) -> dict | None:
    """Yesterday's P&L row in the same shape morning_report.api uses."""
    from .models import DailyMetric
    r = DailyMetric.objects.filter(marketplace=marketplace, date=d).first()
    if not r:
        return None
    rev   = float(r.revenue);    ppc = float(r.ppc_spend)
    ref   = float(r.amazon_fee); fba = float(r.fba_fee)
    cogs  = float(r.cgs)
    profit = rev - ppc - ref - fba - cogs
    return {
        'date':      d.isoformat(),
        'revenue':   round(rev,    2),
        'ppc':       round(ppc,    2),
        'referral':  round(ref,    2),
        'fba':       round(fba,    2),
        'cogs':      round(cogs,   2),
        'profit':    round(profit, 2),
        'margin_pct': round(profit / rev * 100, 2) if rev > 0 else 0.0,
        'tacos':      round(ppc    / rev * 100, 2) if rev > 0 else 0.0,
        'orders':     r.orders,
        'units':      r.units,
    }


def gather_briefing_data(marketplace: str, target_date: date | None = None,
                          window_days: int = 7) -> dict:
    """
    Compact briefing dict used by Phase 4 prompts.

    Sections:
      day_pl, day_pl_prev, deltas         — yesterday vs day-before P&L
      week_pl, week_pl_prev               — last 7-day P&L vs prior 7-day
      top_campaigns, worst_campaigns      — yesterday by gross_profit
      top_skus, worst_skus                — last 7d by gross_profit
      losing_campaigns                    — yesterday's losers with spend
      wasted_terms                        — yesterday's high-spend, no-sales terms
      alerts                              — unresolved alerts (last 14d)
      ba_share                            — most-recent week brand share
      cross_sell_signals                  — top external co-purchase pairs

    Keep this lean — every field gets serialised into the prompt, so anything
    irrelevant just costs tokens.
    """
    from .models import (
        DailyMetric, CampaignProfitDaily, DailySkuSnapshot, SkuPpcAllocation,
        Product, Alert, AdsSearchTermDailySnapshot, Campaign,
        PPCCampaignSnapshot,
        BASearchQueryWeekly, BAMarketBasketWeekly,
    )
    from django.db.models import Sum, Avg, Max

    today_d = date.today()
    yday = target_date or (today_d - timedelta(days=1))
    dby  = yday - timedelta(days=1)
    week_end   = yday
    week_start = yday - timedelta(days=window_days - 1)
    prev_week_end   = week_start - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=window_days - 1)

    # ── P&L ────────────────────────────────────────────────────────────────
    day_pl = _day_pl(marketplace, yday)
    dby_pl = _day_pl(marketplace, dby)
    deltas = None
    if day_pl and dby_pl:
        deltas = {k: round(day_pl[k] - dby_pl[k], 2)
                   for k in ('revenue', 'ppc', 'profit', 'margin_pct',
                              'tacos', 'orders', 'units')}

    def _window_pl(s: date, e: date) -> dict:
        agg = DailyMetric.objects.filter(
            marketplace=marketplace, date__gte=s, date__lte=e
        ).aggregate(
            rev=Sum('revenue'),    ppc=Sum('ppc_spend'),
            ref=Sum('amazon_fee'), fba=Sum('fba_fee'), cogs=Sum('cgs'),
            orders=Sum('orders'),  units=Sum('units'),
        )
        rev = float(agg['rev'] or 0); ppc = float(agg['ppc'] or 0)
        ref = float(agg['ref'] or 0); fba = float(agg['fba'] or 0)
        cogs= float(agg['cogs'] or 0)
        profit = rev - ppc - ref - fba - cogs
        return {
            'start':    s.isoformat(), 'end': e.isoformat(),
            'revenue':  round(rev, 2),
            'ppc':      round(ppc, 2),
            'profit':   round(profit, 2),
            'margin_pct': round(profit / rev * 100, 2) if rev > 0 else 0.0,
            'tacos':      round(ppc    / rev * 100, 2) if rev > 0 else 0.0,
            'orders':     int(agg['orders'] or 0),
            'units':      int(agg['units']  or 0),
        }
    week_pl      = _window_pl(week_start, week_end)
    week_pl_prev = _window_pl(prev_week_start, prev_week_end)

    # ── Campaigns ──────────────────────────────────────────────────────────
    def _camp_name(cid: str) -> str:
        c = Campaign.objects.filter(marketplace=marketplace, campaign_id=cid
                                     ).values_list('campaign_name', flat=True).first()
        if c: return c
        c = PPCCampaignSnapshot.objects.filter(
            marketplace=marketplace, campaign_id=cid
        ).order_by('-date').values_list('campaign_name', flat=True).first()
        return c or cid

    camp_rows = list(CampaignProfitDaily.objects.filter(
        marketplace=marketplace, date=yday,
    ).values('campaign_id', 'spend', 'ad_revenue', 'gross_profit',
             'margin_pct', 'acos', 'roas'))

    def _camp_summary(r, profit_sort_only=False) -> dict:
        return {
            'campaign_id':   r['campaign_id'],
            'campaign_name': _camp_name(r['campaign_id']),
            'spend':         round(float(r['spend']        or 0), 2),
            'revenue':       round(float(r['ad_revenue']   or 0), 2),
            'profit':        round(float(r['gross_profit'] or 0), 2),
            'margin_pct':    round(float(r['margin_pct']   or 0), 2),
            'roas':          round(float(r['roas']         or 0), 2),
            'acos':          round(float(r['acos']         or 0), 2),
        }

    top_campaigns = sorted(camp_rows, key=lambda r: float(r['gross_profit'] or 0), reverse=True)[:5]
    top_campaigns = [_camp_summary(r) for r in top_campaigns]
    worst_campaigns = sorted(camp_rows, key=lambda r: float(r['gross_profit'] or 0))[:5]
    worst_campaigns = [_camp_summary(r) for r in worst_campaigns]

    # Losing campaigns with non-trivial spend yesterday
    losing_campaigns = sorted(
        [r for r in camp_rows
          if float(r['gross_profit'] or 0) < -10 and float(r['spend'] or 0) > 10],
        key=lambda r: float(r['gross_profit'] or 0),
    )[:10]
    losing_campaigns = [_camp_summary(r) for r in losing_campaigns]

    # ── SKUs over the last 7 days ──────────────────────────────────────────
    sku_pl: dict[str, dict] = defaultdict(lambda: {
        'sku':'', 'revenue': Decimal('0'), 'units': 0, 'cogs': Decimal('0'),
        'referral': Decimal('0'), 'fba': Decimal('0'), 'ppc': Decimal('0'),
    })
    for r in DailySkuSnapshot.objects.filter(
        marketplace=marketplace, date__gte=week_start, date__lte=week_end
    ).values('sku', 'revenue', 'qty', 'cgs', 'amz_fee', 'fulfill'):
        b = sku_pl[r['sku']]
        b['sku'] = r['sku']
        b['revenue'] += _safe_decimal(r['revenue'])
        b['units']   += int(r['qty'] or 0)
        b['cogs']    += _safe_decimal(r['cgs'])
        b['referral']+= _safe_decimal(r['amz_fee'])
        b['fba']     += _safe_decimal(r['fulfill'])
    for r in SkuPpcAllocation.objects.filter(
        marketplace=marketplace, date__gte=week_start, date__lte=week_end
    ).values('sku').annotate(spend=Sum('sku_ppc_spend')):
        if r['sku'] in sku_pl:
            sku_pl[r['sku']]['ppc'] = _safe_decimal(r['spend'])

    titles = {p.sku: (p.title or '', p.brand or '')
               for p in Product.objects.filter(marketplace=marketplace,
                                                sku__in=sku_pl.keys()
                                                ).only('sku', 'title', 'brand')}
    sku_rows = []
    for sku, b in sku_pl.items():
        profit = b['revenue'] - b['ppc'] - b['referral'] - b['fba'] - b['cogs']
        title, brand = titles.get(sku, ('', ''))
        sku_rows.append({
            'sku':       sku,
            'title':     title[:64],
            'brand':     brand,
            'revenue':   round(float(b['revenue']), 2),
            'units':     b['units'],
            'ppc':       round(float(b['ppc']),     2),
            'profit':    round(float(profit),       2),
            'margin_pct':round(float(profit / b['revenue'] * 100), 2) if b['revenue'] > 0 else 0.0,
            'tacos':     round(float(b['ppc'] / b['revenue'] * 100), 2) if b['revenue'] > 0 else 0.0,
        })
    top_skus   = sorted(sku_rows, key=lambda r: r['profit'], reverse=True)[:10]
    worst_skus = sorted(sku_rows, key=lambda r: r['profit'])[:10]

    # ── Wasted search terms yesterday ──────────────────────────────────────
    wasted_terms: dict[str, dict] = defaultdict(lambda:
        {'term': '', 'spend': 0.0, 'clicks': 0, 'campaigns': set()})
    for r in AdsSearchTermDailySnapshot.objects.filter(
        marketplace=marketplace, date=yday, orders_7d=0,
    ).values('search_term', 'spend', 'clicks', 'campaign_id'
    ).order_by('-spend')[:50]:
        b = wasted_terms[r['search_term']]
        b['term']  = r['search_term']
        b['spend'] += float(r['spend']  or 0)
        b['clicks'] += int(r['clicks']  or 0)
        b['campaigns'].add(r['campaign_id'])
    wasted_terms_out = [
        {'term': b['term'], 'spend': round(b['spend'], 2),
         'clicks': b['clicks'], 'campaign_count': len(b['campaigns'])}
        for b in sorted(wasted_terms.values(), key=lambda x: x['spend'], reverse=True)[:10]
        if b['spend'] >= 5
    ]

    # ── Open alerts ────────────────────────────────────────────────────────
    open_alerts = [
        {'severity': a.severity, 'category': a.category, 'title': a.title,
         'message': (a.message or '')[:280]}
        for a in Alert.objects.filter(
            marketplace=marketplace, is_resolved=False,
            created_at__gte=today_d - timedelta(days=14),
        ).order_by('-created_at')[:20]
    ]

    # ── BA share signal (most recent week) ─────────────────────────────────
    ba_week = BASearchQueryWeekly.objects.filter(
        marketplace=marketplace).aggregate(mx=Max('week_start'))['mx']
    ba_share = None
    if ba_week:
        agg = BASearchQueryWeekly.objects.filter(
            marketplace=marketplace, week_start=ba_week
        ).aggregate(
            avg_click=Avg('brand_click_share'),
            avg_purchase=Avg('brand_purchase_share'),
        )
        ba_share = {
            'week_start': ba_week.isoformat(),
            'avg_click_share':    round(float(agg['avg_click']    or 0), 2),
            'avg_purchase_share': round(float(agg['avg_purchase'] or 0), 2),
        }

    # ── Cross-sell — top 5 external Market Basket pairs ────────────────────
    our_asins = set(Product.objects.filter(
        marketplace=marketplace).values_list('asin', flat=True))
    mb_week = BAMarketBasketWeekly.objects.filter(
        marketplace=marketplace).aggregate(mx=Max('week_start'))['mx']
    cross_sell_signals: list = []
    if mb_week:
        rows = list(BAMarketBasketWeekly.objects.filter(
            marketplace=marketplace, week_start=mb_week,
        ).values('asin', 'purchased_asin', 'purchased_title', 'combination_pct')
         .order_by('-combination_pct')[:30])
        for r in rows:
            cross_sell_signals.append({
                'our_asin':      r['asin'],
                'with_asin':     r['purchased_asin'],
                'with_title':    (r['purchased_title'] or '')[:60],
                'is_internal':   r['purchased_asin'] in our_asins,
                'combo_pct':     round(float(r['combination_pct'] or 0) * 100, 2),
            })
            if len(cross_sell_signals) >= 10:
                break

    return {
        'marketplace':     marketplace,
        'reference_date':  yday.isoformat(),
        'day_pl':          day_pl,
        'day_pl_prev':     dby_pl,
        'day_deltas':      deltas,
        'week_pl':         week_pl,
        'week_pl_prev':    week_pl_prev,
        'top_campaigns':   top_campaigns,
        'worst_campaigns': worst_campaigns,
        'losing_campaigns': losing_campaigns,
        'top_skus':        top_skus,
        'worst_skus':      worst_skus,
        'wasted_terms':    wasted_terms_out,
        'open_alerts':     open_alerts,
        'ba_share':        ba_share,
        'cross_sell_signals': cross_sell_signals,
    }


def gather_campaign_brief(marketplace: str, campaign_id: str,
                           window_days: int = 7) -> dict:
    """Scoped data block for a single Campaign Detail page."""
    from .models import (
        CampaignProfitDaily, Campaign, PPCCampaignSnapshot,
        AdsAdvertisedProductDailySnapshot, AdsSearchTermDailySnapshot,
        DailyMetric,
    )
    from django.db.models import Sum, Avg

    today_d = date.today()
    end = today_d - timedelta(days=1)
    start = end - timedelta(days=window_days - 1)

    cname = (Campaign.objects.filter(marketplace=marketplace, campaign_id=campaign_id)
             .values_list('campaign_name', flat=True).first()
             or PPCCampaignSnapshot.objects.filter(
                 marketplace=marketplace, campaign_id=campaign_id
             ).order_by('-date').values_list('campaign_name', flat=True).first()
             or campaign_id)

    pl = CampaignProfitDaily.objects.filter(
        marketplace=marketplace, campaign_id=campaign_id,
        date__gte=start, date__lte=end,
    ).aggregate(
        spend=Sum('spend'), rev=Sum('ad_revenue'), profit=Sum('gross_profit'),
        margin=Avg('margin_pct'), roas=Avg('roas'), acos=Avg('acos'),
    )
    spend = float(pl['spend'] or 0); rev = float(pl['rev'] or 0)
    profit = float(pl['profit'] or 0)

    # Top advertised SKUs over the window
    top_asins = list(AdsAdvertisedProductDailySnapshot.objects.filter(
        marketplace=marketplace, campaign_id=campaign_id,
        date__gte=start, date__lte=end,
    ).values('asin', 'advertised_sku').annotate(
        sales=Sum('sales_7d'), units=Sum('units_7d')
    ).order_by('-sales')[:5])
    top_asins = [{'asin': r['asin'], 'sku': r['advertised_sku'],
                   'sales': round(float(r['sales'] or 0), 2),
                   'units': int(r['units'] or 0)} for r in top_asins]

    # Wasted terms in this campaign
    wasted = list(AdsSearchTermDailySnapshot.objects.filter(
        marketplace=marketplace, campaign_id=campaign_id,
        date__gte=start, date__lte=end, orders_7d=0,
    ).values('search_term').annotate(spend=Sum('spend')).order_by('-spend')[:5])
    wasted = [{'term': r['search_term'], 'spend': round(float(r['spend'] or 0), 2)}
               for r in wasted if float(r['spend'] or 0) > 5]

    # Total account spend for the window — used to compute the campaign's
    # share of total spend (an indicator of how much budget this campaign owns)
    acct_spend = DailyMetric.objects.filter(
        marketplace=marketplace, date__gte=start, date__lte=end
    ).aggregate(total=Sum('ppc_spend'))['total'] or 0

    return {
        'marketplace':   marketplace,
        'campaign_id':   campaign_id,
        'campaign_name': cname,
        'window':        {'start': start.isoformat(), 'end': end.isoformat(),
                           'days': window_days},
        'pl': {
            'spend':      round(spend, 2),
            'revenue':    round(rev,   2),
            'profit':     round(profit, 2),
            'margin_pct': round(float(pl['margin'] or 0), 2),
            'roas':       round(float(pl['roas']   or 0), 2),
            'acos':       round(float(pl['acos']   or 0), 2) * 100,
            'share_of_account_spend': round(spend / float(acct_spend) * 100, 2) if acct_spend else 0,
        },
        'top_advertised':    top_asins,
        'wasted_search_terms': wasted,
    }


def gather_sku_brief(marketplace: str, sku: str, window_days: int = 7) -> dict:
    """Scoped data block for a SKU Profitability AI commentary widget."""
    from .models import DailySkuSnapshot, SkuPpcAllocation, Product
    from django.db.models import Sum

    today_d = date.today()
    end = today_d - timedelta(days=1)
    start = end - timedelta(days=window_days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=window_days - 1)

    p = Product.objects.filter(marketplace=marketplace, sku=sku).only(
        'asin', 'title', 'brand').first()

    def _window(s: date, e: date) -> dict:
        agg = DailySkuSnapshot.objects.filter(
            marketplace=marketplace, sku=sku, date__gte=s, date__lte=e
        ).aggregate(rev=Sum('revenue'), qty=Sum('qty'),
                     cogs=Sum('cgs'), ref=Sum('amz_fee'), fba=Sum('fulfill'))
        ppc = SkuPpcAllocation.objects.filter(
            marketplace=marketplace, sku=sku, date__gte=s, date__lte=e
        ).aggregate(spend=Sum('sku_ppc_spend'))['spend'] or 0
        rev = float(agg['rev'] or 0); cogs = float(agg['cogs'] or 0)
        ref = float(agg['ref'] or 0); fba  = float(agg['fba'] or 0)
        ppc = float(ppc)
        profit = rev - ppc - ref - fba - cogs
        return {
            'start': s.isoformat(), 'end': e.isoformat(),
            'revenue': round(rev, 2), 'units': int(agg['qty'] or 0),
            'ppc':     round(ppc, 2),
            'cogs':    round(cogs, 2),
            'profit':  round(profit, 2),
            'margin_pct': round(profit / rev * 100, 2) if rev > 0 else 0.0,
            'tacos':      round(ppc    / rev * 100, 2) if rev > 0 else 0.0,
        }

    return {
        'marketplace': marketplace,
        'sku':         sku,
        'asin':        p.asin if p else '',
        'product_name': (p.title or '')[:80] if p else '',
        'brand':       p.brand if p else '',
        'this_window':  _window(start, end),
        'prior_window': _window(prev_start, prev_end),
    }
