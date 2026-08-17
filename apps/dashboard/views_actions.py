"""
apps/dashboard/views_actions.py — P4 action queue endpoints.

Every mutating endpoint here is POST-only, authenticated, permission-gated and
server-validated. Nothing the client sends is trusted: the current value, the
entity's marketplace ownership, the action's state and the write capability are
all re-derived server-side on every call.

No endpoint in this module can be reached by a scheduler or an AI agent — each
one requires an authenticated user, and `execute` additionally requires an
action a human has already approved.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.core.decorators import permission_required

from . import ad_actions as ACT
from .models import AdActionRequest
from .views import _allowed_marketplaces


def _body(request):
    try:
        return json.loads(request.body or '{}')
    except ValueError:
        return {}


def _get_action(request, pk):
    """Fetch an action and prove the caller may act on its marketplace."""
    a = AdActionRequest.objects.filter(pk=pk).first()
    if a is None:
        return None, JsonResponse({'status': 'failed',
                                   'message': 'Action not found.'}, status=404)
    if not request.user.can_access_marketplace(a.marketplace):
        return None, JsonResponse({'status': 'failed',
                                   'message': 'forbidden'}, status=403)
    return a, None


# ── Page ────────────────────────────────────────────────────────────────────
@login_required
@permission_required('can_view_dashboard')
def action_queue(request):
    """Supporting management surface. Context still lives on the SKU/campaign
    pages — this is the queue view of the same actions."""
    marketplace = request.GET.get('mp', 'usa')
    if not request.user.can_access_marketplace(marketplace):
        marketplace = _allowed_marketplaces(request.user)[0]
    return render(request, 'dashboard/action_queue.html', {
        'marketplace': marketplace,
        'allowed_marketplaces': _allowed_marketplaces(request.user),
        'capability': ACT.write_capability(marketplace),
    })


# ── Read ────────────────────────────────────────────────────────────────────
@login_required
@permission_required('can_view_dashboard')
def api_actions_list(request):
    marketplace = request.GET.get('mp', 'usa')
    if not request.user.can_access_marketplace(marketplace):
        return JsonResponse({'error': 'forbidden'}, status=403)
    qs = AdActionRequest.objects.filter(marketplace=marketplace)
    statuses = [s.strip() for s in (request.GET.get('status') or '').split(',')
                if s.strip()]
    if statuses:
        qs = qs.filter(status__in=statuses)
    entity_id = (request.GET.get('entity_id') or '').strip()
    if entity_id:
        qs = qs.filter(entity_id=entity_id)
    rows = [ACT.serialize(a) for a in qs.select_related(
        'proposed_by', 'approved_by')[:200]]
    counts = {}
    for a in AdActionRequest.objects.filter(marketplace=marketplace).values_list(
            'status', flat=True):
        counts[a] = counts.get(a, 0) + 1
    return JsonResponse({
        'marketplace': marketplace,
        'capability': ACT.write_capability(marketplace),
        'counts': counts, 'rows': rows,
    })


@login_required
@permission_required('can_view_dashboard')
def api_action_detail(request, pk: int):
    """The review screen's data — includes a LIVE staleness check so the
    reviewer sees the current state, not the state at proposal time."""
    a, err = _get_action(request, pk)
    if err:
        return err
    stale, why = ACT.staleness(a)
    d = ACT.serialize(a)
    d['stale'] = stale
    d['stale_reason'] = why
    d['capability'] = ACT.write_capability(a.marketplace)
    return JsonResponse(d)


# ── Write (each step explicit and human-driven) ─────────────────────────────
@login_required
@permission_required('can_manage_cogs')
@require_POST
def api_action_propose(request):
    """Create a recommendation from an opportunity. Creates a QUEUE ENTRY —
    never an execution."""
    d = _body(request)
    marketplace = (d.get('mp') or 'usa').lower()
    if not request.user.can_access_marketplace(marketplace):
        return JsonResponse({'status': 'failed', 'message': 'forbidden'},
                            status=403)
    if (d.get('action_type') or 'campaign_budget') != 'campaign_budget':
        return JsonResponse(
            {'status': 'failed',
             'message': 'Only campaign budget actions are supported. Target '
                        'bids are not offered because Pulse stores no current '
                        'bid value to change from.'}, status=400)
    try:
        period = None
        p = d.get('period') or {}
        if p.get('start') and p.get('end'):
            from datetime import date as _d
            period = (_d.fromisoformat(p['start']), _d.fromisoformat(p['end']))
        action, created = ACT.propose_campaign_budget(
            marketplace=marketplace,
            campaign_id=d.get('campaign_id'),
            proposed_value=d.get('proposed_value'),
            user=request.user,
            opportunity=d.get('opportunity') or {},
            from_sku=d.get('from_sku') or '',
            period=period)
    except ACT.ActionError as exc:
        return JsonResponse({'status': 'failed', 'message': str(exc)}, status=400)
    return JsonResponse({'status': 'ok', 'created': created,
                         'action': ACT.serialize(action)})


@login_required
@permission_required('can_manage_cogs')
@require_POST
def api_action_approve(request, pk: int):
    a, err = _get_action(request, pk)
    if err:
        return err
    try:
        ACT.approve(a, request.user)
    except ACT.ActionError as exc:
        return JsonResponse({'status': 'failed', 'message': str(exc),
                             'action': ACT.serialize(a)}, status=409)
    return JsonResponse({'status': 'ok', 'action': ACT.serialize(a)})


@login_required
@permission_required('can_manage_cogs')
@require_POST
def api_action_reject(request, pk: int):
    a, err = _get_action(request, pk)
    if err:
        return err
    try:
        ACT.reject(a, request.user, note=_body(request).get('note', ''))
    except ACT.ActionError as exc:
        return JsonResponse({'status': 'failed', 'message': str(exc)}, status=409)
    return JsonResponse({'status': 'ok', 'action': ACT.serialize(a)})


@login_required
@permission_required('can_manage_cogs')
@require_POST
def api_action_execute(request, pk: int):
    """Execute an APPROVED action.

    `dry_run` defaults to TRUE: a caller must ask explicitly for a live attempt,
    and even then the capability gate decides whether anything can happen.
    """
    a, err = _get_action(request, pk)
    if err:
        return err
    dry_run = bool(_body(request).get('dry_run', True))
    try:
        action, message = ACT.execute(a, request.user, dry_run=dry_run)
    except ACT.ActionError as exc:
        return JsonResponse({'status': 'failed', 'message': str(exc),
                             'action': ACT.serialize(a)}, status=409)
    ok = action.status in ('executed', 'approved')
    return JsonResponse({'status': 'ok' if ok else 'blocked',
                         'message': message, 'action': ACT.serialize(action)})
