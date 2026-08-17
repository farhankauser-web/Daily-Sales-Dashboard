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

    rows = []
    for m in CampaignPrefixMap.objects.all():
        g = (m.product_type, m.pack)
        skus = catalog.get(g, [])
        cg = by_group.get(g, {'campaigns': [], 'spend': 0.0})
        # Campaigns are matched to a GROUP, not to one prefix — several
        # prefixes can share a group (e.g. 4BTH and PK4). Attribute a campaign
        # to this row only when its own name actually starts with this prefix;
        # otherwise report it as shared, so counts are never double-claimed.
        mine = [c for c in cg['campaigns']
                if (c['campaign_name'] or '').upper().replace(' ', '')
                .lstrip('-').startswith(m.prefix.upper())]
        rows.append({
            'id': m.pk, 'prefix': m.prefix,
            'product_type': m.product_type, 'pack': m.pack,
            'marketplace': m.marketplace or '',
            'active': m.active, 'note': m.note,
            'updated_at': m.updated_at.isoformat() if m.updated_at else None,
            'sku_count': len(skus),
            'asin_count': len({s['asin'] for s in skus if s['asin']}),
            'campaign_count': len(mine),
            'group_campaign_count': len(cg['campaigns']),
            'spend': round(sum(c['spend'] for c in mine), 2),
            'group_spend': round(cg['spend'], 2),
            'skus': sorted(
                ({'sku': s['sku'], 'asin': s['asin'], 'title': s['title'],
                  'marketplace': s['marketplace'], 'status': s['status']}
                 for s in skus), key=lambda x: (x['sku'] or '')),
            'campaigns': sorted(
                ({'campaign_id': c['campaign_id'],
                  'campaign_name': c['campaign_name'],
                  'marketplace': c['marketplace'],
                  'campaign_type': c['campaign_type'],
                  'spend': round(c['spend'], 2),
                  'allocated': c['allocated']} for c in mine),
                key=lambda x: -x['spend']),
        })
    rows.sort(key=lambda r: (-r['spend'], r['prefix']))

    unallocated = [c for c in camps if not c['allocated']]
    return JsonResponse({
        'marketplace': marketplace, 'days': days,
        'period': {'start': start.isoformat(), 'end': end.isoformat()},
        'kpi': {
            'prefixes': len(rows),
            'active_prefixes': sum(1 for r in rows if r['active']),
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
