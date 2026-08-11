"""
Run orchestration — turns (group × marketplace × range) into a stored
StiReportRun plus persisted, ranked opportunities.

Order matters and is deliberate: cheap scope resolution first, then the one
expensive spine query, then enrichment, then generation. Anything that fails
to produce data degrades to an empty section rather than aborting the run — a
report with six of eight cards is useful; a traceback is not.
"""
import logging
import time
from datetime import date, timedelta

from django.db import transaction

from . import config as cfg
from . import as_of as as_of_mod
from . import market, opportunities, periods as periods_mod, readiness, \
    scope as scope_mod, scoring, spine
from .taxonomy import derive_tier, TIER_LABELS
from ..sync import net_factor

logger = logging.getLogger(__name__)

CURRENCY_SYMBOLS = {'USD': '$', 'GBP': '£', 'AED': 'AED ', 'SAR': 'SAR ',
                    'EUR': '€', 'CAD': 'C$'}


def resolve_period(key: str, marketplace: str, ptype: str = periods_mod.WEEKLY,
                   asins=None):
    """
    The report runs on an Amazon reporting period, not a rolling range.

    Rolling ranges ("last 30 days") resolve differently every day, so two runs
    never cover the same days and the run-diff has to withhold every comparison.
    A named period is fixed forever, which is what makes outcome measurement —
    "we acted in Week 30, did Week 31 improve?" — possible at all.
    """
    return periods_mod.resolve(key, marketplace, ptype, asins)


def _catalog_types(marketplace: str) -> set:
    """Every product type we sell in this marketplace — the product-gap test."""
    from ..models import Product
    cats = (Product.objects.filter(marketplace=marketplace).order_by()
            .values_list('category', flat=True).distinct())
    return scope_mod._types_for_categories([c for c in cats if c])


def _build_context(scope, group, ctx_period=None) -> dict:
    """Assemble everything the generators read. One place, no hidden queries."""
    from django.conf import settings
    from .taxonomy import sync_tags

    sp = spine.build(scope)

    # Classify only the terms this run actually saw.
    terms = {h: t['term'] for h, t in sp['terms'].items()}
    tags = sync_tags(scope.marketplace, terms, scope.lexicon_key)
    tiers = {h: derive_tier(tg, scope.product_types) for h, tg in tags.items()}

    # Brand Analytics for THIS period — matched by construction, not by luck.
    ba_window = market.resolve_window(scope.marketplace, scope.asins,
                                      ctx_period)
    mk = market.build(scope, ba_window)

    # Valuation basis is structural and deliberately off the report window
    # (`MKT-D-012`); group revenue keeps its own span for the one ratio that
    # needs it.
    valuation = spine.valuation_basis(scope)
    revenue = spine.group_revenue(scope)
    inv = readiness.inventory(scope)
    listing = readiness.listing_tokens(scope)
    stamps = as_of_mod.measure(scope, ba_window=ba_window, valuation=valuation,
                               inventory=inv)

    # Attainable-share ceiling per query, from siblings sharing a product type.
    shares_by_type: dict = {}
    for h, q in mk['queries'].items():
        pt = (tags.get(h) or {}).get('product_type')
        if pt:
            shares_by_type.setdefault(pt, []).append(q['share'])

    def ceiling_for(q):
        pt = (tags.get(q['hash']) or {}).get('product_type')
        return market.attainable_share(q['share'], shares_by_type.get(pt, []))

    currency = settings.AMAZON_MARKETPLACES.get(scope.marketplace, {}).get('currency', 'USD')

    # Campaign attribution only for terms that can reach the UI — the whole
    # spine would be a needless second scan of the fact table. Campaign
    # identity is display-only (naming what a user must edit).
    interesting = [h for h, t in sp['terms'].items()
                   if t['spend'] >= cfg.MIN_SPEND_FOR_WASTE or t['orders'] > 0]
    interesting = sorted(interesting, key=lambda h: -sp['terms'][h]['spend'])[:400]
    # Plus the biggest sub-floor zero-order terms, so the long-tail waste case
    # can name the campaigns leaking rather than only quote a total.
    tail = sorted((h for h, t in sp['terms'].items()
                   if t['orders'] == 0 and t['spend'] < cfg.MIN_SPEND_FOR_WASTE
                   and t['spend'] > 0),
                  key=lambda h: -sp['terms'][h]['spend'])[:300]
    interesting = list(dict.fromkeys(interesting + tail))

    return {
        'scope': scope, 'spine': sp, 'tags': tags, 'tiers': tiers,
        'market': mk, 'ba_window': ba_window, 'ba_weeks': ba_window['count'],
        'can_trend': ba_window['can_trend'],
        'queries_by_hash': mk['queries'],
        'valuation': valuation, 'cm_rate': valuation['cm_rate'],
        'margin_trusted': valuation['trusted'],
        'revenue': revenue, 'asp': valuation['asp'],
        'as_of': stamps,
        'inventory': inv, 'listing': listing,
        'token_missing': lambda w: readiness.token_missing(listing, w),
        'campaign_map': spine.campaign_breakdown(scope, interesting),
        'exact_targets': spine.existing_exact_targets(scope),
        'group_types': scope.product_types,
        'catalog_types': _catalog_types(scope.marketplace),
        'ceiling_for': ceiling_for,
        'monthly_factor': cfg.DAYS_PER_MONTH / max(scope.days, 1),
        'currency': currency,
        'cur': CURRENCY_SYMBOLS.get(currency, currency + ' '),
        'net_factor': net_factor(scope.marketplace),
    }


def _generate(ctx) -> list:
    """Run every generator, dedupe, rank."""
    found = []
    for gen in opportunities.GENERATORS:
        try:
            found.extend(gen(ctx))
        except Exception:
            logger.exception('STI generator %s failed', gen.__name__)

    # Ads-side types are mutually exclusive bids on one term; if two still
    # collide, the higher-value case wins (v2 §9).
    best: dict = {}
    for o in found:
        k = (o['opp_type'], o['subject'].lower())
        if k not in best or o['score_if_unblocked'] > best[k]['score_if_unblocked']:
            best[k] = o
    found = list(best.values())

    ads_side = {'defend', 'scale_ppc', 'listing_fix'}
    seen_subject: dict = {}
    deduped = []
    for o in sorted(found, key=lambda x: -x['score_if_unblocked']):
        if o['opp_type'] in ads_side:
            s = o['subject'].lower()
            if s in seen_subject:
                continue
            seen_subject[s] = True
        deduped.append(o)

    scores = sorted((o['score_if_unblocked'] for o in deduped), reverse=True)
    median = scores[len(scores) // 2] if scores else 0.0
    for o in deduped:
        o['quadrant'] = scoring.quadrant(o['score_if_unblocked'], o['difficulty'], median)

    return sorted(deduped, key=lambda o: (-o['score'], -o['score_if_unblocked']))


@transaction.atomic
def _persist(run, group, found: list) -> dict:
    """
    Upsert opportunities by stable key, snapshot them against this run, and
    age out ones the generators no longer emit.
    """
    from ..models import StiOpportunity, StiOpportunitySnapshot

    keys = [o['key'] for o in found]
    existing = {o.key: o for o in StiOpportunity.objects.filter(key__in=keys)}
    new_keys, snapshots = [], []

    for o in found:
        row = existing.get(o['key'])
        if row is None:
            row = StiOpportunity(key=o['key'], product_group=group,
                                 marketplace=run.marketplace,
                                 first_seen_run=run)
            new_keys.append(o['key'])

        row.opp_type        = o['opp_type']
        row.title           = o['title'][:200]
        row.why             = o['why']
        row.subject         = o['subject'][:512]
        row.score           = round(o['score'], 2)
        row.headroom_value  = round(o['headroom_value'], 2)
        row.win_probability = round(o['win_probability'], 4)
        row.margin_factor   = round(max(0.0, min(9.9999, o['margin_factor'])), 4)
        row.difficulty      = o['difficulty']
        row.confidence      = o['confidence']
        row.blocked_reason  = o['blocked_reason'][:120]
        row.evidence        = o['evidence']
        row.required_actions= o['required_actions']
        row.dependencies    = o['dependencies']
        row.timeline        = o['timeline']
        row.last_seen_run   = run
        row.runs_unseen     = 0
        if row.status == 'expired':
            row.status = 'open'          # it came back — reopen it
        row.save()

        snapshots.append(StiOpportunitySnapshot(
            opportunity=row, run=run, period_key=run.period_key,
            score=row.score, headroom_value=row.headroom_value,
            current_share=round((o['evidence'].get('share') or 0) * 100, 4),
            difficulty=row.difficulty, confidence=row.confidence,
            evidence=o['evidence'],
        ))
        o['id'] = row.id
        o['status'] = row.status
        o['is_new'] = o['key'] in new_keys

    StiOpportunitySnapshot.objects.bulk_create(snapshots, ignore_conflicts=True)

    # Anything open for this group/marketplace that this run did not emit.
    stale = (StiOpportunity.objects
             .filter(product_group=group, marketplace=run.marketplace,
                     status__in=['open', 'in_progress'])
             .exclude(key__in=keys))
    # float(), not the raw column: score is a DecimalField and Decimal is not
    # JSON-serialisable, so leaving it raw only breaks on the SECOND run of a
    # group — the first has nothing stale to report.
    disappeared = [{'title': t, 'score': float(s)}
                   for t, s in stale.values_list('title', 'score')[:10]]
    for row in stale:
        row.runs_unseen += 1
        if row.runs_unseen >= cfg.EXPIRE_AFTER_UNSEEN_RUNS and row.status == 'open':
            row.status = 'expired'
        row.save(update_fields=['runs_unseen', 'status'])

    return {'new': len(new_keys), 'new_keys': new_keys, 'disappeared': disappeared}


def _diff(run, group, found: list, persisted: dict) -> dict:
    """
    What changed since the last report for this group and marketplace.

    This is why opportunities carry a stable key: without identity a run is a
    fresh list every time, and "is this getting better?" is unanswerable.

    PERIOD GUARD (`MKT-D-012`). Two runs can cover different windows — a
    different preset, or the same preset resolving differently as new Brand
    Analytics weeks land. A score delta across different periods is a period
    change wearing the costume of a business change, so when the basis moves
    the diff says so rather than reporting movement.
    """
    from ..models import StiOpportunitySnapshot, StiReportRun

    # Same named period = same days, always. That is the whole point of the
    # period grid: a comparison that used to be withheld is now the normal case.
    prev = (StiReportRun.objects
            .filter(product_group=group, marketplace=run.marketplace,
                    status='complete', period_key=run.period_key)
            .exclude(id=run.id).order_by('-generated_at').first())
    if not prev:
        return {'first_run': True, 'new': persisted['new'],
                'disappeared': persisted['disappeared'], 'moved': []}

    prev_meta = (prev.payload or {}).get('meta', {})
    same_window = (prev.period_key == run.period_key)
    prev_ba = (prev_meta.get('ba_window') or {}).get('end')
    cur_ba = ((run.payload or {}).get('meta', {}).get('ba_window') or {}).get('end')
    comparable = same_window and prev_ba == cur_ba

    prev_scores = {
        s['opportunity__key']: float(s['score'])
        for s in StiOpportunitySnapshot.objects.filter(run=prev)
        .values('opportunity__key', 'score')
    }

    moved = []
    if comparable:
        for o in found:
            before = prev_scores.get(o['key'])
            if before is None or before <= 0:
                continue
            delta = o['score'] - before
            # 10% floor: below that it is recomputation noise, not news.
            if abs(delta) < max(1.0, before * 0.10):
                continue
            moved.append({'title': o['title'], 'opp_type': o['opp_type'],
                          'before': round(before, 2), 'after': round(o['score'], 2),
                          'delta': round(delta, 2),
                          'pct': round(delta / before * 100, 1)})
        moved.sort(key=lambda m: -abs(m['delta']))

    reason = ''
    if not comparable:
        reason = (f'The previous report covered {prev.date_from} → {prev.date_to}, a '
                  f'different window, so score movement would reflect the window rather '
                  f'than the business.' if not same_window else
                  'The previous report used a different Brand Analytics week, so '
                  'market-side movement would reflect the data rather than the market.')

    return {
        'first_run': False,
        'previous_run_id': prev.id,
        'previous_at': prev.generated_at.isoformat(),
        'previous_window': f'{prev.date_from} → {prev.date_to}',
        'comparable': comparable,
        'not_comparable_reason': reason,
        'new': persisted['new'],
        'new_items': [{'title': o['title'], 'opp_type': o['opp_type'],
                       'score': round(o['score'], 2)}
                      for o in found if o['key'] in persisted['new_keys']][:8],
        'disappeared': persisted['disappeared'],
        'moved': moved[:8],
    }


def _executive_cards(ctx, found: list) -> list:
    """
    The eight questions (v2 §3). Each card is ONE item — the top-scored member
    of its class — or an explicit "not yet available" when the data behind it
    is not part of Phase 1. An empty card says so; it never pads.
    """
    cur = ctx['cur']
    inv = ctx['inventory']

    headline = found[0] if found else None

    def top(*types):
        pool = [o for o in found if o['opp_type'] in types and not o['blocked_reason']]
        # If this class's best item is already the headline card, show the next
        # one — two identical cards read as a bug, not as emphasis. Falls back
        # to the duplicate when the class has nothing else, which is honest.
        if headline and pool and pool[0] is headline and len(pool) > 1:
            return pool[1]
        return pool[0] if pool else None

    def card(key, label, opp, empty_msg, unavailable=False):
        if unavailable:
            return {'key': key, 'label': label, 'state': 'unavailable',
                    'message': empty_msg}
        if not opp:
            return {'key': key, 'label': label, 'state': 'empty',
                    'message': empty_msg}
        return {
            'key': key, 'label': label, 'state': 'ok',
            'opportunity_id': opp.get('id'),
            'title': opp['title'], 'why': opp['why'],
            'value': opp['score'], 'value_display': f'{cur}{opp["score"]:,.0f}/mo',
            'confidence': opp['confidence'], 'difficulty': opp['difficulty'],
            'opp_type': opp['opp_type'],
        }

    ba_missing = ctx['ba_weeks'] < cfg.BA_WEEKS_MIN
    ba_msg = 'No Brand Analytics weeks for this group yet — market share cards need SQP data.'

    cards = [
        card('biggest_opportunity', 'Biggest Opportunity',
             headline, 'No opportunities above the materiality floor.'),
        card('biggest_waste', 'Biggest Waste', top('defend'),
             'No zero-order spend above the floor. Nothing being wasted.'),
        card('biggest_organic_gap', 'Biggest Organic Gap', top('organic_push'),
             ba_msg if ba_missing else 'No material organic gaps — share is healthy where demand is.',
             unavailable=ba_missing),
        card('biggest_ppc', 'Biggest PPC Opportunity', top('scale_ppc'),
             'No terms are converting far enough below group ACOS to scale.'),
        card('biggest_listing', 'Biggest Listing Opportunity', top('listing_fix'),
             'No high-click / low-conversion terms found.'),
        card('biggest_product', 'Biggest Product Opportunity', top('product_gap'),
             ba_msg if ba_missing else 'No unserved demand types above the floor.',
             unavailable=ba_missing),
        # Competitor SHARE movement is not observable: Amazon publishes no
        # per-query top-3 ASINs and the Item Comparison report is deprecated.
        # What we can measure is our own conquest spending, so that is what
        # this card reports rather than a placeholder.
        card('biggest_conquest', 'Conquest (competitor targeting)', top('conquest'),
             'No competitor ASIN targeting above the floor in this group.'),
    ]

    # Inventory risk is not an opportunity — it is a gate on other ones.
    blocked = [o for o in found if o['blocked_reason']]
    if inv.get('has_data') and (inv['blocked'] or inv['warn']):
        held = sum(o['score_if_unblocked'] for o in blocked)
        cards.append({
            'key': 'inventory_risk', 'label': 'Biggest Inventory Risk', 'state': 'ok',
            'title': f'{inv["worst_sku"]} — {inv["min_cover"]:.0f} days cover',
            'why': (f'{len(blocked)} spend opportunities worth {cur}{held:,.0f}/mo are held '
                    f'back by stock.' if blocked else
                    'Cover is below the safety threshold; scaling spend here would risk a stockout.'),
            'value': held, 'value_display': f'{cur}{held:,.0f}/mo held',
            'confidence': 'high', 'difficulty': 2, 'opp_type': 'inventory',
        })
    else:
        cards.append({
            'key': 'inventory_risk', 'label': 'Biggest Inventory Risk',
            'state': 'empty' if inv.get('has_data') else 'unavailable',
            'message': ('Stock cover is healthy across the group.' if inv.get('has_data')
                        else 'No inventory cover data for this group.'),
        })

    return cards


def _tier_mix(ctx) -> list:
    """Where the money goes by intent tier — the one distribution worth showing."""
    agg: dict = {}
    for h, t in ctx['spine']['terms'].items():
        tier = ctx['tiers'].get(h, 'off_category')
        a = agg.setdefault(tier, {'tier': tier, 'label': TIER_LABELS.get(tier, tier),
                                  'spend': 0.0, 'sales': 0.0, 'orders': 0, 'terms': 0})
        a['spend'] += t['spend']
        a['sales'] += t['sales']
        a['orders'] += t['orders']
        a['terms'] += 1
    rows = sorted(agg.values(), key=lambda r: -r['spend'])
    for r in rows:
        r['acos'] = (r['spend'] / r['sales']) if r['sales'] > 0 else None
        r['orders'] = int(round(r['orders']))
    return rows


def generate(group, marketplace: str, period, user=None):
    """Entry point. Takes an Amazon reporting Period. Returns the stored run."""
    from ..models import StiReportRun

    started = time.time()
    date_from, date_to = period.start, period.end
    run = StiReportRun.objects.create(
        product_group=group, marketplace=marketplace,
        period_type=period.ptype, period_key=period.key,
        date_from=date_from, date_to=date_to,
        generated_by=user if (user and user.is_authenticated) else None,
    )

    try:
        sc = scope_mod.resolve(group, marketplace, date_from, date_to)
        sc.coverage = scope_mod.coverage_stats(marketplace, date_from, date_to)

        ctx = _build_context(sc, group, period)
        found = _generate(ctx)
        persisted = _persist(run, group, found)

        totals = ctx['spine']['totals']
        rev = ctx['revenue']

        # Ad sales restricted to the days settled revenue also covers, and
        # withheld outright when that overlap is too thin to mean anything.
        paid_share, paid_share_note = None, ''
        rev_days = rev.get('days', 0)
        if not rev.get('has_data'):
            paid_share_note = ('No settled revenue inside this window — it ends '
                               'after the revenue feed reaches.')
        elif rev_days < cfg.PAID_SHARE_MIN_DAYS:
            paid_share_note = (f'Only {rev_days} day{"s" if rev_days != 1 else ""} of '
                               f'settled revenue overlap this window — too few to divide by.')
        elif rev['revenue'] > 0:
            from dataclasses import replace
            clipped_sales = spine.build(replace(sc, date_to=rev['end']))['totals']['sales']
            paid_share = round(clipped_sales / rev['revenue'], 4)

        run.payload = {
            'meta': {
                'group':        group.name,
                'group_slug':   group.slug,
                'marketplace':  marketplace,
                'currency':     ctx['currency'],
                'symbol':       ctx['cur'],
                'period_key':   period.key,
                'period_type':  period.ptype,
                'period_label': period.label,
                'period_coverage': {'ads_days': period.ads_days,
                                    'span_days': period.span_days,
                                    'complete': period.complete,
                                    'has_ba': period.has_ba},
                'date_from':    date_from.isoformat(),
                'date_to':      date_to.isoformat(),
                'days':         sc.days,
                'campaigns':    len(sc.campaign_ids),
                'ad_groups':    len(sc.ad_group_weights),
                'weighted':     sc.is_weighted,
                'asins':        len(sc.asins),
                'skus':         len(sc.skus),
                'terms':        len(ctx['spine']['terms']),
                'ba_weeks':     ctx['ba_weeks'],
                'ba_window':    ({'start': ctx['ba_window']['start'].isoformat(),
                                  'end':   ctx['ba_window']['end'].isoformat()}
                                 if ctx['ba_window']['has_data'] else None),
                'ba_can_trend': ctx['can_trend'],
                'ba_stale':     ctx['ba_window'].get('stale', False),
                'ba_stale_days': ctx['ba_window'].get('staleness_days'),
                'valuation':    {'cm_rate':  round(ctx['cm_rate'], 4),
                                 'asp':      round(ctx['asp'], 2),
                                 'days':     ctx['valuation']['days'],
                                 'skus_sold':  ctx['valuation']['skus_sold'],
                                 'skus_total': ctx['valuation']['skus_total'],
                                 'source':   ctx['valuation']['source'],
                                 'trusted':  ctx['margin_trusted']},
                'as_of':        ctx['as_of'],
                'asp':          round(ctx['asp'], 2),
                'net_factor':   round(ctx['net_factor'], 4),
                'scope_warning': scope_mod.diagnose_empty(sc),
                'data_quality': {
                    'attribution':      sc.coverage,
                    'listing_titles':   ctx['listing']['title_count'],
                    'sqp_coverage_pct': _sqp_coverage(ctx),
                },
            },
            'kpis': {
                'ad_spend':      round(totals['spend'], 2),
                'ad_sales':      round(totals['sales'], 2),
                # Weighting makes counts fractional; they are display data, so
                # they round here while the spine keeps full precision for the
                # ratios computed from them.
                'orders':        int(round(totals['orders'])),
                'clicks':        int(round(totals['clicks'])),
                'impressions':   int(round(totals['impressions'])),
                'acos':          round(totals['acos'], 4) if totals['acos'] else None,
                'roas':          round(totals['roas'], 2) if totals['roas'] else None,
                'ctr':           round(totals['ctr'], 4),
                'cvr':           round(totals['cvr'], 4),
                'cpc':           round(totals['cpc'], 2),
                'group_revenue': round(rev['revenue'], 2),
                'group_units':   rev['units'],
                'has_revenue':   rev['has_data'],
                # Paid share answers "are we improving organically", so it
                # stays — but it divides ad sales by settled revenue, and those
                # sources cover different spans. The ratio therefore uses only
                # the days both cover. Arithmetic, not alignment (`MKT-D-012`).
                'paid_share':      paid_share,
                'paid_share_days': rev.get('days', 0),
                'paid_share_end':  rev['end'].isoformat() if rev.get('end') else None,
                'paid_share_note': paid_share_note,
                # Ad sales can legitimately exceed group revenue: sales_7d is
                # 7-day attributed to the CLICK date (so orders land outside
                # the window) and includes halo sales of other SKUs, while
                # group revenue counts only this group's SKUs on the order
                # date. Flagged rather than clipped — a >100% paid share is
                # information about attribution, not an error to hide.
                'paid_share_exceeds': bool(rev['revenue'] > 0
                                           and totals['sales'] > rev['revenue']),
                'market_share':  round(ctx['market']['totals']['share'], 4) if ctx['ba_weeks'] else None,
                'open_value':    round(sum(o['score'] for o in found), 2),
            },
            'cards':         _executive_cards(ctx, found),
            'opportunities': found,
            'tier_mix':      _tier_mix(ctx),
            'inventory':     ctx['inventory'],
            'diff':          {},          # filled below — needs meta in place
        }
        run.payload['diff'] = _diff(run, group, found, persisted)
        run.status = 'complete'

    except Exception as exc:                       # noqa: BLE001 — surfaced to UI
        logger.exception('STI run failed')
        run.status = 'failed'
        run.error = f'{type(exc).__name__}: {exc}'

    run.duration_ms = int((time.time() - started) * 1000)
    try:
        run.save()
    except (TypeError, ValueError) as exc:
        # A payload that will not serialise must not 500 the request. Record
        # the failure on the run so the UI can say what happened.
        logger.exception('STI payload could not be stored')
        run.payload = {}
        run.status = 'failed'
        run.error = f'Payload could not be stored: {type(exc).__name__}: {exc}'
        run.save()
    return run


def _sqp_coverage(ctx) -> float:
    """
    What share of ad spend has Brand Analytics data behind it.

    The honesty metric (v2 §1.4): a thin SQP week must read as thin, never as
    "no demand". Shown on the page, not buried.
    """
    if not ctx['ba_weeks']:
        return 0.0
    total = ctx['spine']['totals']['spend']
    if total <= 0:
        return 0.0
    covered = sum(t['spend'] for h, t in ctx['spine']['terms'].items()
                  if h in ctx['market']['queries'])
    return round(covered / total * 100, 1)
