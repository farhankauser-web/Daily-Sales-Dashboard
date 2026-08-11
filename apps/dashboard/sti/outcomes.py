"""
Did acting on it actually work?

The reason the Center stores runs, keys opportunities stably, and runs on named
periods at all. Everything else recommends; this is the only part that checks
whether the recommendation was any good — and it is what a keyword tool cannot
do, because it does not hold your margins or your history.

THE MEASUREMENT. An opportunity marked done records the period it was acted in
(`StiOpportunity.acted_period_key`). Its subject is then measured in that period
and in the following one. Because periods are Amazon's own fixed weeks, the two
observations cover the same number of days on the same grid — a comparison that
was impossible while windows rolled.

WHAT "BETTER" MEANS DIFFERS BY TYPE, and must be stated rather than assumed.
Negating a wasteful term succeeds when its SPEND FALLS. Scaling succeeds when
SALES RISE. A listing fix succeeds when CONVERSION rises. Applying one direction
to all of them would score a successful negative as a catastrophic collapse.
"""
from dataclasses import dataclass

from . import periods as periods_mod

# metric · direction that counts as success · what to call it on screen
MEASURES = {
    'defend':        ('spend',  'down', 'Wasted spend'),
    'scale_ppc':     ('sales',  'up',   'Sales'),
    'listing_fix':   ('cvr',    'up',   'Conversion rate'),
    'conquest':      ('acos',   'down', 'Conquest ACOS'),
    'organic_push':  ('share',  'up',   'Organic share'),
    'capture_share': ('share',  'up',   'Organic share'),
    # A product launch is not measurable by next-period metrics on a search term.
    'product_gap':   None,
}

# Movement smaller than this is noise, not a result.
MATERIAL_PCT = 10.0

# Metrics that scale with the number of days observed. If the result period has
# fewer days of data than the acted period, these MUST be compared per day or a
# data gap reads as a decline. Found in testing: Week 28 held 7/7 days and Week
# 29 only 6/7, which alone depresses money and counts by ~14% — enough to score
# a successful action as "worsened".
VOLUME_METRICS = {'spend', 'sales', 'orders', 'clicks'}

# Below this share of the acted period's coverage, the result period is too thin
# to judge at all and the verdict waits.
MIN_COVERAGE_RATIO = 0.6


@dataclass
class Outcome:
    opportunity_id: int
    key:        str
    title:      str
    opp_type:   str
    subject:    str
    group:      str
    marketplace: str
    metric:     str
    metric_label: str
    good_when:  str
    acted_period:  str
    result_period: str
    before:     float | None
    after:      float | None
    delta_pct:  float | None
    verdict:    str      # improved · worsened · flat · pending · unmeasurable
    note:       str
    score_at_action: float


def _ads_days(marketplace: str, start, end) -> int:
    """Days of advertising data actually present — not days the period spans."""
    from ..models import AdsSearchTermDailySnapshot
    return (AdsSearchTermDailySnapshot.objects
            .filter(marketplace=marketplace, date__range=(start, end))
            .order_by().values('date').distinct().count())


def _term_metrics(scope, subjects: set) -> dict:
    """{subject_lower: {spend, sales, orders, clicks, cvr, acos}} for one period."""
    from . import spine
    sp = spine.build(scope)
    out = {}
    for t in sp['terms'].values():
        key = t['term'].strip().lower()
        if subjects and key not in subjects:
            continue
        out[key] = t
    out['__totals__'] = sp['totals']
    # Portfolio subjects are aggregates over a class of terms, not one term.
    out['__long_tail__'] = sp['totals']
    return out


def _query_shares(scope, period) -> dict:
    """{query_lower: share} for one period, or empty when Amazon published none."""
    from . import market
    window = market.resolve_window(scope.marketplace, scope.asins, period)
    if not window['has_data']:
        return {}
    mk = market.build(scope, window)
    return {q['query'].strip().lower(): q['share'] for q in mk['queries'].values()}


def _conquest_acos(scope, tags: dict) -> float | None:
    from . import spine
    sp = spine.build(scope)
    rows = [t for h, t in sp['terms'].items() if (tags.get(h) or {}).get('is_asin')]
    spend = sum(t['spend'] for t in rows)
    sales = sum(t['sales'] for t in rows)
    return (spend / sales) if sales > 0 else None


def measure_group(group, marketplace: str, opportunities: list) -> list:
    """
    Measure every acted-on opportunity for one group and marketplace.

    Spines are built once per period rather than once per opportunity — two
    builds regardless of how many opportunities were acted on.
    """
    from . import scope as scope_mod
    from .taxonomy import sync_tags

    by_period: dict = {}
    for o in opportunities:
        if o.acted_period_key:
            by_period.setdefault(o.acted_period_key, []).append(o)

    results = []
    for acted_key, opps in by_period.items():
        result_key = periods_mod.next_key(acted_key)
        try:
            a0, a1 = periods_mod.bounds(acted_key)
            r0, r1 = periods_mod.bounds(result_key)
        except (ValueError, IndexError):
            continue

        acted_scope = scope_mod.resolve(group, marketplace, a0, a1)
        result_scope = scope_mod.resolve(group, marketplace, r0, r1)

        # Days of advertising data actually present in each period — the basis
        # for normalising volume metrics rather than comparing raw totals.
        acted_days = _ads_days(marketplace, a0, a1)
        result_days = _ads_days(marketplace, r0, r1)
        coverage_ratio = (result_days / acted_days) if acted_days else 0.0

        subjects = {o.subject.strip().lower() for o in opps}
        before_terms = _term_metrics(acted_scope, subjects)
        after_terms = _term_metrics(result_scope, subjects)

        needs_share = any(MEASURES.get(o.opp_type) and
                          MEASURES[o.opp_type][0] == 'share' for o in opps)
        before_share = _query_shares(acted_scope, periods_mod.resolve(
            acted_key, marketplace, asins=acted_scope.asins)) if needs_share else {}
        after_share = _query_shares(result_scope, periods_mod.resolve(
            result_key, marketplace, asins=result_scope.asins)) if needs_share else {}

        needs_conquest = any(o.opp_type == 'conquest' for o in opps)
        conquest_before = conquest_after = None
        if needs_conquest:
            from . import spine as spine_mod
            for sc, setter in ((acted_scope, 'b'), (result_scope, 'a')):
                sp = spine_mod.build(sc)
                tags = sync_tags(marketplace, {h: t['term'] for h, t in sp['terms'].items()},
                                 sc.lexicon_key)
                val = _conquest_acos(sc, tags)
                if setter == 'b':
                    conquest_before = val
                else:
                    conquest_after = val

        for o in opps:
            spec = MEASURES.get(o.opp_type)
            common = dict(opportunity_id=o.id, key=o.key, title=o.title,
                          opp_type=o.opp_type, subject=o.subject,
                          group=group.name, marketplace=marketplace,
                          acted_period=acted_key, result_period=result_key,
                          score_at_action=float(o.score))
            if spec is None:
                results.append(Outcome(
                    **common, metric='—', metric_label='—', good_when='—',
                    before=None, after=None, delta_pct=None,
                    verdict='unmeasurable',
                    note='A product decision is not measurable from next-period '
                         'search-term metrics.'))
                continue

            metric, good_when, label = spec
            subj = o.subject.strip().lower()

            if metric == 'share':
                before = before_share.get(subj)
                after = after_share.get(subj)
                missing_note = ('Amazon has not published market data for one of these '
                                'periods, so share cannot be compared yet.')
            elif o.opp_type == 'conquest':
                before, after = conquest_before, conquest_after
                missing_note = 'No conquest spend in one of these periods.'
            else:
                b, a = before_terms.get(subj), after_terms.get(subj)
                before = b.get(metric) if b else None
                after = a.get(metric) if a else None
                missing_note = ('The term did not appear in one of these periods — '
                                'often the point, when the action was a negative keyword.')

            if before is None or after is None:
                # A negated term vanishing IS the success case; say so instead of
                # reporting "no data".
                if (o.opp_type == 'defend' and before is not None and after is None):
                    results.append(Outcome(
                        **common, metric=metric, metric_label=label,
                        good_when=good_when, before=float(before), after=0.0,
                        delta_pct=-100.0, verdict='improved',
                        note='The term stopped spending entirely in the following period.'))
                    continue
                results.append(Outcome(
                    **common, metric=metric, metric_label=label, good_when=good_when,
                    before=float(before) if before is not None else None,
                    after=float(after) if after is not None else None,
                    delta_pct=None, verdict='pending', note=missing_note))
                continue

            before, after = float(before), float(after)

            # Normalise volume metrics to a per-day rate so a short result
            # period cannot masquerade as a decline.
            normalised = ''
            if metric in VOLUME_METRICS and acted_days and result_days \
                    and acted_days != result_days:
                if coverage_ratio < MIN_COVERAGE_RATIO:
                    results.append(Outcome(
                        **common, metric=metric, metric_label=label,
                        good_when=good_when, before=before, after=after,
                        delta_pct=None, verdict='pending',
                        note=(f'{result_key} holds only {result_days} days of advertising '
                              f'data against {acted_days} in {acted_key} — too thin to '
                              f'judge. Re-check once the period fills.')))
                    continue
                before = before / acted_days
                after = after / result_days
                normalised = (f' Compared per day ({acted_days} vs {result_days} days of '
                              f'data) so the shorter period is not read as a decline.')

            if before == 0:
                delta = 100.0 if after > 0 else 0.0
            else:
                delta = (after - before) / abs(before) * 100.0

            if abs(delta) < MATERIAL_PCT:
                verdict = 'flat'
                note = 'Movement below the 10% materiality floor.' + normalised
            else:
                moved_up = delta > 0
                good = (moved_up and good_when == 'up') or \
                       (not moved_up and good_when == 'down')
                verdict = 'improved' if good else 'worsened'
                note = (f'{label} {"rose" if moved_up else "fell"} {abs(delta):.0f}%; '
                        f'success for this type is "{good_when}".' + normalised)

            results.append(Outcome(
                **common, metric=metric, metric_label=label, good_when=good_when,
                before=before, after=after, delta_pct=round(delta, 1),
                verdict=verdict, note=note))

    return results


def scoreboard(marketplace: str = '', group=None) -> dict:
    """Every measurable acted-on opportunity, with a headline tally."""
    from ..models import ProductGroup, StiOpportunity

    qs = StiOpportunity.objects.filter(status='done').exclude(acted_period_key='')
    if marketplace:
        qs = qs.filter(marketplace=marketplace)
    if group:
        qs = qs.filter(product_group=group)

    rows = []
    pairs = {(o.product_group_id, o.marketplace) for o in qs}
    for gid, mp in pairs:
        g = ProductGroup.objects.filter(id=gid).first()
        if not g:
            continue
        opps = [o for o in qs if o.product_group_id == gid and o.marketplace == mp]
        try:
            rows.extend(measure_group(g, mp, opps))
        except Exception:                       # noqa: BLE001 — one group must not
            import logging                       # take down the whole scoreboard
            logging.getLogger(__name__).exception('STI outcome measurement failed')

    tally = {'improved': 0, 'worsened': 0, 'flat': 0,
             'pending': 0, 'unmeasurable': 0}
    for r in rows:
        tally[r.verdict] = tally.get(r.verdict, 0) + 1

    judged = tally['improved'] + tally['worsened'] + tally['flat']
    return {
        'rows': sorted(rows, key=lambda r: (r.verdict != 'improved', -r.score_at_action)),
        'tally': tally,
        'judged': judged,
        'hit_rate': round(tally['improved'] / judged * 100, 1) if judged else None,
        'value_acted': round(sum(r.score_at_action for r in rows), 2),
    }
