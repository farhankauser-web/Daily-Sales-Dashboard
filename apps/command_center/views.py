"""Command Center — page + JSON endpoints."""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import UserDashboardLayout
from .widgets import WIDGET_CATALOG, DEFAULT_LAYOUT, get_widget_data


def _layout_for(user):
    obj, _ = UserDashboardLayout.objects.get_or_create(
        user=user, defaults={'layout': list(DEFAULT_LAYOUT)})
    if not obj.layout:                       # empty → seed default
        obj.layout = list(DEFAULT_LAYOUT)
        obj.save(update_fields=['layout'])
    return obj


@login_required
def command_center(request):
    layout = _layout_for(request.user).layout
    # catalog grouped by category for the "Add widget" drawer
    cats = {}
    for key, spec in WIDGET_CATALOG.items():
        cats.setdefault(spec['category'], []).append({'key': key, **spec})
    return render(request, 'command_center/command_center.html', {
        'layout_json': json.dumps(layout),
        'catalog_json': json.dumps({k: v for k, v in WIDGET_CATALOG.items()}),
        'catalog_by_cat': cats,
    })


@login_required
def api_widget(request):
    """Return one widget's data. ?key=...&config={json}"""
    key = request.GET.get('key', '')
    cfg = {}
    raw = request.GET.get('config')
    if raw:
        try:
            cfg = json.loads(raw)
        except (ValueError, TypeError):
            cfg = {}
    if key not in WIDGET_CATALOG:
        return HttpResponseBadRequest('unknown widget')
    return JsonResponse(get_widget_data(key, request.user, cfg))


@login_required
@require_POST
def api_save_layout(request):
    """Persist the whole layout (Gridstack serialize)."""
    try:
        payload = json.loads(request.body or '{}')
        layout = payload.get('layout', [])
        assert isinstance(layout, list)
    except (ValueError, AssertionError):
        return HttpResponseBadRequest('invalid layout')
    # keep only known keys + whitelisted geometry
    clean = []
    for w in layout:
        if not isinstance(w, dict) or w.get('key') not in WIDGET_CATALOG:
            continue
        clean.append({
            'key': w['key'],
            'x': int(w.get('x', 0)), 'y': int(w.get('y', 0)),
            'w': int(w.get('w', 3)), 'h': int(w.get('h', 2)),
            'config': w.get('config') if isinstance(w.get('config'), dict) else {},
        })
    obj = _layout_for(request.user)
    obj.layout = clean
    obj.save(update_fields=['layout', 'updated_at'])
    return JsonResponse({'status': 'ok', 'count': len(clean)})


@login_required
@require_POST
def api_reset_layout(request):
    obj = _layout_for(request.user)
    obj.layout = list(DEFAULT_LAYOUT)
    obj.save(update_fields=['layout', 'updated_at'])
    return JsonResponse({'status': 'ok', 'layout': obj.layout})
