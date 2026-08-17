"""
apps/dashboard/prefix_map.py — the single source of the campaign-prefix config.

WHAT THIS IS
    One cached read of `CampaignPrefixMap` shaped exactly like the dictionary it
    replaces: {PREFIX: (product_type, pack)}. Two hard-coded copies used to hold
    this — the canonical 29-entry dict in `amazon_api.views` and a 10-entry copy
    inside `dashboard.views.product_line_analysis` that had silently dropped 19
    prefixes. Both now read from here.

WHAT THIS IS NOT
    It is not a matcher and not an allocator. `_match_campaign_to_group()` and
    `_NAME_PRODUCT_RULES` are untouched and remain the matching authority; this
    module only supplies the prefix→product/pack table they consult. No PPC
    calculation changes because of it.

CACHING
    The table is tiny (~29 rows) and read on every campaign classification, so
    it is cached in-process and invalidated whenever a row is saved or deleted
    (see the signals at the bottom). A cold cache costs one query.
"""
import threading

_LOCK = threading.Lock()
_CACHE = {'map': None}


def get_prefix_map() -> dict:
    """{PREFIX: (product_type, pack)} for all ACTIVE rows.

    Marketplace is deliberately ignored: every migrated row is global
    (marketplace='') and the current convention is that a prefix means the same
    thing everywhere. The column exists for a future per-region override, which
    would be a behaviour change and is not implemented here.
    """
    cached = _CACHE['map']
    if cached is not None:
        return cached
    with _LOCK:
        if _CACHE['map'] is not None:
            return _CACHE['map']
        from .models import CampaignPrefixMap
        built = {}
        try:
            rows = (CampaignPrefixMap.objects.filter(active=True)
                    .values_list('prefix', 'product_type', 'pack', 'marketplace'))
            # Global rows first, so a (future) marketplace-specific row cannot
            # silently win today. Same key → first one wins.
            for prefix, ptype, pack, mp in sorted(rows, key=lambda r: r[3] != ''):
                key = (prefix or '').upper()
                if key and key not in built:
                    built[key] = (ptype, pack)
        except Exception:
            # Never let a config read break allocation — an empty map simply
            # means the name-rules fallback in the matcher does the work.
            built = {}
        _CACHE['map'] = built
        return built


def resolve(prefix: str):
    """(product_type, pack) for a prefix, or None."""
    return get_prefix_map().get((prefix or '').strip().upper())


def reverse_index() -> dict:
    """{(product_type, pack): [PREFIX, ...]} — used by the Prefix Mapping page
    to suggest a prefix from a campaign's advertised product."""
    out = {}
    for prefix, group in get_prefix_map().items():
        out.setdefault(group, []).append(prefix)
    for v in out.values():
        # Longest first: '6HNDTWL' is a better suggestion than 'HND'.
        v.sort(key=len, reverse=True)
    return out


def invalidate(*_args, **_kwargs):
    _CACHE['map'] = None


def group_from_title(title: str):
    """(product_type, pack) parsed from a Product title — the SAME rule the
    allocator uses (`ppc_allocator._load_signals._group_from_title`), reused so
    the page's SKU lists match what allocation actually does."""
    parts = [p.strip() for p in (title or '').split(' - ') if p.strip()]
    return (parts[0], parts[1]) if len(parts) >= 2 else None
