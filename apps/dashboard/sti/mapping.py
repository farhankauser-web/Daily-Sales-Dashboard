"""
ad group → advertised ASIN → product group.

THE RULE THIS ENFORCES: everything under Marketing scopes by SKU/ASIN as
defined in the product catalog. Campaign naming is for reading a report, never
for deciding what a number contains.

This module is deliberately the ONLY place that derives the mapping, and its
signature carries no Search-Intelligence concepts — it takes a marketplace and
a window, and returns ad-group weights keyed by product-group slug. Pulse
already holds four different notions of "product group" (campaign initials,
title splitting, a campaign-name-prefix dict for SB/SD, and this one), and
`MKT-ALLOC-002` records what happens when two of them drift. A fourth is only
justified if it can absorb the others, so this is written to be adopted by the
PPC allocator later rather than duplicated inside it.

WHY AD GROUP, NOT CAMPAIGN. The search-term fact table has no ASIN column —
Amazon does not report search terms per ASIN, so attribution has to route
through something. Both fact tables carry `ad_group_id` on every row, and the
ad group is where ASINs are actually attached, which makes it the finest grain
available. Measured on the dev snapshot: ad groups are single-group for 243 of
254 (USA) and 68 of 73 (UK) with 0.00% of spend in mixed ones; UAE and KSA have
5.1% and 9.2% in genuinely mixed ad groups, which is what the weights are for.
"""
from collections import defaultdict

from django.db.models import Sum

# An ad group contributing less than this share of a group's demand is noise;
# including it would attribute spend on the strength of a rounding error.
MIN_AD_GROUP_WEIGHT = 0.05


def asin_to_group(marketplace: str, groups=None) -> dict:
    """
    {asin: group_slug} for one marketplace, from the catalog alone.

    Membership is `Product.category ∈ ProductGroup.categories`, plus the
    per-group manual overrides. A SKU implies its category — an existing Pulse
    invariant — so this needs no name parsing anywhere.
    """
    from ..models import Product, ProductGroup

    groups = list(groups if groups is not None
                  else ProductGroup.objects.filter(active=True))

    cat_to_group, extra, excluded = {}, {}, defaultdict(set)
    for g in groups:
        for c in (g.categories or []):
            cat_to_group[c] = g.slug
        for a in (g.extra_asins or []):
            extra[a] = g.slug
        for a in (g.excluded_asins or []):
            excluded[g.slug].add(a)

    out = {}
    rows = Product.objects.filter(marketplace=marketplace).values_list('asin', 'category')
    for asin, category in rows:
        slug = cat_to_group.get(category)
        if slug and asin:
            out[asin] = slug

    out.update({a: s for a, s in extra.items()})           # overrides win
    return {a: s for a, s in out.items() if a not in excluded.get(s, ())}


def ad_group_weights(marketplace: str, date_from, date_to, groups=None) -> dict:
    """
    {ad_group_id: {group_slug: weight}} over the window, weights summing to 1.

    Derived from the advertised-product report — Amazon's own statement of
    which ASINs an ad group ran. Weighting is by spend, because that is what we
    are attributing; where an ad group recorded no spend (new or paused) the
    weight falls back to an equal split across its distinct ASINs, so the ad
    group still routes correctly instead of vanishing.
    """
    from ..models import AdsAdvertisedProductDailySnapshot

    a2g = asin_to_group(marketplace, groups)
    if not a2g:
        return {}

    rows = (AdsAdvertisedProductDailySnapshot.objects
            .filter(marketplace=marketplace, date__range=(date_from, date_to))
            .values('ad_group_id', 'asin')
            .annotate(spend=Sum('spend')))

    by_spend = defaultdict(lambda: defaultdict(float))
    by_count = defaultdict(lambda: defaultdict(int))
    for r in rows:
        slug = a2g.get(r['asin'])
        if not slug or not r['ad_group_id']:
            continue
        by_spend[r['ad_group_id']][slug] += float(r['spend'] or 0)
        by_count[r['ad_group_id']][slug] += 1

    out = {}
    for ag, mix in by_spend.items():
        total = sum(mix.values())
        if total > 0:
            weights = {s: v / total for s, v in mix.items()}
        else:
            counts = by_count[ag]
            n = sum(counts.values()) or 1
            weights = {s: c / n for s, c in counts.items()}

        weights = {s: w for s, w in weights.items() if w >= MIN_AD_GROUP_WEIGHT}
        if not weights:
            continue
        # Re-normalise after dropping noise so the ad group still accounts for
        # itself exactly once.
        norm = sum(weights.values())
        out[ag] = {s: w / norm for s, w in weights.items()}

    return out


def group_ad_groups(marketplace: str, date_from, date_to, group_slug: str,
                    groups=None) -> dict:
    """{ad_group_id: weight} for one product group — what a report scopes to."""
    return {ag: mix[group_slug]
            for ag, mix in ad_group_weights(marketplace, date_from, date_to, groups).items()
            if group_slug in mix}


def unattributed_spend(marketplace: str, date_from, date_to, known_ad_groups) -> dict:
    """
    Search-term spend whose ad group the catalog cannot place, and therefore
    that belongs to no product group.

    Reported in the footer rather than dropped: spend nobody can see is the
    failure mode this whole rework exists to remove.
    """
    from ..models import AdsSearchTermDailySnapshot

    qs = AdsSearchTermDailySnapshot.objects.filter(
        marketplace=marketplace, date__range=(date_from, date_to))
    total = float(qs.aggregate(s=Sum('spend'))['s'] or 0)
    placed = float(qs.filter(ad_group_id__in=list(known_ad_groups))
                   .aggregate(s=Sum('spend'))['s'] or 0)
    return {
        'total': round(total, 2),
        'placed': round(placed, 2),
        'unplaced': round(total - placed, 2),
        'coverage_pct': round(placed / total * 100, 1) if total > 0 else 0.0,
    }
