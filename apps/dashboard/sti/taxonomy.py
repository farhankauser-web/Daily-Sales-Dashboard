"""
Search-term classification — multi-dimensional tags, not a single bucket.

The design (v2 §5 / v1 §5.1) rejects one-bucket classification because
"is it a bath towel?", "is it white?", "is it a competitor?" are orthogonal
questions. A term carries all of them at once; the single displayed INTENT
TIER is derived afterwards, and derived RELATIVE TO THE GROUP being reported —
"kitchen towels" is an adjacent product inside a Bath Towels report and the
core product inside a Kitchen Towels one.
"""
import hashlib

from .lexicon import (
    LEXICON_VERSION, get_lexicon, ASIN_RE,
    PRODUCT_TYPE_LABELS, ATTRIBUTE_LABELS,
)

# Intent tiers, ordered from most to least valuable. The order is the display
# order and the priority order for tie-breaking.
TIER_BRANDED       = 'branded'
TIER_COMPETITOR    = 'competitor'
TIER_HIGH_INTENT   = 'high_intent'     # our product type + a qualifying attribute
TIER_PRODUCT_MATCH = 'product_match'   # our product type, no qualifier
TIER_GENERIC       = 'generic'         # head terms: "towels"
TIER_ADJACENT      = 'adjacent'        # another product type we sell
TIER_OFF_CATEGORY  = 'off_category'    # not ours at all — negative-keyword pool

TIER_ORDER = [TIER_BRANDED, TIER_COMPETITOR, TIER_HIGH_INTENT, TIER_PRODUCT_MATCH,
              TIER_GENERIC, TIER_ADJACENT, TIER_OFF_CATEGORY]

TIER_LABELS = {
    TIER_BRANDED:       'Branded',
    TIER_COMPETITOR:    'Competitor',
    TIER_HIGH_INTENT:   'High intent — this product',
    TIER_PRODUCT_MATCH: 'Product match',
    TIER_GENERIC:       'Generic category',
    TIER_ADJACENT:      'Adjacent product',
    TIER_OFF_CATEGORY:  'Off-category',
}


def term_hash(term: str) -> str:
    """SHA1(lower(term)) — must match AdsSearchTermDailySnapshot.search_term_hash
    and BASearchQueryWeekly.search_query_hash so all three join on one key."""
    return hashlib.sha1((term or '').strip().lower().encode('utf-8')).hexdigest()


def classify_term(term: str, lexicon_key: str = 'towel') -> dict:
    """
    Classify one search term into independent tag dimensions.

    Returns:
        {product_type, attributes[], room_usage, brand_class, is_asin}
    """
    lex = get_lexicon(lexicon_key)
    text = (term or '').strip()

    if ASIN_RE.match(text):
        return {'product_type': 'unknown', 'attributes': [], 'room_usage': None,
                'brand_class': 'competitor_brand', 'is_asin': True}

    brand_class = 'unbranded'
    if lex['our_brand'].search(text):
        brand_class = 'our_brand'
    elif lex['competitor'].search(text):
        brand_class = 'competitor_brand'

    product_type = 'unknown'
    for label, pattern in lex['product_types']:
        if pattern.search(text):
            product_type = label
            break

    attributes = [label for label, pattern in lex['attributes'] if pattern.search(text)]

    room_usage = None
    for label, pattern in lex['room_usage']:
        if pattern.search(text):
            room_usage = label
            break

    return {'product_type': product_type, 'attributes': attributes,
            'room_usage': room_usage, 'brand_class': brand_class, 'is_asin': False}


def derive_tier(tags: dict, group_product_types: set) -> str:
    """
    Collapse tags into ONE displayed tier, relative to the reported group.

    `group_product_types` is what this ProductGroup actually sells — so the
    same term legitimately lands in different tiers in different reports.
    """
    if tags.get('brand_class') == 'our_brand':
        return TIER_BRANDED
    if tags.get('is_asin') or tags.get('brand_class') == 'competitor_brand':
        return TIER_COMPETITOR

    ptype = tags.get('product_type') or 'unknown'

    if ptype in group_product_types:
        # A qualifying attribute (colour, pack, quality…) means the shopper has
        # already narrowed — that is the highest-converting kind of demand.
        return TIER_HIGH_INTENT if tags.get('attributes') else TIER_PRODUCT_MATCH
    if ptype == 'generic_towel':
        return TIER_GENERIC
    if ptype in ('non_towel', 'unknown'):
        return TIER_OFF_CATEGORY
    return TIER_ADJACENT


def node_key(product_type: str, attributes: list) -> str:
    """
    Stable identifier for a demand node — a product type plus a sorted
    attribute set. Sorting matters: "white 4-pack" and "4-pack white" are the
    same demand pool and must not become two nodes.
    """
    attrs = '+'.join(sorted(attributes or []))
    return f'{product_type}|{attrs}' if attrs else product_type


def node_label(product_type: str, attributes: list) -> str:
    """Human label for a demand node, e.g. 'Bath Towels · White · 4-Pack'."""
    base = PRODUCT_TYPE_LABELS.get(product_type, product_type)
    parts = [ATTRIBUTE_LABELS.get(a, a) for a in sorted(attributes or [])]
    return ' · '.join([base] + parts)


def sync_tags(marketplace: str, terms: dict, lexicon_key: str = 'towel') -> dict:
    """
    Ensure every term in `terms` ({hash: text}) has a current SearchTermTag row,
    and return {hash: tags}.

    Classification is persisted so the next run reuses it: the fact table can
    hold >100k distinct terms per marketplace and re-running the whole lexicon
    on every report would dominate the run. Rows classified under an older
    LEXICON_VERSION are re-classified here rather than in a separate job, so a
    lexicon change takes effect on the next report without an operator step.
    """
    from ..models import SearchTermTag

    existing = {
        r.search_term_hash: (r.tags, r.lexicon_version)
        for r in SearchTermTag.objects.filter(
            marketplace=marketplace, search_term_hash__in=list(terms.keys()))
    }

    out, to_create, to_update = {}, [], []
    for h, text in terms.items():
        hit = existing.get(h)
        if hit and hit[1] == LEXICON_VERSION:
            out[h] = hit[0]
            continue
        tags = classify_term(text, lexicon_key)
        out[h] = tags
        row = SearchTermTag(marketplace=marketplace, search_term_hash=h,
                            search_term=text[:512], tags=tags,
                            lexicon_version=LEXICON_VERSION)
        (to_update if hit else to_create).append(row)

    if to_create:
        SearchTermTag.objects.bulk_create(to_create, batch_size=1000,
                                          ignore_conflicts=True)
    if to_update:
        # bulk_update needs pks; a targeted per-hash update is simpler and this
        # path only runs after a lexicon bump.
        for row in to_update:
            SearchTermTag.objects.filter(
                marketplace=marketplace, search_term_hash=row.search_term_hash
            ).update(tags=row.tags, lexicon_version=LEXICON_VERSION,
                     search_term=row.search_term)

    return out
