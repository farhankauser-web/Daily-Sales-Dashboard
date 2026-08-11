"""
Opportunity generators — one small function per type.

Every generator returns a list of plain dicts in the same shape, so the runner
persists them uniformly and the UI renders them uniformly. No generator writes
to the database and none of them reads config values inline — thresholds all
come from `config.py`, so the whole business policy is reviewable in one file.

Money convention: every value is per MONTH in the marketplace's own currency,
and `score` is expected CONTRIBUTION MARGIN per month measured on revenue
ex-VAT. That is what makes a negative-keyword saving and a product launch
comparable on one ranked board.

Disjointness (v2 §9 dedupe rule) is designed in, not patched afterwards:
  · ads-side  — defend (orders = 0) | scale_ppc (orders >= floor, low ACOS)
                | listing_fix (orders > 0, clicks convert poorly)
  · BA-side   — partitioned by share band and by whether we sell the type at
                all, so organic_push / capture_share / product_gap cannot both
                fire on one query.
"""
import hashlib

from . import config as cfg
from . import scoring
from .taxonomy import PRODUCT_TYPE_LABELS


def make_key(opp_type: str, group_id: int, marketplace: str, subject: str) -> str:
    """
    Stable identity across runs — the reason history and outcome tracking work
    at all. Same opportunity, same key, every week, until it is closed.
    """
    raw = f'{opp_type}|{group_id}|{marketplace}|{(subject or "").strip().lower()}'
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _shell(opp_type, scope, subject, title, why, headroom, win_prob, factors,
           margin_factor, evidence, actions, dependencies,
           conf='low', blocked='', unmet=0):
    """Assemble one opportunity dict. Every generator ends here."""
    value = scoring.score(headroom, win_prob, margin_factor)
    return {
        'key':             make_key(opp_type, scope.group_id, scope.marketplace, subject),
        'opp_type':        opp_type,
        'subject':         subject,
        'title':           title,
        'why':             why,
        'score':           0.0 if blocked else value,
        'score_if_unblocked': value,
        'headroom_value':  headroom,
        'win_probability': win_prob,
        'margin_factor':   margin_factor,
        'factors':         factors,
        'difficulty':      scoring.difficulty(opp_type, unmet),
        'confidence':      conf,
        'blocked_reason':  blocked,
        'evidence':        evidence,
        'required_actions': actions,
        'dependencies':    dependencies,
        'timeline':        cfg.TIMELINE.get(opp_type, ''),
    }


# ═══════════════════════════════════════════════════════════════════════════
# ADS SIDE — needs only the term spine, so these work with no Brand Analytics
# ═══════════════════════════════════════════════════════════════════════════

def defend_waste(ctx) -> list:
    """
    Spend with nothing to show for it.

    A saved dollar IS a contribution-margin dollar, so margin_factor is 1.0 —
    this is the one type whose money needs no margin assumption, which is why
    it is usually the most trustworthy item on the board.
    """
    scope, spine, tiers = ctx['scope'], ctx['spine'], ctx['tiers']
    out = []
    tail = []

    for h, t in spine['terms'].items():
        if t['orders'] > 0:
            continue
        if t['spend'] < cfg.MIN_SPEND_FOR_WASTE or t['clicks'] < cfg.MIN_CLICKS_FOR_WASTE:
            if t['spend'] > 0:
                tail.append((h, t))
            continue

        monthly = t['spend'] * ctx['monthly_factor']
        tier = tiers.get(h, '')
        off_category = tier in ('off_category', 'adjacent')

        camps = ctx['campaign_map'].get(h, [])
        where = camps[0]['campaign_name'] if camps else '—'
        # Off-category terms deserve a negative; on-category ones may just need
        # a lower bid — the same evidence, two different decisions.
        verb = 'Add as negative keyword' if off_category else 'Reduce bid or pause'

        # Execution is near-certain (we control the bid), so probability is
        # high but never 1.0 — Amazon match types leak.
        factors = {'execution': 0.9}
        prob, _ = scoring.win_probability(**factors)

        out.append(_shell(
            'defend', scope, t['term'],
            title=f'{verb}: "{t["term"]}"',
            why=(f'{ctx["cur"]}{t["spend"]:,.0f} spent on {t["clicks"]:,.0f} clicks with zero '
                 f'orders over {scope.days} days'
                 + (f'. Classified {tier.replace("_", " ")} — not what this group sells.'
                    if off_category else '.')),
            headroom=monthly, win_prob=prob, factors=factors, margin_factor=1.0,
            evidence={
                'spend': t['spend'], 'clicks': t['clicks'], 'orders': 0,
                'impressions': t['impressions'], 'cpc': t['cpc'], 'ctr': t['ctr'],
                'tier': tier, 'campaigns': camps[:3],
            },
            actions=[{'domain': 'ppc', 'text': f'{verb} "{t["term"]}" in {where}'}],
            dependencies=[],
            conf=scoring.confidence(clicks=t['clicks'], orders=0,
                                    ba_weeks=ctx['ba_weeks'], margin_trusted=True),
        ))

    out = sorted(out, key=lambda o: -o['score'])[:cfg.MAX_OPPORTUNITIES_PER_TYPE]

    tail_case = _long_tail_waste(ctx, tail)
    if tail_case:
        out.append(tail_case)
    return sorted(out, key=lambda o: -o['score'])


def _long_tail_waste(ctx, tail: list):
    """
    The waste no single term can show you.

    Verified on real data during build: 8,323 of this group's terms had zero
    orders, together burning far more than every above-floor term combined,
    yet each one individually sits under the action threshold. Reporting only
    per-term waste would have shown a $36 card while thousands leaked past it.
    No negative keyword fixes this — it is a targeting-structure problem, so
    the action is written accordingly.
    """
    scope = ctx['scope']
    if not tail:
        return None

    spend = sum(t['spend'] for _, t in tail)
    clicks = sum(t['clicks'] for _, t in tail)
    monthly = spend * ctx['monthly_factor']
    if monthly < cfg.MIN_SPEND_FOR_WASTE * 3:
        return None

    # Attribute as much of the tail as the campaign map covers, so the action
    # names real campaigns instead of gesturing at "your account".
    by_campaign: dict = {}
    for h, t in tail:
        for c in ctx['campaign_map'].get(h, []):
            e = by_campaign.setdefault(c['campaign_id'],
                                       {'name': c['campaign_name'], 'spend': 0.0, 'terms': 0})
            e['spend'] += c['spend']
            e['terms'] += 1
    worst = sorted(by_campaign.values(), key=lambda e: -e['spend'])[:5]

    off_cat = sum(t['spend'] for h, t in tail
                  if ctx['tiers'].get(h) in ('off_category', 'adjacent'))

    factors = {'execution': 0.5}     # structural fixes are slower and partial
    prob, _ = scoring.win_probability(**factors)

    return _shell(
        'defend', scope, '__long_tail__',
        title=f'Long-tail waste: {len(tail):,} terms with clicks and no orders',
        why=(f'{len(tail):,} search terms spent {ctx["cur"]}{spend:,.0f} across {clicks:,.0f} '
             f'clicks and produced no orders. Each is individually below the '
             f'{ctx["cur"]}{cfg.MIN_SPEND_FOR_WASTE:.0f} action floor, so no negative keyword '
             f'reaches them — together they are the largest single leak in this group. '
             f'This is a targeting-structure problem, not a keyword one.'),
        headroom=monthly, win_prob=prob, factors=factors, margin_factor=1.0,
        evidence={
            'spend': spend, 'clicks': clicks, 'orders': 0,
            'terms': len(tail),
            'off_category_spend': off_cat,
            'worst_campaigns': worst,
            'floor': cfg.MIN_SPEND_FOR_WASTE,
        },
        actions=[
            {'domain': 'ppc', 'text': 'Tighten auto and broad targeting — the tail is where '
                                      'loose match types spend'},
            {'domain': 'ppc', 'text': 'Add negative phrases (not exact) to catch the tail by pattern'},
            {'domain': 'ppc', 'text': 'Review the campaigns listed below for match-type mix'},
        ],
        dependencies=[],
        conf='high' if clicks >= cfg.CONFIDENCE_HIGH_CLICKS else 'medium',
    )


def scale_ppc(ctx) -> list:
    """
    Terms converting well below the group's ACOS — room to buy more of the
    same demand.

    Headroom model: the term could absorb more spend until its ACOS reaches
    the group average. Incremental revenue is capped at 100% growth, because
    doubling a term in one cycle is already an aggressive claim.

    Margin model: the MARGINAL rate is `cm_rate − term_acos` — the extra
    revenue still has to pay for its own advertising. A term whose ACOS
    already exceeds the group's contribution margin produces nothing and is
    filtered out rather than shown with a flattering number.
    """
    scope, spine = ctx['scope'], ctx['spine']
    group = spine['totals']
    group_acos = group['acos']
    inv = ctx['inventory']
    out = []

    if not group_acos:
        return out

    for h, t in spine['terms'].items():
        if t['orders'] < cfg.MIN_ORDERS_FOR_SCALE or not t['acos']:
            continue
        if t['acos'] > group_acos * cfg.SCALE_ACOS_RATIO:
            continue

        marginal_rate = ctx['cm_rate'] - t['acos']
        if marginal_rate <= 0:
            continue

        growth = min(1.0, (group_acos / t['acos']) - 1.0)
        headroom = t['sales'] * growth * ctx['monthly_factor']
        if headroom <= 0:
            continue

        q = ctx['queries_by_hash'].get(h)
        factors = {
            'conversion': scoring.conversion_factor(t['cvr'], group['cvr'], t['clicks']),
            'readiness':  scoring.readiness_factor(inv, needs_stock=True),
            'momentum':   scoring.momentum_factor(q['share_trend'] if q else None,
                                                  ctx['can_trend']),
        }
        if q:
            factors['intensity'] = scoring.intensity_factor(q['intensity'])
        prob, _ = scoring.win_probability(**factors)

        camps = ctx['campaign_map'].get(h, [])
        where = camps[0]['campaign_name'] if camps else '—'
        has_exact = t['term'].strip().lower() in ctx['exact_targets']
        action = (f'Raise bid on "{t["term"]}" in {where}' if has_exact else
                  f'Create an exact-match target for "{t["term"]}" (currently '
                  f'converting on broad/phrase only)')

        out.append(_shell(
            'scale_ppc', scope, t['term'],
            title=f'Scale "{t["term"]}" — ACOS {t["acos"]*100:.0f}% vs group {group_acos*100:.0f}%',
            why=(f'{t["orders"]:,.0f} orders at {t["acos"]*100:.0f}% ACOS, well inside the '
                 f'group average. Marginal contribution after ad cost is '
                 f'{marginal_rate*100:.0f}%.'),
            headroom=headroom, win_prob=prob, factors=factors,
            margin_factor=marginal_rate,
            evidence={
                'spend': t['spend'], 'sales': t['sales'], 'orders': t['orders'],
                'clicks': t['clicks'], 'acos': t['acos'], 'roas': t['roas'],
                'cvr': t['cvr'], 'group_acos': group_acos, 'group_cvr': group['cvr'],
                'has_exact_target': has_exact, 'campaigns': camps[:3],
                'market_share': q['share'] if q else None,
            },
            actions=[{'domain': 'ppc', 'text': action}],
            dependencies=([{'kind': 'inventory',
                            'text': f'Stock cover {inv["min_cover"]:.0f} days on {inv["worst_sku"]}',
                            'met': not inv['warn']}] if inv.get('has_data') else []),
            conf=scoring.confidence(clicks=t['clicks'], orders=t['orders'],
                                    ba_weeks=ctx['ba_weeks'],
                                    margin_trusted=ctx['margin_trusted']),
            blocked=(f'Stock cover {inv["min_cover"]:.0f} days on {inv["worst_sku"]} — '
                     f'restock before scaling' if inv.get('blocked') else ''),
            unmet=1 if inv.get('warn') else 0,
        ))

    return sorted(out, key=lambda o: -o['score_if_unblocked'])[:cfg.MAX_OPPORTUNITIES_PER_TYPE]


def listing_fix(ctx) -> list:
    """
    The ad is attractive, the listing is not — a conversion problem no bid
    change can fix.

    Headroom = the clicks we already pay for, converting at the group's own
    rate instead of this term's. Nothing speculative: the traffic exists and
    is already bought.
    """
    scope, spine = ctx['scope'], ctx['spine']
    group = spine['totals']
    out = []

    if not group['cvr'] or not ctx['asp']:
        return out

    for h, t in spine['terms'].items():
        if t['orders'] <= 0 or t['clicks'] < cfg.MIN_CLICKS_FOR_CVR_PROOF:
            continue
        if t['ctr'] < group['ctr'] * cfg.LISTING_CTR_RATIO:
            continue
        if t['cvr'] > group['cvr'] * cfg.LISTING_CVR_RATIO:
            continue

        tags = ctx['tags'].get(h) or {}
        # An ASIN target carries no customer language, so "add these words to
        # your title" would be nonsense advice. Poor conversion against a
        # competitor's detail page is a CONQUEST question (Phase 2), not a
        # copywriting one.
        if tags.get('is_asin'):
            continue

        lost_orders = t['clicks'] * (group['cvr'] - t['cvr'])
        headroom = lost_orders * ctx['asp'] * ctx['monthly_factor']
        if headroom <= 0:
            continue

        missing = [w for w in _tokens(t['term'])
                   if ctx['token_missing'](w)]

        factors = {
            'execution': 0.55,             # copy changes are not a guaranteed lift
            'conversion': scoring.conversion_factor(t['cvr'], group['cvr'], t['clicks']),
        }
        prob, _ = scoring.win_probability(**factors)

        why = (f'CTR {t["ctr"]*100:.2f}% vs group {group["ctr"]*100:.2f}% — shoppers '
               f'click. CVR {t["cvr"]*100:.1f}% vs group {group["cvr"]*100:.1f}% — '
               f'then they do not buy.')
        if missing:
            why += f' Customer words absent from every listing title: {", ".join(missing[:4])}.'

        out.append(_shell(
            'listing_fix', scope, t['term'],
            title=f'Listing converts poorly on "{t["term"]}"',
            why=why,
            headroom=headroom, win_prob=prob, factors=factors,
            margin_factor=ctx['cm_rate'],
            evidence={
                'clicks': t['clicks'], 'orders': t['orders'], 'ctr': t['ctr'],
                'cvr': t['cvr'], 'group_ctr': group['ctr'], 'group_cvr': group['cvr'],
                'spend': t['spend'], 'sales': t['sales'],
                'missing_tokens': missing[:8], 'attributes': tags.get('attributes', []),
                'lost_orders_estimate': lost_orders,
            },
            actions=([{'domain': 'listing',
                       'text': f'Add customer language to titles/bullets: {", ".join(missing[:4])}'}]
                     if missing else
                     [{'domain': 'listing',
                       'text': f'Review images, price and A+ for the "{t["term"]}" query'}]),
            dependencies=[{'kind': 'listing', 'text': 'Phase 1 checks titles only — '
                                                      'bullets and A+ are not synced yet',
                           'met': False}],
            conf=scoring.confidence(clicks=t['clicks'], orders=t['orders'],
                                    ba_weeks=ctx['ba_weeks'],
                                    margin_trusted=ctx['margin_trusted']),
        ))

    return sorted(out, key=lambda o: -o['score'])[:cfg.MAX_OPPORTUNITIES_PER_TYPE]


_STOPWORDS = {'for', 'and', 'the', 'with', 'of', 'in', 'a', 'to', 'set', 'pack',
              'x', 'by', 'on', 'best', 'top'}


def _tokens(term: str) -> list:
    """Content words from a search term, for listing-coverage checks."""
    return [w for w in (term or '').lower().split()
            if len(w) > 2 and w not in _STOPWORDS and not w.isdigit()]


# ═══════════════════════════════════════════════════════════════════════════
# MARKET SIDE — needs Brand Analytics; each returns [] when SQP is unavailable
# ═══════════════════════════════════════════════════════════════════════════

def _market_case(ctx, q, opp_type, ceiling, title, why, actions, deps,
                 unmet=0, blocked=''):
    """Shared headroom maths for every share-based opportunity."""
    scope = ctx['scope']
    gain = max(0.0, ceiling - q['share'])
    headroom = gain * q['market_purchases_wk'] * cfg.WEEKS_PER_MONTH * ctx['asp']
    if headroom <= 0:
        return None

    term = ctx['spine']['terms'].get(q['hash'])
    clicks = term['clicks'] if term else 0
    orders = term['orders'] if term else 0

    factors = {
        'conversion': scoring.conversion_factor(
            term['cvr'] if term else 0, ctx['spine']['totals']['cvr'], clicks),
        'foothold':  scoring.foothold_factor(q['share']),
        'intensity': scoring.intensity_factor(q['intensity']),
        'momentum':  scoring.momentum_factor(q['share_trend'], ctx['can_trend']),
    }
    prob, _ = scoring.win_probability(**factors)

    return _shell(
        opp_type, scope, q['query'], title=title, why=why,
        headroom=headroom, win_prob=prob, factors=factors,
        margin_factor=ctx['cm_rate'],
        evidence={
            'market_purchases_wk': q['market_purchases_wk'],
            'our_purchases_wk':    q['our_purchases_wk'],
            'share':               q['share'],
            'attainable_share':    ceiling,
            'click_share':         q['click_share'],
            'share_trend':         q['share_trend'],
            'rank':                q['rank'],
            'intensity':           q['intensity'],
            'top_clicked':         q['top_clicked'],
            'market_cvr':          q['market_cvr'],
            'our_cvr':             q['our_cvr'],
            'ad_spend':            term['spend'] if term else 0,
            'ad_orders':           orders,
            'ba_weeks':            ctx['ba_weeks'],
            'asp':                 ctx['asp'],
        },
        actions=actions, dependencies=deps,
        conf=scoring.confidence(clicks=clicks, orders=orders,
                                ba_weeks=ctx['ba_weeks'],
                                margin_trusted=ctx['margin_trusted']),
        unmet=unmet, blocked=blocked,
    )


def organic_push(ctx) -> list:
    """
    Big pool, we convert, and we are still nearly invisible in it.

    The signal management actually wants: demand we have PROVEN we can serve
    (paid conversion) but do not own organically. More ad spend rents this
    demand; rank buys it.
    """
    out = []
    if not ctx['ba_weeks']:
        return out
    inv = ctx['inventory']

    for h, q in ctx['market']['queries'].items():
        if q['market_purchases_wk'] < cfg.MIN_MARKET_PURCHASES_WK:
            continue
        if q['share'] >= cfg.ORGANIC_GAP_MAX_SHARE:
            continue
        ptype = (ctx['tags'].get(h) or {}).get('product_type')
        if ptype not in ctx['group_types'] and ptype != 'generic_towel':
            continue

        ceiling = ctx['ceiling_for'](q)
        case = _market_case(
            ctx, q, 'organic_push', ceiling,
            title=f'Own "{q["query"]}" organically — share {q["share"]*100:.1f}%',
            why=(f'{q["market_purchases_wk"]:.0f} purchases a week happen on this query '
                 f'and {q["our_purchases_wk"]:.1f} of them are ours. We already convert '
                 f'this demand through ads, so the gap is visibility, not fit.'),
            actions=[
                {'domain': 'listing', 'text': f'Optimise title/backend for "{q["query"]}"'},
                {'domain': 'ppc', 'text': 'Hold exact-match spend to defend rank while it builds'},
            ],
            deps=([{'kind': 'inventory',
                    'text': f'Stock cover {inv["min_cover"]:.0f} days',
                    'met': not inv['warn']}] if inv.get('has_data') else []),
            blocked=(f'Stock cover {inv["min_cover"]:.0f} days on {inv["worst_sku"]}'
                     if inv.get('blocked') else ''),
        )
        if case:
            out.append(case)

    return sorted(out, key=lambda o: -o['score_if_unblocked'])[:cfg.MAX_OPPORTUNITIES_PER_TYPE]


def capture_share(ctx) -> list:
    """
    Pools where we are present but not winning — the contested middle.

    Share band sits between the organic-gap ceiling and WINNING_SHARE, so this
    cannot double-count with organic_push by construction.
    """
    out = []
    if not ctx['ba_weeks']:
        return out

    for h, q in ctx['market']['queries'].items():
        if q['market_purchases_wk'] < cfg.MIN_MARKET_PURCHASES_WK:
            continue
        if not (cfg.ORGANIC_GAP_MAX_SHARE <= q['share'] < cfg.WINNING_SHARE):
            continue
        ptype = (ctx['tags'].get(h) or {}).get('product_type')
        if ptype not in ctx['group_types'] and ptype != 'generic_towel':
            continue

        ceiling = ctx['ceiling_for'](q)
        falling = q['share_trend'] is not None and q['share_trend'] < 0
        case = _market_case(
            ctx, q, 'capture_share', ceiling,
            title=f'Take share on "{q["query"]}" — {q["share"]*100:.1f}% of a '
                  f'{q["market_purchases_wk"]:.0f}/wk pool',
            why=(f'We hold {q["share"]*100:.1f}% of this pool'
                 + (' and it is falling.' if falling else '.')
                 + f' Market converts clicks at {q["market_cvr"]*100:.1f}%, we convert at '
                   f'{q["our_cvr"]*100:.1f}%.'),
            actions=[
                {'domain': 'ppc', 'text': f'Increase exact-match coverage of "{q["query"]}"'},
                {'domain': 'listing', 'text': 'Close the conversion gap vs market rate'},
            ],
            deps=[],
        )
        if case:
            out.append(case)

    return sorted(out, key=lambda o: -o['score'])[:cfg.MAX_OPPORTUNITIES_PER_TYPE]


def product_gap(ctx) -> list:
    """
    Demand for something we do not sell.

    Our ASINs surfaced against these queries (that is why SQP reports them at
    all), the pool is real, and no product in the catalog answers it. Estimated
    with the same market maths as any other opportunity, so a product decision
    can be compared against a bid decision on one board.
    """
    out = []
    if not ctx['ba_weeks']:
        return out

    for h, q in ctx['market']['queries'].items():
        if q['market_purchases_wk'] < cfg.MIN_MARKET_PURCHASES_WK:
            continue
        tags = ctx['tags'].get(h) or {}
        ptype = tags.get('product_type')
        if not ptype or ptype in ('unknown', 'non_towel', 'generic_towel'):
            continue
        if ptype in ctx['catalog_types']:
            continue          # we already sell this type somewhere
        if q['share'] >= cfg.FALLBACK_ATTAINABLE_SHARE:
            continue          # we are somehow already serving it

        label = PRODUCT_TYPE_LABELS.get(ptype, ptype)
        ceiling = cfg.FALLBACK_ATTAINABLE_SHARE
        case = _market_case(
            ctx, q, 'product_gap', ceiling,
            title=f'Product gap: {label} — "{q["query"]}"',
            why=(f'{q["market_purchases_wk"]:.0f} purchases a week on this query and no '
                 f'{label.lower()} product in the catalog. Our listings surface against '
                 f'it, which is why Brand Analytics reports it at all.'),
            actions=[
                {'domain': 'catalog', 'text': f'Evaluate adding a {label.lower()} line'},
                {'domain': 'catalog', 'text': 'Check supplier costs and MOQ before committing'},
            ],
            deps=[{'kind': 'catalog', 'text': 'Requires a new product — not a PPC action',
                   'met': False}],
            unmet=1,
        )
        if case:
            out.append(case)

    return sorted(out, key=lambda o: -o['score'])[:cfg.MAX_OPPORTUNITIES_PER_TYPE]


def conquest(ctx) -> list:
    """
    Is buying our way onto competitors' detail pages earning its place?

    WHY THIS SHAPE. The competitor intelligence originally designed — riser,
    fader, vulnerable, fortress — rested on the per-query top-3 ASIN arrays in
    Brand Analytics. Those arrays are empty by construction: `ba_reports.py`
    writes them as `[]` with the comment "SQP does not include competitor top-3
    ASINs at the query level", and the Item Comparison report that could have
    supplied them is deprecated as of 2026. So competitor *share movement* is
    not observable from Pulse's data at all.

    What is observable is our own conquest spending: the ASIN targets we bid on
    show up in the search-term table as `b0…` terms, already flagged by the
    taxonomy. That supports one genuine portfolio decision — is conquest paying
    for itself relative to the rest of this group — which no per-term generator
    answers, because each ASIN target on its own looks like any other keyword.
    """
    scope, spine = ctx['scope'], ctx['spine']
    group = spine['totals']
    group_acos = group['acos']
    if not group_acos:
        return []

    rows = [(h, t) for h, t in spine['terms'].items()
            if (ctx['tags'].get(h) or {}).get('is_asin')]
    if not rows:
        return []

    spend = sum(t['spend'] for _, t in rows)
    sales = sum(t['sales'] for _, t in rows)
    orders = sum(t['orders'] for _, t in rows)
    clicks = sum(t['clicks'] for _, t in rows)
    if spend < cfg.CONQUEST_MIN_SPEND:
        return []

    acos = (spend / sales) if sales > 0 else None
    share_of_spend = spend / group['spend'] if group['spend'] else 0
    cur = ctx['cur']
    top = sorted(rows, key=lambda r: -r[1]['spend'])[:5]
    evidence = {
        'spend': spend, 'sales': sales, 'orders': int(round(orders)),
        'clicks': int(round(clicks)), 'acos': acos, 'group_acos': group_acos,
        'targets': len(rows), 'share_of_spend': share_of_spend,
        'top_targets': [{'asin': t['term'], 'spend': t['spend'],
                         'orders': int(round(t['orders'])), 'acos': t['acos']}
                        for _, t in top],
    }

    if acos is None or acos >= group_acos * cfg.CONQUEST_POOR_RATIO:
        # Recoverable = the spend above what group-average efficiency would cost
        # to buy the same sales. Money, not a ratio.
        efficient = (sales * group_acos) if sales else 0.0
        headroom = max(0.0, spend - efficient) * ctx['monthly_factor']
        factors = {'execution': 0.85}
        prob, _ = scoring.win_probability(**factors)
        return [_shell(
            'conquest', scope, '__conquest_portfolio__',
            title=f'Conquest is underperforming — {len(rows)} competitor ASIN targets',
            why=(f'{cur}{spend:,.0f} on competitor ASIN targets returned '
                 + (f'{cur}{sales:,.0f} at {acos*100:.0f}% ACOS against a group average of '
                    f'{group_acos*100:.0f}%.' if acos else 'no sales at all.')
                 + f' That is {share_of_spend*100:.0f}% of this group\'s spend buying '
                   f'placement on other brands\' pages.'),
            headroom=headroom, win_prob=prob, factors=factors, margin_factor=1.0,
            evidence=evidence,
            actions=[
                {'domain': 'ppc', 'text': 'Cut the weakest ASIN targets listed below'},
                {'domain': 'ppc', 'text': 'Redeploy the budget to keyword targets already '
                                          'converting inside group ACOS'},
            ],
            dependencies=[],
            conf=scoring.confidence(clicks=int(clicks), orders=int(orders),
                                    ba_weeks=ctx['ba_weeks'], margin_trusted=True),
        )]

    if acos <= group_acos * cfg.CONQUEST_GOOD_RATIO:
        marginal = ctx['cm_rate'] - acos
        if marginal <= 0:
            return []
        growth = min(1.0, (group_acos / acos) - 1.0)
        headroom = sales * growth * ctx['monthly_factor']
        factors = {'execution': 0.7,
                   'conversion': scoring.conversion_factor(
                       (orders / clicks) if clicks else 0, group['cvr'], int(clicks))}
        prob, _ = scoring.win_probability(**factors)
        return [_shell(
            'conquest', scope, '__conquest_portfolio__',
            title=f'Conquest is working — room to expand ASIN targeting',
            why=(f'{len(rows)} competitor ASIN targets returned {cur}{sales:,.0f} at '
                 f'{acos*100:.0f}% ACOS, well inside the group average of '
                 f'{group_acos*100:.0f}%. Taking sales on a rival\'s page is working '
                 f'and is currently only {share_of_spend*100:.0f}% of spend.'),
            headroom=headroom, win_prob=prob, factors=factors,
            margin_factor=marginal, evidence=evidence,
            actions=[
                {'domain': 'ppc', 'text': 'Raise bids on the ASIN targets listed below'},
                {'domain': 'ppc', 'text': 'Add ASIN targets for competitors in the same '
                                          'category not yet covered'},
            ],
            dependencies=[],
            conf=scoring.confidence(clicks=int(clicks), orders=int(orders),
                                    ba_weeks=ctx['ba_weeks'], margin_trusted=True),
        )]
    return []


GENERATORS = [defend_waste, scale_ppc, listing_fix,
              organic_push, capture_share, product_gap, conquest]
