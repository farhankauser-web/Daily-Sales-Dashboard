"""
ProductGroup → the concrete ids every other module queries with.

SCOPING IS BY SKU/ASIN, FROM THE CATALOG. A product group is defined by
`Product.category`; the ad groups whose spend belongs to it are derived from
the advertised-product report (see `mapping.py`). Campaign names — and the
`initials` parsed from them — are display only and never enter a query.

That rule is not a preference, it is more correct. Measured on the dev snapshot
over 2026-07-06→2026-08-04: the old campaign-initials route covered 0% of UAE
and KSA (no campaign-dimension rows exist for them) and undercovered the UK,
where the dimension holds 74 campaigns against 315 that actually advertised.
The ASIN route attributes 95.5–99.9% of search-term spend in all four.
"""
from dataclasses import dataclass, field
from datetime import date

from . import mapping


@dataclass
class GroupScope:
    """Everything resolved once per run, then passed down. No re-querying."""
    group_id:      int
    group_slug:    str
    group_name:    str
    lexicon_key:   str
    marketplace:   str
    date_from:     date
    date_to:       date

    # The scope proper — ad groups and how much of each belongs to this group.
    ad_group_weights: dict = field(default_factory=dict)   # {ad_group_id: 0-1}
    asins:         list = field(default_factory=list)
    skus:          list = field(default_factory=list)
    product_types: set  = field(default_factory=set)

    # Campaign ids are carried for DISPLAY ONLY — naming the campaign a user
    # must edit is what makes a recommendation executable. They never filter.
    campaign_ids:  list = field(default_factory=list)

    # Data-quality signals surfaced in the report footer rather than hidden.
    coverage: dict = field(default_factory=dict)

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    @property
    def ad_group_ids(self) -> list:
        return list(self.ad_group_weights.keys())

    @property
    def is_weighted(self) -> bool:
        """True when any ad group is shared with another product group."""
        return any(w < 0.999 for w in self.ad_group_weights.values())


# Which taxonomy product types a Product.category implies. Used to decide
# whether a search term is "our product" or "adjacent" within this group.
_CATEGORY_TO_TYPE = [
    ('bath sheet',          'bath_sheet'),
    ('bath towel',          'bath_towel'),
    ('hand towel',          'hand_towel'),
    ('wash cloth',          'washcloth'),
    ('washcloth',           'washcloth'),
    ('kitchen towel',       'kitchen_towel'),
    ('tea towel',           'kitchen_towel'),
    ('dish',                'kitchen_towel'),
    ('beach towel',         'beach_towel'),
    ('face towel',          'face_towel'),
    ('bath mat',            'bath_mat'),
    ('bedsheet',            'bedsheet'),
    ('bed sheet',           'bedsheet'),
    ('duvet',               'bedsheet'),
    ('pillow',              'pillowcase'),
    ('mattress',            'mattress_protector'),
    ('turkish towel',       'bath_towel'),
    ('blanket',             'bedsheet'),
]


def _types_for_categories(categories) -> set:
    out = set()
    for cat in categories or []:
        low = (cat or '').lower()
        for needle, ptype in _CATEGORY_TO_TYPE:
            if needle in low:
                out.add(ptype)
                break
    return out


def resolve(group, marketplace: str, date_from: date, date_to: date) -> GroupScope:
    """Resolve a ProductGroup into ad-group weights, ASINs, SKUs and types."""
    from ..models import AdsSearchTermDailySnapshot, Product

    scope = GroupScope(
        group_id=group.id, group_slug=group.slug, group_name=group.name,
        lexicon_key=group.lexicon_key, marketplace=marketplace,
        date_from=date_from, date_to=date_to,
    )

    # ── Catalog: what this group sells here ──────────────────────────────────
    products = Product.objects.filter(marketplace=marketplace)
    asins, skus = set(), set()
    if group.categories:
        for asin, sku in products.filter(category__in=group.categories).values_list('asin', 'sku'):
            if asin:
                asins.add(asin)
            if sku:
                skus.add(sku)

    for asin in group.extra_asins or []:
        asins.add(asin)
        for sku in products.filter(asin=asin).values_list('sku', flat=True):
            if sku:
                skus.add(sku)
    for asin in group.excluded_asins or []:
        asins.discard(asin)

    scope.asins = sorted(asins)
    scope.skus = sorted(skus)
    scope.product_types = _types_for_categories(group.categories)

    # ── Advertising: which ad groups spent on those ASINs, and how much ──────
    scope.ad_group_weights = mapping.group_ad_groups(
        marketplace, date_from, date_to, group.slug)

    # ── Campaign ids for display only ────────────────────────────────────────
    if scope.ad_group_weights:
        scope.campaign_ids = list(
            AdsSearchTermDailySnapshot.objects
            .filter(marketplace=marketplace, date__range=(date_from, date_to),
                    ad_group_id__in=scope.ad_group_ids)
            .order_by().values_list('campaign_id', flat=True).distinct()
        )

    return scope


def coverage_stats(marketplace: str, date_from: date, date_to: date) -> dict:
    """
    How much of the marketplace's search-term spend any product group can see.

    Replaces the old "campaigns with no parsed initials" measure, which counted
    a naming-convention failure. This counts the thing that actually matters:
    advertising spend the catalog cannot place.
    """
    all_weights = mapping.ad_group_weights(marketplace, date_from, date_to)
    return mapping.unattributed_spend(marketplace, date_from, date_to,
                                      set(all_weights.keys()))


def diagnose_empty(scope) -> str:
    """
    Explain WHY a scope resolved to nothing, precisely enough to act on.

    A blank report with no explanation cannot be told apart from a broken
    pipeline, so it never ships without a reason attached.
    """
    from ..models import AdsAdvertisedProductDailySnapshot, AdsSearchTermDailySnapshot

    if scope.ad_group_weights:
        return ''

    mp = scope.marketplace
    if not scope.asins:
        return (f'No products in the {mp.upper()} catalog carry this group\'s '
                f'categories, so nothing can be attributed to it. Check the '
                f'group\'s categories against the catalog.')

    advertised = (AdsAdvertisedProductDailySnapshot.objects
                  .filter(marketplace=mp, date__range=(scope.date_from, scope.date_to),
                          asin__in=scope.asins).exists())
    any_ads = (AdsSearchTermDailySnapshot.objects
               .filter(marketplace=mp, date__range=(scope.date_from, scope.date_to))
               .exists())

    if not any_ads:
        return f'No advertising data for {mp.upper()} in this window.'
    if not advertised:
        return (f'{mp.upper()} has advertising in this window, but none of this '
                f'group\'s {len(scope.asins)} ASINs were advertised in it. The '
                f'group exists in the catalog and is simply not being promoted '
                f'here — that is a business fact, not a data problem.')
    return (f'This group\'s ASINs were advertised in {mp.upper()}, but no ad '
            f'group reached the minimum attribution weight. Check for ad groups '
            f'shared across many product groups.')
