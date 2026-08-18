"""
apps/dashboard/views_cogs.py — COGS entry browser: filter, sort, edit, delete.

The COGS page previously rendered a fixed "last 100 entries" list with no way
to find, correct or remove a row. These endpoints back a filterable table
(region · month · product group · SKU) with inline editing and hard delete.

SCOPE: this only manages COGSEntry rows. Upload, the FBA-rate upload and the
recalculation pipeline are untouched — a COGS change takes effect the same way
it always has, when the month is recalculated.
"""
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from apps.core.decorators import permission_required

from .models import COGSEntry
from .views import _allowed_marketplaces

_COST_FIELDS = ['unit_cost', 'shipping_cost', 'duties_cost', 'prep_cost',
                'other_cost']


def _serialize(e):
    p = e.product
    total = sum(float(getattr(e, f) or 0) for f in _COST_FIELDS)
    return {
        'id': e.pk,
        'sku': p.sku, 'asin': p.asin,
        'title': (p.title or '')[:80],
        'category': p.category or '',
        'brand': p.brand or '',
        'marketplace': p.marketplace,
        'month': e.month.isoformat() if e.month else None,
        'month_label': e.month.strftime('%b %Y') if e.month else '',
        'unit_cost': float(e.unit_cost or 0),
        'shipping_cost': float(e.shipping_cost or 0),
        'duties_cost': float(e.duties_cost or 0),
        'prep_cost': float(e.prep_cost or 0),
        'other_cost': float(e.other_cost or 0),
        'total_cogs': round(total, 4),
        'notes': e.notes or '',
        'updated_at': e.updated_at.isoformat() if e.updated_at else None,
        'uploaded_by': getattr(e.uploaded_by, 'email', '') or '',
    }


@login_required
@permission_required('can_manage_cogs')
def api_cogs_entries(request):
    """Filterable COGS entries + the option lists the filter bar needs.

    Filters: mp (marketplace), month (YYYY-MM-DD), category, q (SKU/ASIN/title).
    Sorting is done client-side on the returned page, same as the other tables.
    """
    allowed = _allowed_marketplaces(request.user)
    qs = (COGSEntry.objects
          .select_related('product', 'uploaded_by')
          .filter(product__marketplace__in=allowed))

    mp = (request.GET.get('mp') or '').strip()
    if mp:
        if not request.user.can_access_marketplace(mp):
            return JsonResponse({'error': 'forbidden'}, status=403)
        qs = qs.filter(product__marketplace=mp)

    month = (request.GET.get('month') or '').strip()
    if month:
        qs = qs.filter(month=month)

    category = (request.GET.get('category') or '').strip()
    if category:
        qs = qs.filter(product__category=category)

    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(product__sku__icontains=q) |
                       Q(product__asin__icontains=q) |
                       Q(product__title__icontains=q))

    try:
        limit = max(1, min(int(request.GET.get('limit') or 500), 2000))
    except ValueError:
        limit = 500

    total = qs.count()
    rows = [_serialize(e) for e in
            qs.order_by('-month', 'product__sku')[:limit]]

    # Option lists come from the whole permitted set, not the filtered slice,
    # so choosing one filter never empties the others.
    base = COGSEntry.objects.filter(product__marketplace__in=allowed)
    months = sorted({m for m in base.values_list('month', flat=True) if m},
                    reverse=True)
    return JsonResponse({
        'rows': rows,
        'total': total,
        'shown': len(rows),
        'truncated': total > len(rows),
        'filters': {'mp': mp, 'month': month, 'category': category, 'q': q},
        'options': {
            'marketplaces': allowed,
            'months': [{'value': m.isoformat(),
                        'label': m.strftime('%b %Y')} for m in months],
            'categories': sorted({c for c in base.values_list(
                'product__category', flat=True) if c}),
        },
    })


@login_required
@permission_required('can_manage_cogs')
@require_POST
def api_cogs_entry_save(request, pk: int):
    """Edit the cost components of one entry.

    The product and month are the entry's identity (unique together) and are
    not editable here — changing either would silently move a cost onto a
    different SKU or period. Delete and re-add instead.
    """
    e = (COGSEntry.objects.select_related('product')
         .filter(pk=pk).first())
    if e is None:
        return JsonResponse({'status': 'failed', 'message': 'Not found.'},
                            status=404)
    if not request.user.can_access_marketplace(e.product.marketplace):
        return JsonResponse({'status': 'failed', 'message': 'forbidden'},
                            status=403)
    try:
        d = json.loads(request.body or '{}')
    except ValueError:
        d = {}

    changed = []
    for f in _COST_FIELDS:
        if f not in d:
            continue
        try:
            v = round(float(d[f]), 4)
        except (TypeError, ValueError):
            return JsonResponse({'status': 'failed',
                                 'message': f'{f} is not a number.'}, status=400)
        if v < 0:
            return JsonResponse({'status': 'failed',
                                 'message': f'{f} cannot be negative.'},
                                status=400)
        setattr(e, f, v)
        changed.append(f)
    if 'notes' in d:
        e.notes = (d.get('notes') or '')[:2000]
        changed.append('notes')
    if not changed:
        return JsonResponse({'status': 'failed', 'message': 'Nothing to save.'},
                            status=400)
    e.save(update_fields=changed + ['updated_at'])
    return JsonResponse({'status': 'ok', 'row': _serialize(e)})


@login_required
@permission_required('can_manage_cogs')
@require_POST
def api_cogs_entry_delete(request, pk: int):
    """Hard-delete one COGS entry.

    Deliberately permanent, per the page's purpose — but it means the SKU has
    NO cost for that month until one is re-added, and any later recalculation
    of that month will reflect that. The UI warns before calling this.
    """
    e = (COGSEntry.objects.select_related('product')
         .filter(pk=pk).first())
    if e is None:
        return JsonResponse({'status': 'failed', 'message': 'Not found.'},
                            status=404)
    if not request.user.can_access_marketplace(e.product.marketplace):
        return JsonResponse({'status': 'failed', 'message': 'forbidden'},
                            status=403)
    label = f'{e.product.sku} · {e.month:%b %Y}' if e.month else e.product.sku
    e.delete()
    return JsonResponse({'status': 'ok', 'deleted': label})
