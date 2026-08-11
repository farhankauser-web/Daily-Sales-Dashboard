"""
The money spine — the ads term aggregate every section reasons from.

Scoped by AD GROUP, resolved from the catalog via `mapping.py`, never by
campaign name. Where an ad group serves more than one product group its money
is apportioned by the group's weight; where it serves one (the norm) the weight
is 1.0 and the arithmetic is a plain filter.

Ratio discipline: ACOS/CTR/CVR/CPC are recomputed from SUMMED numerators and
denominators. The per-day ratio columns on the fact table must never be
averaged — a mean of daily ACOS is not the period ACOS, and the difference is
largest exactly where spend is lumpy.
"""
from django.db.models import Count, Max, Sum

from . import config as cfg


def _ratios(row: dict) -> dict:
    """Derive the rate metrics for one aggregated row, division-safe."""
    spend, sales = row['spend'], row['sales']
    clicks, imps, orders = row['clicks'], row['impressions'], row['orders']
    row['acos'] = (spend / sales) if sales > 0 else None
    row['roas'] = (sales / spend) if spend > 0 else None
    row['ctr']  = (clicks / imps) if imps > 0 else 0.0
    row['cvr']  = (orders / clicks) if clicks > 0 else 0.0
    row['cpc']  = (spend / clicks) if clicks > 0 else 0.0
    return row


def _empty_totals() -> dict:
    return _ratios({'spend': 0.0, 'sales': 0.0, 'clicks': 0, 'impressions': 0,
                    'orders': 0, 'units': 0})


def build(scope) -> dict:
    """
    Return {'terms': {hash: row}, 'totals': {...}} for the scope's ad groups.

    Counts (clicks, impressions, orders, units) are weighted alongside money so
    every derived rate stays internally consistent — a weighted spend over an
    unweighted click count would invent a CPC that never happened.
    """
    from ..models import AdsSearchTermDailySnapshot

    weights = scope.ad_group_weights
    if not weights:
        return {'terms': {}, 'totals': _empty_totals()}

    qs = (AdsSearchTermDailySnapshot.objects
          .filter(marketplace=scope.marketplace,
                  date__range=(scope.date_from, scope.date_to),
                  ad_group_id__in=list(weights.keys()))
          .values('search_term_hash', 'search_term', 'ad_group_id')
          .annotate(spend=Sum('spend'), sales=Sum('sales_7d'),
                    clicks=Sum('clicks'), impressions=Sum('impressions'),
                    orders=Sum('orders_7d'), units=Sum('units_7d'),
                    campaigns=Count('campaign_id', distinct=True)))

    terms = {}
    tot = {'spend': 0.0, 'sales': 0.0, 'clicks': 0.0, 'impressions': 0.0,
           'orders': 0.0, 'units': 0.0}

    for r in qs.iterator(chunk_size=2000):
        w = weights.get(r['ad_group_id'], 0.0)
        if w <= 0:
            continue
        vals = {
            'spend':       float(r['spend'] or 0) * w,
            'sales':       float(r['sales'] or 0) * w,
            'clicks':      float(r['clicks'] or 0) * w,
            'impressions': float(r['impressions'] or 0) * w,
            'orders':      float(r['orders'] or 0) * w,
            'units':       float(r['units'] or 0) * w,
        }
        for k in tot:
            tot[k] += vals[k]

        # Merge on hash. One term appears once per ad group, and the hash is
        # SHA1(lower(term)), so assigning instead of merging would discard
        # every occurrence but the last along with its spend.
        row = terms.get(r['search_term_hash'])
        if row:
            for k, v in vals.items():
                row[k] += v
            row['campaigns'] = max(row['campaigns'], int(r['campaigns'] or 0))
            _ratios(row)
        else:
            row = dict(vals)
            row.update({'hash': r['search_term_hash'], 'term': r['search_term'],
                        'campaigns': int(r['campaigns'] or 0)})
            terms[r['search_term_hash']] = _ratios(row)

    return {'terms': terms, 'totals': _ratios(tot)}


def campaign_breakdown(scope, term_hashes: list) -> dict:
    """
    {term_hash: [{campaign_id, campaign_name, spend, orders, match_type}, …]}

    Campaign identity is used HERE and only here — to name the campaign a user
    has to edit. That is the reporting/display use the scoping rule allows.
    Queried for the shortlist that reaches the UI, never for the whole spine.
    """
    from ..models import AdsSearchTermDailySnapshot, Campaign

    weights = scope.ad_group_weights
    if not term_hashes or not weights:
        return {}

    rows = (AdsSearchTermDailySnapshot.objects
            .filter(marketplace=scope.marketplace,
                    date__range=(scope.date_from, scope.date_to),
                    ad_group_id__in=list(weights.keys()),
                    search_term_hash__in=term_hashes)
            .values('search_term_hash', 'campaign_id', 'ad_group_id', 'match_type')
            .annotate(spend=Sum('spend'), orders=Sum('orders_7d')))

    # Names are best-effort: the campaign dimension has no UAE/KSA rows, so
    # those fall back to the id rather than blocking the recommendation.
    names = dict(Campaign.objects.filter(marketplace=scope.marketplace)
                 .values_list('campaign_id', 'campaign_name'))

    merged = {}
    for r in rows:
        w = weights.get(r['ad_group_id'], 0.0)
        if w <= 0:
            continue
        key = (r['search_term_hash'], r['campaign_id'], r['match_type'] or '—')
        e = merged.setdefault(key, {'spend': 0.0, 'orders': 0.0})
        e['spend'] += float(r['spend'] or 0) * w
        e['orders'] += float(r['orders'] or 0) * w

    out = {}
    for (h, cid, match), e in merged.items():
        out.setdefault(h, []).append({
            'campaign_id':   cid,
            'campaign_name': names.get(cid, cid),
            'match_type':    match,
            'spend':         e['spend'],
            'orders':        int(round(e['orders'])),
        })
    for k in out:
        out[k].sort(key=lambda d: -d['spend'])
    return out


def existing_exact_targets(scope) -> set:
    """
    Lower-cased keyword texts already targeted on EXACT match in this group's
    ad groups.

    Distinguishes "winning this term through broad match" (promote it to its
    own exact target) from "already targeted exactly" (no action available).
    """
    from ..models import AdsTargetingDailySnapshot

    if not scope.ad_group_weights:
        return set()

    rows = (AdsTargetingDailySnapshot.objects
            .filter(marketplace=scope.marketplace,
                    date__range=(scope.date_from, scope.date_to),
                    ad_group_id__in=scope.ad_group_ids,
                    match_type='exact').order_by()
            .values_list('expression', flat=True).distinct())
    return {(e or '').strip().lower() for e in rows if e}


def valuation_basis(scope) -> dict:
    """
    Contribution-margin rate and average selling price — what an opportunity is
    worth per unit of demand.

    DELIBERATELY NOT ON THE REPORT WINDOW (`MKT-D-012`). Both are structural
    properties of the product group, not period-sensitive levels: measured
    across three months the USA rate moved 30.3% → 30.1% → 28.7% and ASP sat at
    ~$36. Pricing an opportunity needs *the group's margin*, not this month's.

    Tying them to the report window is what broke: a 7-day report contained no
    settled revenue at all, so the rate silently fell back to a pessimistic
    constant and every value on the page was computed from it. A long trailing
    window ending at the newest settled data makes a sync lag irrelevant.

    THE MARGIN INVARIANT, and the trap inside it: the stored `cm` is ALREADY
    ex-VAT — `sync.py:356` computes it as `revenue_net − cgs − amz_fee −
    fulfill`. The revenue column beside it is gross. Dividing one by the other
    understates every VAT marketplace: UK reads 27.9% on a gross denominator
    against 33.5% on the correct ex-VAT one. USA is identical either way
    (net_factor 1.0), which is precisely how this class of error survives
    review — it is invisible in the marketplace people check most.
    """
    from datetime import timedelta

    from ..models import DailySkuSnapshot, Product
    from ..sync import net_factor

    fallback = {'cm_rate': cfg.FALLBACK_CM_RATE, 'asp': 0.0, 'observed': None,
                'start': None, 'end': None, 'as_of': None, 'days': 0,
                'skus_sold': 0, 'skus_total': len(scope.skus),
                'source': 'fallback', 'trusted': False}
    if not scope.skus:
        return fallback

    qs = DailySkuSnapshot.objects.filter(marketplace=scope.marketplace,
                                         sku__in=scope.skus)
    as_of = qs.aggregate(d=Max('date'))['d']
    if not as_of:
        return fallback

    start = as_of - timedelta(days=cfg.VALUATION_TRAILING_DAYS - 1)
    window = qs.filter(date__range=(start, as_of))

    agg = window.aggregate(rev=Sum('revenue'), cm=Sum('cm'), qty=Sum('qty'))
    gross = float(agg['rev'] or 0)
    units = int(agg['qty'] or 0)
    days = window.order_by().values('date').distinct().count()
    sold = window.filter(revenue__gt=0).order_by().values('sku').distinct().count()

    if gross <= 0 or days < cfg.VALUATION_MIN_DAYS:
        fallback.update({'as_of': as_of, 'days': days, 'skus_sold': sold})
        return fallback

    net_rev = gross * net_factor(scope.marketplace)
    rate = float(agg['cm'] or 0) / net_rev if net_rev > 0 else 0.0
    asp = gross / units if units else 0.0

    in_band = cfg.CM_RATE_MIN <= rate <= cfg.CM_RATE_MAX
    trusted = in_band and sold > 0

    if not trusted:
        asp_fallback = [float(p.selling_price) for p in
                        Product.objects.filter(marketplace=scope.marketplace,
                                               asin__in=scope.asins)
                        if p.selling_price]
        asp = asp or (sum(asp_fallback) / len(asp_fallback) if asp_fallback else 0.0)

    return {
        'cm_rate':  rate if trusted else cfg.FALLBACK_CM_RATE,
        'asp':      asp,
        'observed': rate,
        'start':    start, 'end': as_of, 'as_of': as_of, 'days': days,
        'skus_sold': sold, 'skus_total': len(scope.skus),
        'source':   'sku_actuals_trailing' if trusted else 'fallback',
        'trusted':  trusted,
    }


def group_revenue(scope) -> dict:
    """
    Total group revenue / units from DailySkuSnapshot — the denominator for
    paid share and group TACoS. Gross (display) revenue, per the convention
    that displayed revenue stays gross while margins are measured ex-VAT.
    """
    from ..models import DailySkuSnapshot

    if not scope.skus:
        return {'revenue': 0.0, 'units': 0, 'cm': 0.0, 'has_data': False}

    qs = DailySkuSnapshot.objects.filter(marketplace=scope.marketplace,
                                         sku__in=scope.skus)
    latest = qs.aggregate(d=Max('date'))['d']
    if not latest:
        return {'revenue': 0.0, 'units': 0, 'cm': 0.0, 'has_data': False,
                'start': None, 'end': None, 'days': 0}

    # This source lags advertising. Report the span it actually covers so the
    # one ratio that uses it (paid share) can divide over the same days —
    # arithmetic, not alignment (`MKT-D-012`).
    end = min(scope.date_to, latest)
    window = qs.filter(date__range=(scope.date_from, end))
    agg = window.aggregate(rev=Sum('revenue'), qty=Sum('qty'), cm=Sum('cm'))

    rev = float(agg['rev'] or 0)
    return {'revenue': rev, 'units': int(agg['qty'] or 0),
            'cm': float(agg['cm'] or 0), 'has_data': rev > 0,
            'start': scope.date_from, 'end': end,
            'days': window.order_by().values('date').distinct().count()}
