"""
apps/dashboard/opportunities.py — P3 contextual opportunity intelligence.

DIAGNOSIS ONLY. Nothing here changes a bid, a budget, a target or anything on
Amazon; it classifies numbers Pulse has already computed and explains why.

Design rules this module obeys:

  • NO composite score. Every opportunity is an explicit rule over existing
    metrics, and every card carries the numbers that produced it.
  • NO new analytics. The inputs are the rows the P0/P1/P2 endpoints already
    built (driver shares, P1 signals, search-term tags) — this module never
    re-queries or re-derives them.
  • NO new thresholds where a rule already exists. PPC-dependency and organic
    decline come from the P1 signal engine; search-term/target rules come from
    `_tag_search_term`. Exactly one new rule is introduced — SHARE_RATIO — and
    it is documented below.
  • Ranking is by MONEY AT STAKE, matching the existing Pulse convention
    (StiOpportunity.score = expected CM/month, AIRecommendation.rank_score).
  • Data honesty: an opportunity is never emitted from missing/unlinked data.
    `—` is not zero, and an unavailable report is not poor performance.
"""

# ── Kinds ───────────────────────────────────────────────────────────────────
SCALE          = 'scale'
EFFICIENCY     = 'efficiency'
PPC_DEPENDENCY = 'ppc_dependency'
ORGANIC_DECLINE = 'organic_decline'
TERM_WASTE     = 'term_waste'
TERM_SCALE     = 'term_scale'
TERM_CVR       = 'term_cvr'

KIND_META = {
    SCALE:          ('SCALE',          'Producing more sales share than it consumes in spend'),
    EFFICIENCY:     ('EFFICIENCY',     'Consuming more spend share than it produces in sales'),
    PPC_DEPENDENCY: ('PPC DEPENDENCY', 'Revenue leans heavily on paid traffic'),
    ORGANIC_DECLINE:('ORGANIC DECLINE','Organic (est.) fell while paid held or grew'),
    TERM_WASTE:     ('TERM WASTE',     'Spend with no attributed sales'),
    TERM_SCALE:     ('TERM SCALE',     'Strong return at low spend'),
    TERM_CVR:       ('LISTING / CVR',  'Traffic converts poorly after the click'),
    # StiOpportunity.TYPES — labelled here so surfaced STI cards read the same
    # way as Pulse cards. The records themselves are untouched.
    'capture_share': ('CAPTURE SHARE', 'Search Intelligence: share available to win'),
    'product_gap':   ('PRODUCT GAP',   'Search Intelligence: demand with no matching product'),
    'organic_push':  ('ORGANIC PUSH',  'Search Intelligence: organic rank opportunity'),
    'listing_fix':   ('LISTING FIX',   'Search Intelligence: listing quality issue'),
    'scale_ppc':     ('SCALE PPC',     'Search Intelligence: paid headroom'),
    'defend':        ('DEFEND (WASTE)','Search Intelligence: spend to protect/trim'),
    'conquest':      ('CONQUEST',      'Search Intelligence: competitor-held demand'),
}

# ── The single new rule (P3.2: document, do not silently invent) ────────────
# A driver is called out when one share is at least this multiple of the other.
# 2.0 is the minimum ratio at which a mismatch is unambiguous rather than noise
# (the brief's own examples are 4x and 3x, which clear this bar comfortably).
# The spend floor is REUSED from the P1 signal engine so a $3 campaign never
# produces a card.
SHARE_RATIO = 2.0

# Confidence wording is derived from the EXISTING allocation fields, never a
# second confidence system.
def _confidence_label(confidence, settlement, attribution_source, linked=True):
    """Map existing attribution fields to a short, honest label."""
    if not linked:
        return ('unlinked', 'Spend is authoritative but this campaign id could not '
                            'be matched to the Ads reports — sales attribution '
                            'unavailable.')
    if confidence is None:
        return ('unknown', 'No allocation confidence recorded.')
    est_sources = {'sb_revenue_share', 'sd_revenue_share', 'group_revenue_share',
                   'cold_start_equal', 'cold_start_catalog', 'sp_provisional'}
    if settlement and settlement != 'locked':
        return ('provisional', f'Window includes {settlement} days — figures may '
                               f'still move (T+3). Allocation confidence '
                               f'{confidence:.2f}.')
    if attribution_source in est_sources:
        return ('estimated', f'Spend attributed by revenue share, not Amazon\'s '
                             f'per-SKU report. Allocation confidence {confidence:.2f}.')
    if confidence >= 0.8:
        return ('authoritative', f'Settled, from Amazon\'s advertised-product '
                                 f'report. Allocation confidence {confidence:.2f}.')
    return ('low', f'Allocation confidence {confidence:.2f}.')


def _opp(kind, *, title, subject, evidence, reason, at_stake,
         confidence_label, confidence_note, level, drill=None, source='pulse',
         status=None, key=None):
    """One opportunity card. `at_stake` is the money the card is about — the
    ranking key, matching how STI and AIRecommendation already sort."""
    label, meaning = KIND_META.get(kind, (kind.upper(), ''))
    return {
        'kind': kind, 'label': label, 'meaning': meaning,
        'title': title, 'subject': subject,
        'evidence': evidence,          # [{label, value}] — every number cited
        'reason': reason,              # measurable sentence, never vague praise
        'at_stake': round(float(at_stake or 0), 2),
        'confidence': confidence_label, 'confidence_note': confidence_note,
        'level': level,                # sku | campaign | target | search_term
        'drill': drill,                # {label, url} or {label, target_id}
        'source': source,              # pulse | sti | ai
        'status': status,              # only for sti/ai records
        'key': key or f'{kind}:{level}:{subject}',
    }


def _money(v):
    return f'${v:,.0f}' if abs(v) >= 1 else f'${v:,.2f}'


# ── SKU level ───────────────────────────────────────────────────────────────
def sku_opportunities(*, sku, driver_rows, context, signals, campaign_url,
                      limit=6):
    """Deterministic SKU-level opportunities.

    Inputs are ALREADY COMPUTED by api_sku_campaigns / api_pnl_skus:
      driver_rows — P0 rows (spend, spend_share, ppc_sales, sales_share, roas,
                    attribution_source, confidence, settlement, linked)
      context     — P1 window context (revenue, ppc_sales, organic, deltas…)
      signals     — P1 signal ids already computed for this SKU
    Nothing is re-derived here.
    """
    out = []
    min_spend = _min_spend()

    for r in driver_rows:
        spend = float(r.get('spend') or 0)
        # Data honesty: no card from unlinked rows (sales unknown, not zero).
        if not r.get('linked', True) or r.get('sales_share') is None:
            continue
        if spend < min_spend:
            continue
        sp_share = float(r.get('spend_share') or 0)
        sa_share = float(r.get('sales_share') or 0)
        sales = float(r.get('ppc_sales') or 0)
        conf_label, conf_note = _confidence_label(
            r.get('confidence'), r.get('settlement'), r.get('attribution_source'),
            linked=r.get('linked', True))
        drill = {'label': 'View campaign', 'url': campaign_url(r['campaign_id'])}
        base_ev = [
            {'label': 'Spend',        'value': _money(spend)},
            {'label': 'Spend share',  'value': f'{sp_share:.1f}%'},
            {'label': 'PPC sales',    'value': _money(sales)},
            {'label': 'Sales share',  'value': f'{sa_share:.1f}%'},
            {'label': 'ROAS',         'value': f'{r["roas"]:.2f}' if r.get('roas') is not None else '—'},
            {'label': 'ACOS',         'value': f'{r["acos"]:.1f}%' if r.get('acos') is not None else '—'},
        ]
        if sa_share >= sp_share * SHARE_RATIO and sales > 0 and sp_share > 0:
            mult = sa_share / sp_share
            out.append(_opp(
                SCALE, title=r['campaign_name'], subject=r['campaign_id'],
                evidence=base_ev,
                reason=(f'Produces {mult:.1f}× its share of ad spend in PPC sales '
                        f'({sa_share:.1f}% of sales from {sp_share:.1f}% of spend).'),
                # What is at stake: the sales this campaign already returns —
                # the size of the thing being under-funded.
                at_stake=sales, confidence_label=conf_label,
                confidence_note=conf_note, level='campaign', drill=drill))
        elif sp_share >= sa_share * SHARE_RATIO and sp_share > 0:
            # P3.14 — a ZERO-sales verdict is only trustworthy when the spend
            # itself came from Amazon's advertised-product report (the same
            # report the sales join reads). Under a revenue-share/cold-start
            # fallback the spend was spread by estimate, so "no sales" may be
            # an artifact of the allocation, not real waste. Skip those.
            if sales <= 0 and r.get('attribution_source') not in (
                    'sp_advertised_product', 'sp_provisional'):
                continue
            mult = (sp_share / sa_share) if sa_share > 0 else None
            out.append(_opp(
                EFFICIENCY, title=r['campaign_name'], subject=r['campaign_id'],
                evidence=base_ev,
                reason=(f'Consumes {sp_share:.1f}% of this SKU\'s ad spend but '
                        + (f'returns only {sa_share:.1f}% of its PPC sales '
                           f'({mult:.1f}× its share).' if mult
                           else 'has no attributed PPC sales in this window.')),
                # At stake: the spend that is not pulling its weight.
                at_stake=spend * (1 - (sa_share / sp_share if sp_share else 0)),
                confidence_label=conf_label, confidence_note=conf_note,
                level='campaign', drill=drill))

    # SKU-level signals — reuse P1 verbatim, no second calculation.
    rev = float(context.get('revenue') or 0)
    if 'ppc_dependent' in signals and context.get('ppc_share_pct') is not None:
        out.append(_opp(
            PPC_DEPENDENCY, title=sku, subject=sku,
            evidence=[
                {'label': 'Revenue',           'value': _money(rev)},
                {'label': 'PPC sales (est.)',  'value': _money(context.get('ppc_sales') or 0)},
                {'label': 'PPC dependency',    'value': f'{context["ppc_share_pct"]:.0f}%'},
                {'label': 'TACoS',             'value': f'{context["tacos"]:.1f}%' if context.get('tacos') is not None else '—'},
            ],
            reason=(f'{context["ppc_share_pct"]:.0f}% of revenue is attributed to '
                    f'paid traffic (P1 threshold: above '
                    f'{int(_threshold("ppc_dependent") * 100)}%).'),
            at_stake=float(context.get('ppc_sales') or 0),
            confidence_label=('estimated' if context.get('organic_flag') != 'unavailable'
                              else 'unknown'),
            confidence_note=('PPC/organic split is an estimate — SP/SD attributed by '
                             'SKU (7-day), SB by ASIN (14-day).'),
            level='sku', drill=None))

    if 'organic_down' in signals:
        d = context.get('deltas') or {}
        out.append(_opp(
            ORGANIC_DECLINE, title=sku, subject=sku,
            evidence=[
                {'label': 'Organic (est.)',   'value': _money(context.get('organic') or 0)},
                {'label': 'Organic Δ',        'value': f'{d.get("organic"):+.0f}%' if d.get('organic') is not None else '—'},
                {'label': 'PPC sales Δ',      'value': f'{d.get("ppc_sales"):+.0f}%' if d.get('ppc_sales') is not None else '—'},
                {'label': 'PPC dependency',   'value': f'{context["ppc_share_pct"]:.0f}%' if context.get('ppc_share_pct') is not None else '—'},
            ],
            reason=(f'Organic (est.) fell more than '
                    f'{int(_threshold("organic_down_rel") * 100)}% while PPC sales held '
                    f'or grew — paid is replacing organic rather than adding to it.'),
            at_stake=abs(float(context.get('organic') or 0)
                         * (float(d.get('organic') or 0) / 100)),
            confidence_label='estimated',
            confidence_note=('Organic is derived (revenue − attributed ad sales) and '
                             'carries the SP/SB/SD window caveats.'),
            level='sku', drill=None))

    # Ranked by money at stake, then capped — P3.10 asks for a compact panel,
    # not every finding. The full driver table sits directly below, so nothing
    # is hidden: the cards are the headline, the table is the detail.
    out.sort(key=lambda o: o['at_stake'], reverse=True)
    return out[:limit]


# ── Campaign level: targets and search terms ───────────────────────────────
# Rules REUSED from _tag_search_term (apps.dashboard.views) — the same tags the
# Search Terms page and the Optimizer already show. No new thresholds.
_TAG_TO_KIND = {
    'high_spend_no_sales':  TERM_WASTE,
    'losing_money':         TERM_WASTE,
    'high_ctr_low_cvr':     TERM_CVR,
    'scaling_opportunity':  TERM_SCALE,
    'high_profit':          TERM_SCALE,
}


def entity_opportunities(*, rows, level, campaign_id, tagger, drill_builder=None,
                         limit=6):
    """Target- or search-term-level opportunities from rows the campaign page
    already fetched. `tagger` is the existing `_tag_search_term`.

    Emits at most `limit` cards, ranked by money at stake, so a campaign with
    2,000 terms produces a readable set rather than a wall.
    """
    out = []
    for r in rows:
        spend = float(r.get('spend') or 0)
        sales = float(r.get('sales') or 0)
        clicks = int(r.get('clicks') or 0)
        impr = int(r.get('impressions') or 0)
        if spend <= 0 and sales <= 0:
            continue                      # nothing happened — not an opportunity
        acos = (spend / sales) if sales else None
        tags = tagger(spend=spend, sales=sales, orders=int(r.get('orders') or 0),
                      clicks=clicks, impr=impr,
                      est_profit=float(r.get('estimated_profit') or 0), acos=acos)
        kind = next((_TAG_TO_KIND[t] for t in tags if t in _TAG_TO_KIND), None)
        if kind is None:
            continue
        name = r.get('search_term') or r.get('expression') or r.get('target_id') or '—'
        ev = [
            {'label': 'Spend',   'value': _money(spend)},
            {'label': 'Sales',   'value': _money(sales)},
            {'label': 'Orders',  'value': f'{int(r.get("orders") or 0):,}'},
            {'label': 'Clicks',  'value': f'{clicks:,}'},
            {'label': 'CTR',     'value': f'{r["ctr"]:.2f}%' if r.get('ctr') is not None else '—'},
            {'label': 'CVR',     'value': f'{r["cvr"]:.2f}%' if r.get('cvr') is not None else '—'},
            {'label': 'ACOS',    'value': f'{acos * 100:.1f}%' if acos else '—'},
            {'label': 'ROAS',    'value': f'{(sales / spend):.2f}' if spend else '—'},
        ]
        if kind == TERM_WASTE:
            reason = (f'{_money(spend)} spent across {clicks:,} clicks with '
                      + ('no attributed sales.' if sales <= 0
                         else f'only {_money(sales)} back (ACOS {acos * 100:.0f}%).'))
            at_stake = spend if sales <= 0 else max(spend - sales, 0)
        elif kind == TERM_SCALE:
            reason = (f'Returns {(sales / spend):.1f}× its spend '
                      f'({_money(sales)} from {_money(spend)}).') if spend else \
                     f'Returns {_money(sales)} in sales.'
            at_stake = sales
        else:   # TERM_CVR
            reason = (f'{clicks:,} clicks converted at '
                      f'{(r.get("cvr") or 0):.2f}% — traffic arrives but the offer '
                      f'does not close.')
            at_stake = spend
        drill = drill_builder(r) if drill_builder else None
        out.append(_opp(kind, title=name, subject=name, evidence=ev, reason=reason,
                        at_stake=at_stake, confidence_label='authoritative',
                        confidence_note=('From Amazon\'s search-term/targeting report '
                                         'for this campaign and window.'),
                        level=level, drill=drill,
                        key=f'{kind}:{level}:{campaign_id}:{name}'))
    out.sort(key=lambda o: o['at_stake'], reverse=True)
    return out[:limit]


# ── External systems: STI + AIRecommendation (surface, never duplicate) ─────
def sti_cards(sti_rows, *, level, dedupe_subjects=frozenset()):
    """Map existing StiOpportunity records to the shared card shape.

    The evidence JSON the STI generator already stores is what gets shown —
    this does not re-score, re-rank or re-explain anything.
    """
    out = []
    for o in sti_rows:
        subj = (o.subject or '').strip()
        if subj and subj.lower() in dedupe_subjects:
            continue                     # a Pulse card already covers this subject
        ev = []
        for k, v in list((o.evidence or {}).items())[:6]:
            if isinstance(v, (int, float)):
                ev.append({'label': k.replace('_', ' ').title(),
                           'value': f'{v:,.2f}' if isinstance(v, float) else f'{v:,}'})
            elif isinstance(v, str) and len(v) < 40:
                ev.append({'label': k.replace('_', ' ').title(), 'value': v})
        out.append(_opp(
            o.opp_type, title=o.title,
            subject=subj or o.opp_type,
            evidence=ev, reason=(o.why or '').strip()[:400],
            at_stake=float(o.score or 0),
            confidence_label=o.confidence,      # existing high/medium/low
            confidence_note='Search Intelligence estimate (expected contribution '
                            'margin per month).',
            level=level, source='sti', status=o.status,
            key=f'sti:{o.key}'))
    return out


def ai_cards(ai_rows, *, level):
    """Map existing AIRecommendation records to the shared card shape.

    P3.6: these are contextual intelligence, NOT executable actions, and
    deterministic data wins where they disagree — so AI cards are always sorted
    below Pulse cards by carrying no `at_stake` claim of their own unless the
    record supplies one.
    """
    out = []
    for r in ai_rows:
        ev = []
        if r.evidence:
            for line in str(r.evidence).split('\n')[:5]:
                line = line.strip(' -•\t')
                if line:
                    ev.append({'label': '', 'value': line[:120]})
        if r.projected_impact:
            ev.append({'label': 'Projected impact', 'value': r.projected_impact})
        out.append(_opp(
            r.category, title=r.headline, subject=r.scope_name or r.scope_id,
            evidence=ev, reason=(r.suggested_action or '').strip()[:400],
            at_stake=0,                    # never outranks measured Pulse cards
            confidence_label=('high' if float(r.confidence or 0) >= 0.8
                              else 'medium' if float(r.confidence or 0) >= 0.5
                              else 'low'),
            confidence_note=f'AI recommendation ({r.ai_model or "model"}), '
                            f'confidence {float(r.confidence or 0):.2f}. '
                            f'Deterministic Pulse figures take precedence.',
            level=level, source='ai', status=r.status,
            key=f'ai:{r.recommendation_id}'))
    return out


def dedupe(cards):
    """P3.12 — one underlying issue, one card.

    Keys are (kind, level, subject); the first occurrence wins because callers
    append in priority order (Pulse deterministic → STI → AI). Cards are not
    merged across levels: the SKU panel shows campaign-level cards and the
    campaign page shows target/term-level cards, each linking deeper rather
    than repeating the same finding.
    """
    seen, out = set(), []
    for c in cards:
        k = c['key']
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


# ── Threshold access (single source of truth = the P1 engine) ──────────────
def _threshold(name):
    from .views import SKU_SIGNAL_THRESHOLDS
    return SKU_SIGNAL_THRESHOLDS[name]


def _min_spend():
    return _threshold('min_spend')
