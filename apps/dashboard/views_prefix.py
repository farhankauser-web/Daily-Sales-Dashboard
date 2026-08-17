"""
apps/dashboard/views_prefix.py — Prefix Mapping: management + diagnostics.

This page EXPOSES the existing campaign-classification system; it does not
implement a second one. Every number here is produced by the same functions the
PPC allocator uses:

    _match_campaign_to_group()      — the matcher (untouched)
    prefix_map.get_prefix_map()     — the config, now DB-backed
    prefix_map.group_from_title()   — the same Product.title parse the
                                      allocator uses to find a group's ASINs

"Unallocated" means exactly what it means to the allocator and to the existing
`unmapped_ppc_campaigns` command: the matcher returned no group for the
campaign name. There is no second definition.

Pulse cannot rename campaigns — the Ads integration has no write capability —
so this page suggests a corrected name to copy and never contacts Amazon.
"""
from collections import defaultdict
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Max, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.core.decorators import permission_required

from .prefix_map import get_prefix_map, group_from_title, reverse_index
from .views import _allowed_marketplaces

_WINDOW_DAYS = 30


def _window(request):
    try:
        days = max(1, min(int(request.GET.get('days') or _WINDOW_DAYS), 365))
    except ValueError:
        days = _WINDOW_DAYS
    end = date.today() - timedelta(days=1)
    return end - timedelta(days=days - 1), end, days


def _catalog_by_group(marketplace=''):
    """{(product_type, pack): [ {sku, asin, title, marketplace, status} ]}

    Built with the allocator's own title parse, so the SKUs listed here are the
    SKUs a campaign with that prefix actually resolves to.
    """
    from .models import Product
    qs = Product.objects.all()
    if marketplace:
        qs = qs.filter(marketplace=marketplace)
    out = defaultdict(list)
    for p in qs.values('sku', 'asin', 'title', 'marketplace', 'status'):
        g = group_from_title(p['title'])
        if g:
            out[g].append(p)
    return out


def _campaigns(marketplace, start, end):
    """One row per campaign in the window, with its live classification."""
    from apps.amazon_api.views import _match_campaign_to_group
    from .models import PPCCampaignSnapshot

    qs = PPCCampaignSnapshot.objects.filter(date__gte=start, date__lte=end)
    if marketplace:
        qs = qs.filter(marketplace=marketplace)
    rows = (qs.values('marketplace', 'campaign_id', 'campaign_name',
                      'campaign_type')
            .annotate(spend=Sum('spend'), last_seen=Max('date')))
    # A campaign can appear under more than one name across the window (it was
    # renamed). Keep the most recent name — that is what the matcher sees now.
    latest = {}
    for r in rows:
        key = (r['marketplace'], str(r['campaign_id']))
        cur = latest.get(key)
        if cur is None or (r['last_seen'] or start) > (cur['last_seen'] or start):
            if cur:
                r = {**r, 'spend': float(r['spend'] or 0) + cur['spend']}
            latest[key] = {**r, 'spend': float(r['spend'] or 0)}
        else:
            cur['spend'] += float(r['spend'] or 0)
    out = []
    for r in latest.values():
        g = _match_campaign_to_group(r['campaign_name'] or '')
        out.append({**r, 'group': g, 'allocated': bool(g)})
    return out


# ── Page ────────────────────────────────────────────────────────────────────
@login_required
@permission_required('can_view_dashboard')
def prefix_mapping(request):
    marketplace = request.GET.get('mp', '')
    if marketplace and not request.user.can_access_marketplace(marketplace):
        marketplace = ''
    return render(request, 'dashboard/prefix_mapping.html', {
        'marketplace': marketplace,
        'allowed_marketplaces': _allowed_marketplaces(request.user),
    })


# ── Tab A — prefixes, their products, SKUs and campaigns ────────────────────
@login_required
@permission_required('can_view_dashboard')
def api_prefix_mapping(request):
    from .models import CampaignPrefixMap

    marketplace = request.GET.get('mp', '')
    if marketplace and not request.user.can_access_marketplace(marketplace):
        return JsonResponse({'error': 'forbidden'}, status=403)
    start, end, days = _window(request)

    catalog = _catalog_by_group(marketplace)
    camps = _campaigns(marketplace, start, end)

    by_group = defaultdict(lambda: {'campaigns': [], 'spend': 0.0})
    for c in camps:
        if c['group']:
            b = by_group[tuple(c['group'])]
            b['campaigns'].append(c)
            b['spend'] += c['spend']

    # ── One row per PRODUCT · PACK, not per prefix ──────────────────────────
    # A product legitimately has several campaign-naming prefixes: US campaigns
    # are named 4BTH-…, the same product's UK campaigns PK4-… or LUX-…. Listing
    # a row per prefix repeated each product's SKUs once per alias, which reads
    # like duplicated configuration. Grouping by product shows every SKU set
    # exactly once, with its prefixes as a set — while every prefix stays
    # independently editable and deactivatable.
    all_maps = list(CampaignPrefixMap.objects.all())
    by_product = defaultdict(list)
    for m in all_maps:
        by_product[(m.product_type, m.pack)].append(m)

    def _prefix_of(name, candidates):
        """Which of this product's prefixes does the campaign name start with?"""
        n = (name or '').upper().replace(' ', '').lstrip('-')
        for p in sorted(candidates, key=len, reverse=True):
            if n.startswith(p.upper()):
                return p
        return ''

    rows = []
    for g, maps in by_product.items():
        skus = catalog.get(g, [])
        cg = by_group.get(g, {'campaigns': [], 'spend': 0.0})
        names = [m.prefix for m in maps]
        per_prefix = {m.prefix: {'n': 0, 'sp': 0.0} for m in maps}
        camp_rows = []
        for c in cg['campaigns']:
            pfx = _prefix_of(c['campaign_name'], names)
            if pfx:
                per_prefix[pfx]['n'] += 1
                per_prefix[pfx]['sp'] += c['spend']
            camp_rows.append({
                'campaign_id': c['campaign_id'],
                'campaign_name': c['campaign_name'],
                'marketplace': c['marketplace'],
                'campaign_type': c['campaign_type'],
                'spend': round(c['spend'], 2),
                'matched_prefix': pfx,     # '' = matched by the name rules
                'allocated': c['allocated'],
            })
        camp_rows.sort(key=lambda x: -x['spend'])
        rows.append({
            'product_type': g[0], 'pack': g[1],
            'key': f'{g[0]}|{g[1]}',
            'prefixes': sorted(
                ({'id': m.pk, 'prefix': m.prefix, 'active': m.active,
                  'note': m.note, 'marketplace': m.marketplace or '',
                  'campaign_count': per_prefix[m.prefix]['n'],
                  'spend': round(per_prefix[m.prefix]['sp'], 2),
                  'used': per_prefix[m.prefix]['n'] > 0}
                 for m in maps),
                key=lambda x: (-x['campaign_count'], x['prefix'])),
            'prefix_count': len(maps),
            'active_prefix_count': sum(1 for m in maps if m.active),
            'sku_count': len(skus),
            'asin_count': len({s['asin'] for s in skus if s['asin']}),
            'campaign_count': len(camp_rows),
            'spend': round(cg['spend'], 2),
            'skus': sorted(
                ({'sku': s['sku'], 'asin': s['asin'], 'title': s['title'],
                  'marketplace': s['marketplace'], 'status': s['status']}
                 for s in skus), key=lambda x: (x['sku'] or '')),
            'campaigns': camp_rows,
        })
    rows.sort(key=lambda r: (-r['spend'], r['product_type'], r['pack']))

    # Products that exist in the catalog but have NO prefix yet — the launch
    # path for a new pack size. Their campaigns would be unallocated until a
    # prefix is assigned, so they are surfaced rather than left to be noticed.
    mapped_groups = {(m.product_type, m.pack) for m in all_maps}
    unmapped_groups = sorted(
        ({'product_type': g[0], 'pack': g[1],
          'sku_count': len(v),
          'asin_count': len({s['asin'] for s in v if s['asin']}),
          'skus': sorted({s['sku'] for s in v if s['sku']})[:12]}
         for g, v in catalog.items() if g not in mapped_groups),
        key=lambda x: -x['sku_count'])

    unallocated = [c for c in camps if not c['allocated']]
    return JsonResponse({
        'unmapped_groups': unmapped_groups,
        'marketplace': marketplace, 'days': days,
        'period': {'start': start.isoformat(), 'end': end.isoformat()},
        'kpi': {
            'products': len(rows),
            'prefixes': len(all_maps),
            'active_prefixes': sum(1 for m in all_maps if m.active),
            'unused_prefixes': sum(
                1 for r in rows for p in r['prefixes']
                if p['active'] and not p['used']),
            'campaigns': len(camps),
            'allocated': len(camps) - len(unallocated),
            'unallocated': len(unallocated),
            'unallocated_spend': round(sum(c['spend'] for c in unallocated), 2),
        },
        'products': sorted({r['product_type'] for r in rows}),
        'packs': sorted({r['pack'] for r in rows}),
        'rows': rows,
        'note': ('Classification comes from the existing matcher '
                 '(_match_campaign_to_group) and the shared CampaignPrefixMap '
                 'config. This page changes no PPC calculation.'),
    })


# ── Tab B — unallocated campaigns, with an evidence-based suggestion ────────
@login_required
@permission_required('can_view_dashboard')
def api_prefix_unallocated(request):
    """Campaigns the existing matcher cannot classify.

    Same definition as the allocator and the `unmapped_ppc_campaigns` command:
    `_match_campaign_to_group(name)` returned None.

    The suggested prefix is NOT another name guess — the matcher's own name
    rules have already failed by definition. It is derived from what the
    campaign actually advertised: campaign → advertised ASIN → Product.title →
    (product, pack) → reverse lookup in the prefix config. Blank when the
    evidence is not unanimous.
    """
    from .models import AdsAdvertisedProductDailySnapshot, Product

    marketplace = request.GET.get('mp', '')
    if marketplace and not request.user.can_access_marketplace(marketplace):
        return JsonResponse({'error': 'forbidden'}, status=403)
    start, end, days = _window(request)

    camps = [c for c in _campaigns(marketplace, start, end) if not c['allocated']]
    ids = [c['campaign_id'] for c in camps]

    # campaign → advertised ASINs (evidence)
    ap = defaultdict(lambda: defaultdict(float))
    if ids:
        apqs = AdsAdvertisedProductDailySnapshot.objects.filter(
            campaign_id__in=ids, date__gte=start, date__lte=end)
        if marketplace:
            apqs = apqs.filter(marketplace=marketplace)
        for r in (apqs.values('campaign_id', 'asin')
                  .annotate(sp=Sum('spend'), sa=Sum('sales_7d'))):
            ap[str(r['campaign_id'])][(r['asin'] or '').upper()] += (
                float(r['sp'] or 0) + float(r['sa'] or 0))

    title_of_asin = {}
    for p in Product.objects.values('asin', 'title'):
        a = (p['asin'] or '').upper()
        if a and a not in title_of_asin:
            title_of_asin[a] = p['title']
    rev = reverse_index()

    rows = []
    for c in camps:
        evidence = ap.get(str(c['campaign_id']), {})
        groups = defaultdict(float)
        for asin, weight in evidence.items():
            g = group_from_title(title_of_asin.get(asin, ''))
            if g:
                groups[g] += weight
        suggested_prefix, suggested_group, confidence = '', None, ''
        reason = ('Campaign name does not contain a known prefix, and the name '
                  'rules did not identify a product.')
        if groups:
            total = sum(groups.values())
            top, top_w = max(groups.items(), key=lambda x: x[1])
            share = (top_w / total) if total else 0
            # Only suggest when the campaign's own advertising points
            # overwhelmingly at ONE product. Anything murkier stays blank —
            # a wrong suggestion is worse than none.
            if share < 0.8:
                reason += (' Its advertised products span several groups, so no '
                           'single prefix can be suggested.')
            elif not rev.get(top):
                # The evidence is unanimous but the product's own title does not
                # parse to any configured group — usually a malformed title
                # (e.g. "Kitchen Towel pack 6 - pack 6 - grey" instead of
                # "Kitchen Towel - 6-Pack - Grey"). Say so: the fix is the
                # catalog title or a new prefix, not the campaign name.
                reason += (f' Its advertised products resolve to '
                           f'"{top[0]} · {top[1]}", which no configured prefix '
                           f'covers — check the product title format or add a '
                           f'prefix for it.')
                suggested_group = list(top)
            else:
                suggested_prefix = rev[top][0]
                suggested_group = list(top)
                confidence = 'high' if share >= 0.95 else 'medium'
        else:
            reason += (' It has no advertised-product data in this window, so '
                       'there is no evidence to suggest a prefix from.')
        name = c['campaign_name'] or ''
        rows.append({
            'campaign_id': c['campaign_id'], 'campaign_name': name,
            'marketplace': c['marketplace'],
            'campaign_type': c['campaign_type'],
            'spend': round(c['spend'], 2),
            'last_seen': c['last_seen'].isoformat() if c['last_seen'] else None,
            'reason': reason,
            'suggested_prefix': suggested_prefix,
            'suggested_group': suggested_group,
            'suggestion_confidence': confidence,
            'suggested_name': f'{suggested_prefix}-{name}' if suggested_prefix else '',
            'evidence_asins': sorted(evidence, key=evidence.get, reverse=True)[:4],
        })
    rows.sort(key=lambda r: (-r['spend'], r['campaign_name']))
    return JsonResponse({
        'marketplace': marketplace, 'days': days,
        'period': {'start': start.isoformat(), 'end': end.isoformat()},
        'kpi': {'unallocated': len(rows),
                'spend': round(sum(r['spend'] for r in rows), 2),
                'with_suggestion': sum(1 for r in rows if r['suggested_prefix'])},
        'rows': rows,
        'rename_note': ('Pulse cannot rename Amazon campaigns — the Ads '
                        'integration is read-only. Copy the suggested name, '
                        'rename the campaign in Amazon Ads, and it will be '
                        'classified on the next sync.'),
    })


# ── Config edit (the only writes on this page) ──────────────────────────────
@login_required
@permission_required('can_manage_cogs')
@require_POST
def api_prefix_create(request):
    """Assign a prefix to a product — the launch path for a new pack size.

    The product/pack must match how the catalog writes it in Product.title
    (split on ' - '), because that is what the allocator uses to find a group's
    ASINs. The caller normally picks an existing catalog group, so this is
    exact by construction; a free-typed group is accepted but reported back
    with its SKU count so a mismatch is obvious immediately.
    """
    import json

    from .models import CampaignPrefixMap

    try:
        d = json.loads(request.body or '{}')
    except ValueError:
        d = {}
    prefix = (d.get('prefix') or '').strip().upper()
    product_type = (d.get('product_type') or '').strip()
    pack = (d.get('pack') or '').strip()

    if not prefix:
        return JsonResponse({'status': 'failed',
                             'message': 'Prefix is required.'}, status=400)
    if not product_type or not pack:
        return JsonResponse({'status': 'failed',
                             'message': 'Product and pack are both required.'},
                            status=400)
    if CampaignPrefixMap.objects.filter(prefix=prefix, marketplace='').exists():
        return JsonResponse(
            {'status': 'failed',
             'message': f'{prefix} already exists — edit it instead of adding '
                        f'a second mapping for the same prefix.'}, status=409)

    m = CampaignPrefixMap.objects.create(
        prefix=prefix, product_type=product_type, pack=pack,
        marketplace='', active=True,
        note=(d.get('note') or '')[:256])      # post_save clears the cache

    # Report how many catalog SKUs this group actually resolves to. Zero is
    # allowed — a product can be configured before it goes live — but the
    # caller is told, because zero also means a typo'd product/pack.
    skus = _catalog_by_group().get((product_type, pack), [])
    return JsonResponse({
        'status': 'ok', 'id': m.pk, 'prefix': m.prefix,
        'product_type': m.product_type, 'pack': m.pack,
        'sku_count': len(skus),
        'skus': sorted({s['sku'] for s in skus if s['sku']})[:12],
        'warning': ('No catalog products currently resolve to '
                    f'"{product_type} · {pack}". That is fine if the product '
                    'is not live yet — but check the spelling matches the '
                    'product title format, or campaigns will classify to an '
                    'empty group.') if not skus else '',
    })


@login_required
@permission_required('can_manage_cogs')
@require_POST
def api_prefix_save(request, pk: int):
    """Edit a prefix or toggle it active. Product/pack are intentionally not
    editable here: they must keep matching Product.title exactly, or a prefix
    would resolve to a group that owns no SKUs."""
    import json

    from .models import CampaignPrefixMap

    m = CampaignPrefixMap.objects.filter(pk=pk).first()
    if m is None:
        return JsonResponse({'status': 'failed', 'message': 'Not found.'},
                            status=404)
    try:
        d = json.loads(request.body or '{}')
    except ValueError:
        d = {}

    if 'prefix' in d:
        new = (d.get('prefix') or '').strip().upper()
        if not new:
            return JsonResponse({'status': 'failed',
                                 'message': 'Prefix cannot be empty.'}, status=400)
        if (CampaignPrefixMap.objects
                .filter(prefix=new, marketplace=m.marketplace)
                .exclude(pk=m.pk).exists()):
            return JsonResponse(
                {'status': 'failed',
                 'message': f'{new} already exists — one mapping per prefix.'},
                status=409)
        m.prefix = new
    if 'active' in d:
        m.active = bool(d['active'])
    if 'note' in d:
        m.note = (d.get('note') or '')[:256]
    m.save()      # post_save signal drops the resolver cache
    return JsonResponse({'status': 'ok', 'id': m.pk, 'prefix': m.prefix,
                         'active': m.active, 'note': m.note})
