"""
apps/dashboard/views_inventory.py — Inventory + PPC Analytics + Alerts views
Imported into urls.py alongside views.py
"""
import json
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.conf import settings
from django.db.models import Sum, Avg, F, Q, OuterRef, Subquery
from django.utils import timezone

from apps.core.decorators import permission_required
from apps.users.models import AuditLog
from .models import (
    Product, InventorySnapshot, PPCCampaignSnapshot,
    Alert, MonthlyTarget, DailyMetric
)

logger = logging.getLogger(__name__)


# ── INVENTORY DASHBOARD ───────────────────────────────────────────────────────
@login_required
@permission_required('can_view_inventory')
def inventory(request):
    mp = request.GET.get('mp', 'usa')
    if not request.user.can_access_marketplace(mp):
        mp = (request.user.allowed_marketplaces or ['usa'])[0]

    today    = date.today()
    allowed  = request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys())

    base_snapshots = (
        InventorySnapshot.objects
        .filter(product__marketplace=mp)
        .select_related('product')
    )
    latest_snapshots = base_snapshots.filter(date=today).order_by('product__asin')

    # If no today data, use most recent available
    if not latest_snapshots.exists():
        # Cross-database latest row per product (SQLite-safe; no DISTINCT ON).
        latest_date_subquery = (
            InventorySnapshot.objects
            .filter(product=OuterRef('product'))
            .order_by('-date')
            .values('date')[:1]
        )
        latest_snapshots = (
            base_snapshots
            .annotate(_latest_date=Subquery(latest_date_subquery))
            .filter(date=F('_latest_date'))
            .order_by('product__asin')
        )

    # Aggregate alert counts
    alert_counts = {
        'stockout':  sum(1 for s in latest_snapshots if s.stock_alert == 'stockout'),
        'critical':  sum(1 for s in latest_snapshots if s.stock_alert == 'critical'),
        'low':       sum(1 for s in latest_snapshots if s.stock_alert == 'low'),
        'ok':        sum(1 for s in latest_snapshots if s.stock_alert == 'ok'),
    }

    # Reorder candidates: days_cover < 30 (within lead time + safety)
    reorder_needed = [s for s in latest_snapshots if float(s.days_cover) < 30]

    # Total inbound
    inbound_total = sum(s.afn_inbound_working + s.afn_inbound_shipped + s.afn_inbound_receiving
                        for s in latest_snapshots)

    ctx = {
        'snapshots':        latest_snapshots,
        'alert_counts':     alert_counts,
        'reorder_needed':   reorder_needed,
        'inbound_total':    inbound_total,
        'marketplace':      mp,
        'allowed_marketplaces': allowed,
        'today':            today,
    }
    return render(request, 'dashboard/inventory.html', ctx)


@login_required
@permission_required('can_view_inventory')
def inventory_history(request, pk):
    """30-day inventory trend for a single product."""
    product = get_object_or_404(Product, pk=pk)
    if not request.user.can_access_marketplace(product.marketplace):
        return JsonResponse({'error': 'Access denied'}, status=403)

    snapshots = InventorySnapshot.objects.filter(
        product=product,
        date__gte=date.today() - timedelta(days=30)
    ).order_by('date')

    data = {
        'dates':       [str(s.date) for s in snapshots],
        'fulfillable': [s.afn_fulfillable for s in snapshots],
        'inbound':     [s.afn_inbound_shipped + s.afn_inbound_working for s in snapshots],
        'days_cover':  [float(s.days_cover) for s in snapshots],
    }
    return JsonResponse({'product': str(product), 'data': data})


# ── INVENTORY MANUAL UPDATE ───────────────────────────────────────────────────
@login_required
@permission_required('can_view_inventory')
def inventory_update(request):
    """Manual warehouse stock update via AJAX POST."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data         = json.loads(request.body)
    product_pk   = data.get('product_pk')
    warehouse_qty= data.get('warehouse_stock', 0)

    product = get_object_or_404(Product, pk=product_pk)
    if not request.user.can_access_marketplace(product.marketplace):
        return JsonResponse({'error': 'Access denied'}, status=403)

    snap, _ = InventorySnapshot.objects.get_or_create(
        product=product, date=date.today(),
        defaults={'afn_fulfillable': 0}
    )
    snap.warehouse_stock = int(warehouse_qty)
    snap.save(update_fields=['warehouse_stock'])

    return JsonResponse({'ok': True, 'warehouse_stock': snap.warehouse_stock})


# ── PPC ANALYTICS ─────────────────────────────────────────────────────────────
@login_required
@permission_required('can_view_ppc')
def ppc_analytics(request):
    mp     = request.GET.get('mp', 'usa')
    period = request.GET.get('period', '30d')

    if not request.user.can_access_marketplace(mp):
        mp = (request.user.allowed_marketplaces or ['usa'])[0]

    today = date.today()
    days  = {'7d': 7, '30d': 30, '90d': 90}.get(period, 30)
    start = today - timedelta(days=days)

    campaigns = (
        PPCCampaignSnapshot.objects
        .filter(marketplace=mp, date__gte=start, date__lte=today, state='enabled')
        .values('campaign_id', 'campaign_name', 'campaign_type')
        .annotate(
            total_spend    = Sum('spend'),
            total_sales    = Sum('sales_7d'),
            total_clicks   = Sum('clicks'),
            total_impr     = Sum('impressions'),
            total_orders   = Sum('orders_7d'),
            avg_acos       = Avg('acos'),
            avg_roas       = Avg('roas'),
            avg_ctr        = Avg('ctr'),
            avg_cvr        = Avg('cvr'),
            avg_cpc        = Avg('cpc'),
        )
        .order_by('-total_spend')
    )

    # Portfolio summary
    totals = campaigns.aggregate(
        grand_spend  = Sum('total_spend'),
        grand_sales  = Sum('total_sales'),
        grand_clicks = Sum('total_clicks'),
        grand_impr   = Sum('total_impr'),
        grand_orders = Sum('total_orders'),
    )

    # Type breakdown (SP / SB / SD)
    type_breakdown = (
        PPCCampaignSnapshot.objects
        .filter(marketplace=mp, date__gte=start, date__lte=today)
        .values('campaign_type')
        .annotate(spend=Sum('spend'), sales=Sum('sales_7d'))
        .order_by('-spend')
    )

    # Daily PPC trend
    daily_ppc = (
        PPCCampaignSnapshot.objects
        .filter(marketplace=mp, date__gte=start, date__lte=today)
        .values('date')
        .annotate(spend=Sum('spend'), sales=Sum('sales_7d'), acos=Avg('acos'))
        .order_by('date')
    )

    chart_data = json.dumps({
        'dates': [str(d['date']) for d in daily_ppc],
        'spend': [float(d['spend']) for d in daily_ppc],
        'sales': [float(d['sales']) for d in daily_ppc],
        'acos':  [float(d['acos']) * 100 for d in daily_ppc],
    })

    # Get target TACoS
    target = MonthlyTarget.objects.filter(
        marketplace=mp, month=today.replace(day=1)
    ).first()

    ctx = {
        'campaigns':       campaigns,
        'totals':          totals,
        'type_breakdown':  type_breakdown,
        'chart_data':      chart_data,
        'marketplace':     mp,
        'period':          period,
        'start':           start,
        'today':           today,
        'target':          target,
        'allowed_marketplaces': request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys()),
    }
    return render(request, 'dashboard/ppc.html', ctx)


# ── ALERTS ────────────────────────────────────────────────────────────────────
@login_required
def alerts(request):
    """All unresolved alerts for this user's allowed marketplaces."""
    allowed = request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys())

    qs = Alert.objects.filter(
        Q(marketplace__in=allowed) | Q(marketplace=''),
        is_resolved=False
    ).order_by('-created_at')

    ctx = {
        'alerts':    qs,
        'critical':  qs.filter(severity='critical').count(),
        'warnings':  qs.filter(severity='warning').count(),
    }
    return render(request, 'dashboard/alerts.html', ctx)


@login_required
def alert_resolve(request, pk):
    alert = get_object_or_404(Alert, pk=pk)
    if request.method == 'POST':
        alert.is_resolved  = True
        alert.resolved_by  = request.user
        alert.resolved_at  = timezone.now()
        alert.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at'])
        return JsonResponse({'ok': True})
    return JsonResponse({'error': 'POST required'}, status=405)


@login_required
def alerts_api(request):
    """Returns unread alert count for sidebar badge."""
    allowed = request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys())
    count = Alert.objects.filter(
        Q(marketplace__in=allowed) | Q(marketplace=''),
        is_resolved=False, is_read=False
    ).count()
    return JsonResponse({'count': count})


# ── ALERT GENERATION COMMAND HELPER ───────────────────────────────────────────
def generate_alerts_for_marketplace(mp: str):
    """
    Called after each sync. Checks thresholds and creates alerts.
    Should be called from sync_amazon_data command.
    """
    today = date.today()

    # 1. Inventory alerts
    snapshots = InventorySnapshot.objects.filter(
        product__marketplace=mp, date=today
    ).select_related('product')

    for snap in snapshots:
        dc = float(snap.days_cover)
        if dc < 30 and snap.afn_fulfillable > 0:
            Alert.create_inventory_alert(snap.product, dc, snap.afn_fulfillable)
        elif snap.afn_fulfillable <= 0:
            Alert.create_inventory_alert(snap.product, 0, 0)

    # 2. TACoS alerts
    target = MonthlyTarget.objects.filter(marketplace=mp, month=today.replace(day=1)).first()
    if target:
        recent = DailyMetric.objects.filter(marketplace=mp, date=today).first()
        if recent and float(recent.tacos) * 100 > float(target.tacos_target) * 1.2:
            Alert.create_tacos_alert(mp, float(recent.tacos) * 100, float(target.tacos_target))
