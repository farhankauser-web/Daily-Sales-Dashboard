"""
apps/sqp/ai_insights.py — Compress SQP data into structured context for Claude.

This is the ONLY module that touches both the LLM and the SQPSnapshot table.
Views and tasks call into here; they never talk to llm_client directly.

Key invariant: nothing larger than ~5 KB of JSON is ever sent to Claude. Raw
SQPSnapshot rows are pre-aggregated into:
  - KPI totals for each period (impressions, clicks, ATC, purchases + rates)
  - Top-N gainers / losers by volume Δ%
  - Top-N CTR-holes (high impressions, low CTR)
  - Top-N CVR-holes (high CTR, low CVR)
  - Top-N click-share movers
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from django.db.models import Sum

from .ai_cache import get_or_compute
from .models import SQPSnapshot
from .prompts import load_prompt
from .sync import iso_week_start, last_completed_iso_week

logger = logging.getLogger(__name__)

# Tunables (kept here so prompt engineering & token budgets are one knob each)
TOP_N_GAINERS   = 10
TOP_N_LOSERS    = 10
TOP_N_CTR_HOLES = 10
TOP_N_CVR_HOLES = 10
TOP_N_SHARE     = 10

# Thresholds that make a query "interesting" enough to send to the LLM
CTR_HOLE_MIN_IMPRESSIONS = 200    # need real exposure
CTR_HOLE_MAX_CTR         = 0.005  # ≤0.5% CTR
CVR_HOLE_MIN_CLICKS      = 50     # need real clicks
CVR_HOLE_MAX_CVR         = 0.01   # ≤1.0% CVR


# ─────────────────────────────────────────────────────────────────────────────
# Period resolution
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Period:
    period_type: str   # 'WEEK', 'MONTH', 'YEAR'
    start: date
    end:   date

    def label(self) -> str:
        if self.period_type == 'WEEK':
            iso = self.start.isocalendar()
            return f'{iso.year}-W{iso.week:02d}'
        if self.period_type == 'MONTH':
            return self.start.strftime('%Y-%m')
        return self.start.strftime('%Y')


COMPARISONS = ('wow', 'mom', 'yoy')


def resolve_comparison(comparison: str, anchor: date | None = None) -> tuple[Period, Period]:
    """
    Returns (period_a, period_b) where A is the more recent period and B the older.
    'wow' → last completed ISO week vs the week before
    'mom' → last completed calendar month vs the month before
    'yoy' → same calendar month one year ago vs current latest completed month
    """
    if comparison not in COMPARISONS:
        raise ValueError(f"comparison must be one of {COMPARISONS}, got {comparison!r}")

    anchor = anchor or date.today()
    if comparison == 'wow':
        a_mon, a_sun = last_completed_iso_week(anchor)
        b_mon = a_mon - timedelta(weeks=1)
        b_sun = b_mon + timedelta(days=6)
        return (Period('WEEK', a_mon, a_sun),
                Period('WEEK', b_mon, b_sun))

    if comparison == 'mom':
        last_day_a = anchor.replace(day=1) - timedelta(days=1)
        a_start = last_day_a.replace(day=1)
        last_day_b = a_start - timedelta(days=1)
        b_start = last_day_b.replace(day=1)
        return (Period('MONTH', a_start, last_day_a),
                Period('MONTH', b_start, last_day_b))

    # yoy: last completed month this year vs same month last year
    last_day_a = anchor.replace(day=1) - timedelta(days=1)
    a_start = last_day_a.replace(day=1)
    b_start = a_start.replace(year=a_start.year - 1)
    if b_start.month == 12:
        b_end = date(b_start.year + 1, 1, 1) - timedelta(days=1)
    else:
        b_end = date(b_start.year, b_start.month + 1, 1) - timedelta(days=1)
    return (Period('MONTH', a_start, last_day_a),
            Period('MONTH', b_start, b_end))


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────
def _scope_qs(marketplace: str, period: Period, asin: str | None):
    """Base queryset for one (marketplace, asin, period) scope."""
    qs = SQPSnapshot.objects.filter(
        marketplace  = marketplace,
        period_type  = period.period_type,
        period_start = period.start,
    ).select_related('query')
    if asin:
        qs = qs.filter(asin=asin.upper())
    return qs


def _totals(qs) -> dict:
    agg = qs.aggregate(
        impressions = Sum('impressions_total'),
        clicks      = Sum('clicks_total'),
        atc         = Sum('atc_total'),
        purchases   = Sum('purchases_total'),
        volume      = Sum('search_query_volume'),
    )
    impressions = int(agg['impressions'] or 0)
    clicks      = int(agg['clicks']      or 0)
    atc         = int(agg['atc']         or 0)
    purchases   = int(agg['purchases']   or 0)
    return {
        'impressions':       impressions,
        'clicks':            clicks,
        'atc':               atc,
        'purchases':         purchases,
        'search_volume':     int(agg['volume'] or 0),
        'ctr_pct':           round((clicks / impressions * 100) if impressions else 0, 3),
        'atc_rate_pct':      round((atc / clicks * 100)         if clicks       else 0, 3),
        'cvr_pct':           round((purchases / clicks * 100)   if clicks       else 0, 3),
        'unique_queries':    qs.count(),
    }


def _delta(a: float, b: float) -> dict:
    """{'a':…, 'b':…, 'delta':…, 'delta_pct':…}; safe when b==0."""
    a = float(a or 0)
    b = float(b or 0)
    delta = a - b
    dp = (delta / b * 100) if b else (None if a == 0 else 100.0)
    return {'a': a, 'b': b, 'delta': delta,
            'delta_pct': round(dp, 2) if dp is not None else None}


def _per_query_map(qs) -> dict[int, dict]:
    """{query_id: snapshot_row_dict} so we can diff across periods."""
    out = {}
    for s in qs:
        out[s.query_id] = {
            'query':         s.query.text,
            'volume':        int(s.search_query_volume),
            'impressions':   int(s.impressions_total),
            'clicks':        int(s.clicks_total),
            'atc':           int(s.atc_total),
            'purchases':     int(s.purchases_total),
            'ctr':           float(s.click_rate),
            'cvr':           float(s.purchase_rate),
            'clicks_share':  float(s.clicks_asin_share),
            'purch_share':   float(s.purchases_asin_share),
        }
    return out


def _round_query_row(d: dict) -> dict:
    """Round before sending to Claude — saves tokens, doesn't lose meaning."""
    return {
        'query':           d['query'],
        'a_volume':        d.get('a_volume', d.get('volume')),
        'b_volume':        d.get('b_volume'),
        'volume_delta_pct': d.get('volume_delta_pct'),
        'a_impressions':   d.get('a_impressions'),
        'a_clicks':        d.get('a_clicks'),
        'a_ctr_pct':       round((d.get('a_ctr') or 0) * 100, 3),
        'a_cvr_pct':       round((d.get('a_cvr') or 0) * 100, 3),
        'a_clicks_share_pct': round((d.get('a_clicks_share') or 0) * 100, 2),
    }


def _build_query_diffs(map_a: dict, map_b: dict) -> list[dict]:
    """Flatten union of queries across both periods with delta_pct on volume."""
    out = []
    all_ids = set(map_a) | set(map_b)
    for qid in all_ids:
        a = map_a.get(qid, {})
        b = map_b.get(qid, {})
        a_vol = a.get('volume', 0)
        b_vol = b.get('volume', 0)
        if b_vol:
            dp = (a_vol - b_vol) / b_vol * 100
        elif a_vol:
            dp = 100.0
        else:
            continue
        out.append({
            'query':              a.get('query') or b.get('query'),
            'a_volume':           a_vol,
            'b_volume':           b_vol,
            'volume_delta_pct':   round(dp, 2),
            'a_impressions':      a.get('impressions', 0),
            'a_clicks':           a.get('clicks', 0),
            'a_ctr':              a.get('ctr', 0),
            'a_cvr':              a.get('cvr', 0),
            'a_clicks_share':     a.get('clicks_share', 0),
            'a_purch_share':      a.get('purch_share', 0),
            'b_clicks_share':     b.get('clicks_share', 0),
        })
    return out


def aggregate_asin_comparison(
    marketplace: str,
    asin:        str | None,
    period_a:    Period,
    period_b:    Period,
) -> dict:
    """
    Build the compressed context dict the LLM consumes.
    Stays under ~5 KB JSON for typical brands.
    """
    qs_a = _scope_qs(marketplace, period_a, asin)
    qs_b = _scope_qs(marketplace, period_b, asin)

    totals_a = _totals(qs_a)
    totals_b = _totals(qs_b)

    map_a = _per_query_map(qs_a)
    map_b = _per_query_map(qs_b)
    diffs = _build_query_diffs(map_a, map_b)

    # Top gainers / losers by volume delta %
    by_growth = sorted(diffs, key=lambda r: r['volume_delta_pct'], reverse=True)
    gainers = [_round_query_row(r) for r in by_growth[:TOP_N_GAINERS]
               if r['a_volume'] >= 100]
    losers  = [_round_query_row(r) for r in reversed(by_growth[-TOP_N_LOSERS:])
               if r['b_volume'] >= 100]

    # CTR holes — current period only
    ctr_holes = sorted(
        (r for r in diffs
         if r['a_impressions'] >= CTR_HOLE_MIN_IMPRESSIONS and r['a_ctr'] <= CTR_HOLE_MAX_CTR),
        key=lambda r: r['a_impressions'], reverse=True,
    )
    ctr_holes_out = [_round_query_row(r) for r in ctr_holes[:TOP_N_CTR_HOLES]]

    # CVR holes — current period
    cvr_holes = sorted(
        (r for r in diffs
         if r['a_clicks'] >= CVR_HOLE_MIN_CLICKS and r['a_cvr'] <= CVR_HOLE_MAX_CVR),
        key=lambda r: r['a_clicks'], reverse=True,
    )
    cvr_holes_out = [_round_query_row(r) for r in cvr_holes[:TOP_N_CVR_HOLES]]

    # Share movers — biggest changes in click_share between B → A
    def share_delta(r):
        return (r.get('a_clicks_share', 0) - r.get('b_clicks_share', 0))
    share_movers = sorted(diffs, key=share_delta, reverse=True)
    share_up   = [{'query': r['query'],
                   'b_share_pct': round(r['b_clicks_share'] * 100, 2),
                   'a_share_pct': round(r['a_clicks_share'] * 100, 2)}
                  for r in share_movers[:TOP_N_SHARE]]
    share_down = [{'query': r['query'],
                   'b_share_pct': round(r['b_clicks_share'] * 100, 2),
                   'a_share_pct': round(r['a_clicks_share'] * 100, 2)}
                  for r in list(reversed(share_movers))[:TOP_N_SHARE]]

    context = {
        'scope': {
            'marketplace':  marketplace,
            'asin':         asin or 'BRAND',
            'period_a':     {'type': period_a.period_type,
                             'start': period_a.start.isoformat(),
                             'end':   period_a.end.isoformat(),
                             'label': period_a.label()},
            'period_b':     {'type': period_b.period_type,
                             'start': period_b.start.isoformat(),
                             'end':   period_b.end.isoformat(),
                             'label': period_b.label()},
        },
        'totals': {
            'a': totals_a,
            'b': totals_b,
        },
        'deltas': {
            'impressions':   _delta(totals_a['impressions'],   totals_b['impressions']),
            'clicks':        _delta(totals_a['clicks'],        totals_b['clicks']),
            'atc':           _delta(totals_a['atc'],           totals_b['atc']),
            'purchases':     _delta(totals_a['purchases'],     totals_b['purchases']),
            'search_volume': _delta(totals_a['search_volume'], totals_b['search_volume']),
            'ctr_pp':        round(totals_a['ctr_pct']        - totals_b['ctr_pct'],      3),
            'atc_rate_pp':   round(totals_a['atc_rate_pct']   - totals_b['atc_rate_pct'], 3),
            'cvr_pp':        round(totals_a['cvr_pct']        - totals_b['cvr_pct'],      3),
        },
        'top_gainers':       gainers,
        'top_losers':        losers,
        'ctr_holes':         ctr_holes_out,
        'cvr_holes':         cvr_holes_out,
        'share_winners':     share_up,
        'share_losers':      share_down,
    }
    return context


# ─────────────────────────────────────────────────────────────────────────────
# Public orchestration entrypoint
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class InsightResponse:
    summary:              str
    key_findings:         list
    risks:                list
    opportunities:        list
    recommended_actions:  list
    priority_score:       int
    meta:                 dict    # cache_hit, model, tokens, generated_at, …
    context:              dict    # the aggregated input (for debug/UI)

    def to_dict(self) -> dict:
        return {
            'summary':              self.summary,
            'key_findings':         self.key_findings,
            'risks':                self.risks,
            'opportunities':        self.opportunities,
            'recommended_actions':  self.recommended_actions,
            'priority_score':       self.priority_score,
            'meta':                 self.meta,
            'context':              self.context,
        }


def _empty_response(reason: str) -> dict:
    return {
        'summary': reason,
        'key_findings': [],
        'risks': [],
        'opportunities': [],
        'recommended_actions': [],
        'priority_score': 0,
    }


def analyze_asin(
    marketplace:   str,
    asin:          str | None,
    comparison:    str = 'wow',
    *,
    user           = None,
    force_refresh: bool = False,
    anchor:        date | None = None,
) -> InsightResponse:
    """
    End-to-end ASIN analysis. Aggregates → caches → calls Claude → parses.
    `asin` may be None to run a brand-level analysis.
    """
    period_a, period_b = resolve_comparison(comparison, anchor=anchor)
    context = aggregate_asin_comparison(marketplace, asin, period_a, period_b)

    # Skip the LLM call entirely if there's nothing to analyse
    if context['totals']['a']['unique_queries'] == 0 and context['totals']['b']['unique_queries'] == 0:
        empty = _empty_response(
            f"No SQP data for {marketplace.upper()} · {asin or 'BRAND'} in either "
            f"{period_a.label()} or {period_b.label()}. Sync the relevant weeks first."
        )
        return InsightResponse(
            summary=empty['summary'],
            key_findings=empty['key_findings'],
            risks=empty['risks'],
            opportunities=empty['opportunities'],
            recommended_actions=empty['recommended_actions'],
            priority_score=empty['priority_score'],
            meta={'cache_hit': False, 'skipped': 'no_data',
                  'generated_at': None, 'model': None,
                  'input_tokens': 0, 'output_tokens': 0, 'latency_ms': 0},
            context=context,
        )

    context_json = json.dumps(context, separators=(',', ':'))
    system, user_msg = load_prompt('asin_analysis', context_json=context_json)

    period_label = f'{comparison.upper()} {period_a.label()} vs {period_b.label()}'
    parsed, meta = get_or_compute(
        insight_type   = 'asin_analysis',
        marketplace    = marketplace,
        asin           = asin or '',
        period_label   = period_label,
        context        = context,
        system_prompt  = system,
        user_message   = user_msg,
        user           = user,
        force_refresh  = force_refresh,
        max_tokens     = 2000,
    )

    return InsightResponse(
        summary             = (parsed.get('summary') or '').strip(),
        key_findings        = list(parsed.get('key_findings') or []),
        risks               = list(parsed.get('risks') or []),
        opportunities       = list(parsed.get('opportunities') or []),
        recommended_actions = list(parsed.get('recommended_actions') or []),
        priority_score      = int(parsed.get('priority_score') or 0),
        meta                = meta,
        context             = context,
    )
