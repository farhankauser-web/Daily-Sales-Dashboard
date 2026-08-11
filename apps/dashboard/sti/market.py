"""
Brand Analytics join — market size and our share of it.

THE UNLOCK (v2 §1): SQP reports MARKET-TOTAL funnel counts per query per week
(`clicks_total`, `cart_adds_total`, `purchases_total`) next to our own ASIN
counts. That is a direct measurement of share, not a rank proxy. Every market
number in the Center comes from these fields — no scraping, no third-party data.

THE SECOND CLOCK (v2 §1.4): SQP is weekly and lands ~T-3 after the week closes,
while ads data is daily. This module never date-matches the two. It derives its
own window (the latest complete weeks ending on or before the ads range end),
reports that window separately, and joins to the ads spine BY HASH — both
tables store SHA1(lower(text)), so no date alignment is needed to join at all.

AGGREGATION TRAP, handled here: SQP rows are (week × ASIN × query). The
query-level totals REPEAT on every ASIN row for that query. Summing them would
multiply the market by the number of our ASINs that surfaced. So query-level
fields are taken with Max within a week; only our own ASIN counts are summed.
"""
from django.db.models import Max, Sum

from . import config as cfg


def resolve_window(marketplace: str, asins: list, period) -> dict:
    """
    The Brand Analytics weeks belonging to the SELECTED period — not the latest
    ones available.

    This is the point of running on Amazon's grid. The selector offers Sunday-
    start weeks, which is exactly how Brand Analytics publishes, so a week the
    user picks either has market data or does not — there is no third case where
    an older week gets quietly paired with newer advertising. Before this, the
    resolver took "the most recent weeks up to the range end", which cheerfully
    matched a May market read to an August ad window and left the staleness to a
    banner. Matching by construction is better than labelling a mismatch.

    A weekly period maps to its own week. A monthly period maps to the weeks
    whose week_end falls inside it, so every week counts once and none is split.
    """
    from ..models import BASearchQueryWeekly

    empty = {'weeks': [], 'count': 0, 'start': None, 'end': None,
             'has_data': False, 'can_trend': False, 'stale': False,
             'staleness_days': None}
    if not asins or period is None:
        return empty

    weeks = sorted(
        BASearchQueryWeekly.objects
        .filter(marketplace=marketplace, asin__in=asins,
                week_start__gte=period.start, week_end__lte=period.end)
        .order_by().values_list('week_start', flat=True).distinct()
    )
    if not weeks:
        return empty

    return {
        'weeks':     weeks,
        'count':     len(weeks),
        'start':     weeks[0],
        'end':       weeks[-1],
        'has_data':  True,
        # A trend needs several comparable weeks INSIDE the period, which only a
        # monthly or longer period can supply. A single week has no trend, and
        # saying so is more useful than implying one.
        'can_trend': len(weeks) >= cfg.BA_WEEKS_FOR_TREND,
        'stale':          False,     # impossible now: the week is the period
        'staleness_days': 0,
    }


def _intensity(top_clicked) -> float | None:
    """
    Competitive intensity = share of clicks held by the top-3 ASINs (v2 §7.3).

    Concentrated → entrenched incumbents → harder to take share. Amazon gives
    us only the top 3, which is why this is a floor on concentration, not a
    full HHI. Stated on the page rather than dressed up as precision.
    """
    if not top_clicked:
        return None
    total = 0.0
    for item in top_clicked:
        if not isinstance(item, dict):
            continue
        v = item.get('click_share') or item.get('clickShare') or 0
        try:
            total += float(v)
        except (TypeError, ValueError):
            continue
    if total <= 0:
        return None
    # Amazon reports these as percentages; normalise defensively in case a
    # future ingest writes fractions.
    return min(total / 100.0 if total > 1.5 else total, 1.0)


def build(scope, window: dict) -> dict:
    """
    Return {'queries': {hash: row}, 'weeks': n, 'totals': {...}}.

    Each row carries, as WEEKLY RATES so any window is comparable:
        market_purchases_wk, market_clicks_wk, volume_wk,
        our_purchases_wk, our_clicks_wk, share (0-1), click_share (0-1),
        intensity (0-1|None), top_clicked (list), weeks_seen
    """
    from ..models import BASearchQueryWeekly

    empty = {'queries': {}, 'weeks': 0,
             'totals': {'market_purchases_wk': 0.0, 'our_purchases_wk': 0.0, 'share': 0.0}}
    if not window['has_data'] or not scope.asins:
        return empty

    rows = (BASearchQueryWeekly.objects
            .filter(marketplace=scope.marketplace, asin__in=scope.asins,
                    week_start__in=window['weeks'])
            .values('search_query_hash', 'search_query', 'week_start')
            .annotate(volume=Max('search_query_volume'),
                      mkt_purchases=Max('purchases_total'),
                      mkt_clicks=Max('clicks_total'),
                      mkt_cart_adds=Max('cart_adds_total'),
                      rank=Max('search_query_score'),
                      our_purchases=Sum('asin_purchase_count'),
                      our_clicks=Sum('asin_click_count'),
                      our_impressions=Sum('asin_impression_count')))

    # Step 1 — per (query, week) is what the ORM just gave us. Roll up to query.
    acc: dict = {}
    for r in rows:
        h = r['search_query_hash']
        a = acc.setdefault(h, {
            'hash': h, 'query': r['search_query'], 'weeks_seen': 0,
            'volume': 0, 'mkt_purchases': 0, 'mkt_clicks': 0, 'mkt_cart_adds': 0,
            'our_purchases': 0, 'our_clicks': 0, 'our_impressions': 0,
            'rank_best': None, 'weekly_share': [],
        })
        a['weeks_seen'] += 1
        a['volume']        += int(r['volume'] or 0)
        a['mkt_purchases'] += int(r['mkt_purchases'] or 0)
        a['mkt_clicks']    += int(r['mkt_clicks'] or 0)
        a['mkt_cart_adds'] += int(r['mkt_cart_adds'] or 0)
        a['our_purchases'] += int(r['our_purchases'] or 0)
        a['our_clicks']    += int(r['our_clicks'] or 0)
        a['our_impressions'] += int(r['our_impressions'] or 0)
        rank = int(r['rank'] or 0)
        if rank and (a['rank_best'] is None or rank < a['rank_best']):
            a['rank_best'] = rank
        mp = int(r['mkt_purchases'] or 0)
        if mp > 0:
            a['weekly_share'].append((r['week_start'], int(r['our_purchases'] or 0) / mp))

    # Competitive intensity + top-3 needs the JSON column: fetch once for the
    # most recent week only, since the incumbent set barely moves week to week.
    latest = window['weeks'][-1]
    top_rows = (BASearchQueryWeekly.objects
                .filter(marketplace=scope.marketplace, asin__in=scope.asins,
                        week_start=latest)
                .values('search_query_hash', 'top_clicked_asins'))
    tops = {}
    for r in top_rows:
        if r['search_query_hash'] not in tops and r['top_clicked_asins']:
            tops[r['search_query_hash']] = r['top_clicked_asins']

    n_weeks = max(window['count'], 1)
    queries, tot_mkt, tot_ours = {}, 0.0, 0.0

    for h, a in acc.items():
        mkt_p_wk = a['mkt_purchases'] / n_weeks
        our_p_wk = a['our_purchases'] / n_weeks
        share = (a['our_purchases'] / a['mkt_purchases']) if a['mkt_purchases'] > 0 else 0.0
        cshare = (a['our_clicks'] / a['mkt_clicks']) if a['mkt_clicks'] > 0 else 0.0

        trend = None
        if len(a['weekly_share']) >= cfg.BA_WEEKS_FOR_TREND:
            ordered = [s for _, s in sorted(a['weekly_share'])]
            half = len(ordered) // 2
            first, second = ordered[:half], ordered[half:]
            if first and second:
                f, s = sum(first) / len(first), sum(second) / len(second)
                trend = s - f

        top_clicked = tops.get(h) or []
        queries[h] = {
            'hash': h, 'query': a['query'],
            'volume_wk':            a['volume'] / n_weeks,
            'market_purchases_wk':  mkt_p_wk,
            'market_clicks_wk':     a['mkt_clicks'] / n_weeks,
            'our_purchases_wk':     our_p_wk,
            'our_clicks_wk':        a['our_clicks'] / n_weeks,
            'share':                share,
            'click_share':          cshare,
            'share_trend':          trend,
            'rank':                 a['rank_best'],
            'intensity':            _intensity(top_clicked),
            'top_clicked':          top_clicked[:3],
            'weeks_seen':           a['weeks_seen'],
            # Market funnel benchmark — where we lose: visibility, click appeal,
            # or conversion. market CVR here is click→purchase.
            'market_cvr': (a['mkt_purchases'] / a['mkt_clicks']) if a['mkt_clicks'] > 0 else 0.0,
            'our_cvr':    (a['our_purchases'] / a['our_clicks']) if a['our_clicks'] > 0 else 0.0,
        }
        tot_mkt += mkt_p_wk
        tot_ours += our_p_wk

    return {
        'queries': queries,
        'weeks':   window['count'],
        'totals': {
            'market_purchases_wk': tot_mkt,
            'our_purchases_wk':    tot_ours,
            'share':               (tot_ours / tot_mkt) if tot_mkt > 0 else 0.0,
        },
    }


def attainable_share(node_share: float, sibling_shares: list) -> float:
    """
    The share ceiling used for headroom (v2 §7.2).

    NOT (100% − current share). The ceiling is the best share we ALREADY
    achieve on comparable pools — evidence, not ambition. Two guards:
      · a floor, so a pool where we are invisible still gets a modest case;
      · a cap at MAX_SHARE_MULTIPLE × current, because tripling share in one
        planning cycle is already an aggressive claim.
    """
    usable = [s for s in sibling_shares if s and s > 0]
    ceiling = max(usable) if usable else cfg.FALLBACK_ATTAINABLE_SHARE
    ceiling = max(ceiling, cfg.FALLBACK_ATTAINABLE_SHARE)
    if node_share > 0:
        ceiling = min(ceiling, node_share * cfg.MAX_SHARE_MULTIPLE)
    return max(ceiling, node_share)


def average_selling_price(scope, revenue: dict) -> float:
    """
    ASP for converting unit demand into money. Prefers what we actually sold in
    the window; falls back to catalog price when the window has no sales.
    """
    from ..models import Product

    if revenue.get('units'):
        return revenue['revenue'] / revenue['units']

    prices = [float(p.selling_price) for p in
              Product.objects.filter(marketplace=scope.marketplace, asin__in=scope.asins)
              if p.selling_price]
    return (sum(prices) / len(prices)) if prices else 0.0
