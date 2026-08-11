"""
Execution readiness — can we actually act on an opportunity?

This is the module that separates the Center from a keyword tool. Recommending
more spend into a stockout is worse than recommending nothing, so inventory is
a HARD GATE on every spend-type opportunity, and a blocked opportunity is
reported as blocked rather than quietly dropped: "there is $6k/mo here and
stock is why you can't have it" is itself the decision.
"""
from . import config as cfg


def inventory(scope) -> dict:
    """
    Stock cover for the group, from the latest InventorySnapshot per product.

    Returns {'min_cover', 'worst_sku', 'blocked', 'warn', 'has_data', 'skus'}.
    """
    from ..models import InventorySnapshot

    if not scope.asins:
        return {'has_data': False, 'blocked': False, 'warn': False,
                'min_cover': None, 'worst_sku': '', 'skus': []}

    rows = (InventorySnapshot.objects
            .filter(product__marketplace=scope.marketplace,
                    product__asin__in=scope.asins)
            .select_related('product')
            .order_by('product_id', '-date'))

    latest = {}
    for r in rows:
        if r.product_id not in latest:
            latest[r.product_id] = r

    items = []
    for r in latest.values():
        items.append({
            'sku':         r.product.sku or r.product.asin,
            'asin':        r.product.asin,
            'days_cover':  float(r.days_cover or 0),
            'fulfillable': int(r.afn_fulfillable or 0),
            'date':        r.date.isoformat(),
        })

    # Only products with real cover data can gate anything — a zero on a row
    # that was never computed is absence of data, not a stockout.
    covered = [i for i in items if i['days_cover'] > 0]
    if not covered:
        return {'has_data': False, 'blocked': False, 'warn': False,
                'min_cover': None, 'worst_sku': '', 'skus': items}

    worst = min(covered, key=lambda i: i['days_cover'])
    return {
        'has_data':  True,
        'min_cover': worst['days_cover'],
        'worst_sku': worst['sku'],
        'blocked':   worst['days_cover'] < cfg.INVENTORY_BLOCK_DAYS,
        'warn':      worst['days_cover'] < cfg.INVENTORY_WARN_DAYS,
        'skus':      sorted(items, key=lambda i: i['days_cover'])[:10],
    }


def listing_tokens(scope) -> dict:
    """
    The vocabulary our listings currently carry, from Product.title.

    Phase 1 uses titles only — bullets and A+ content are not synced into Pulse
    yet, so a token found only in bullets would read here as missing. That
    limitation is printed next to the listing opportunities rather than left
    for the reader to discover.
    """
    from ..models import Product

    titles = list(Product.objects.filter(marketplace=scope.marketplace,
                                         asin__in=scope.asins)
                  .values_list('title', flat=True))
    blob = ' '.join(t.lower() for t in titles if t)
    return {'blob': blob, 'title_count': len([t for t in titles if t])}


def token_missing(listing: dict, token: str) -> bool:
    """Is this customer word absent from every listing title in the group?"""
    if not listing.get('title_count'):
        return False        # no titles to check — claim nothing
    return token.lower() not in listing['blob']
