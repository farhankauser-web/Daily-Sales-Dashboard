"""
Django-admin operations dashboard for the Walmart → MCF pipeline.

Manual actions (all safe, all audited):
  * Retry / Reprocess failed order  → ERROR/CANCELLED/HOLD → NEW
  * Retry tracking upload           → TRACKING_UPLOADED → SHIPPED
  * Run import / submit / status / upload now (buttons via actions)
"""
from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils import timezone

from .models import (AmazonMCFOrder, APILog, AuditEvent, ErrorLog,
                     ShipmentPackage, SkuMapping, WalmartOrder,
                     WalmartOrderItem, WalmartOrderState as S)
from .state import IllegalTransition, transition


class ItemInline(admin.TabularInline):
    model = WalmartOrderItem
    extra = 0
    readonly_fields = ('line_number', 'walmart_sku', 'product_name',
                       'quantity', 'unit_price')
    can_delete = False


class AuditInline(admin.TabularInline):
    model = AuditEvent
    extra = 0
    readonly_fields = ('created_at', 'from_state', 'to_state', 'actor', 'detail')
    can_delete = False
    ordering = ('created_at',)


@admin.register(WalmartOrder)
class WalmartOrderAdmin(admin.ModelAdmin):
    list_display = ('purchase_order_id', 'status', 'order_date',
                    'customer_name', 'shipping_method', 'mcf_id',
                    'error_short', 'imported_at')
    list_filter = ('status', 'shipping_method', 'marketplace')
    search_fields = ('purchase_order_id', 'customer_order_id', 'customer_name',
                     'items__walmart_sku', 'mcf__fulfillment_order_id',
                     'mcf__packages__tracking_number')
    readonly_fields = ('purchase_order_id', 'customer_order_id', 'order_date',
                       'raw_order', 'imported_at', 'updated_at',
                       'acknowledged_at')
    inlines = [ItemInline, AuditInline]
    actions = ['action_reprocess', 'action_retry_tracking']
    date_hierarchy = 'order_date'

    @admin.display(description='MCF order')
    def mcf_id(self, obj):
        return getattr(getattr(obj, 'mcf', None), 'fulfillment_order_id', '—')

    @admin.display(description='Error')
    def error_short(self, obj):
        return (obj.error_reason or '')[:60]

    @admin.action(description='↻ Reprocess (ERROR/HOLD/CANCELLED → NEW)')
    def action_reprocess(self, request, queryset):
        done = skipped = 0
        for order in queryset:
            try:
                if order.status in (S.ERROR, S.HOLD, S.CANCELLED) and \
                        transition(order, S.NEW,
                                   f'admin:{request.user.username}',
                                   {'action': 'reprocess'},
                                   error_reason=''):
                    done += 1
                else:
                    skipped += 1
            except IllegalTransition:
                skipped += 1
        messages.success(request, f'{done} order(s) queued for reprocessing; '
                                  f'{skipped} skipped (wrong state).')

    @admin.action(description='↻ Retry tracking upload (→ SHIPPED)')
    def action_retry_tracking(self, request, queryset):
        done = 0
        for order in queryset.filter(status=S.TRACKING_UPLOADED):
            if transition(order, S.SHIPPED, f'admin:{request.user.username}',
                          {'action': 'retry tracking upload'}):
                done += 1
        messages.success(request, f'{done} order(s) will re-run tracking '
                                  f'upload on the next cycle.')

    def changelist_view(self, request, extra_context=None):
        today = timezone.now().date()
        agg = WalmartOrder.objects.aggregate(
            imported_today=Count('pk', filter=Q(imported_at__date=today)),
            submitted=Count('pk', filter=Q(status__in=[
                S.MCF_CREATED, S.SHIPPED, S.TRACKING_UPLOADED, S.COMPLETED])),
            pending=Count('pk', filter=Q(status__in=[
                S.NEW, S.VALIDATED, S.PROCESSING, S.HOLD])),
            shipped=Count('pk', filter=Q(status__in=[
                S.SHIPPED, S.TRACKING_UPLOADED, S.COMPLETED])),
            tracking_uploaded=Count('pk', filter=Q(status__in=[
                S.TRACKING_UPLOADED, S.COMPLETED])),
            errors=Count('pk', filter=Q(status=S.ERROR)),
        )
        extra_context = extra_context or {}
        extra_context['title'] = (
            f"Walmart orders — today: {agg['imported_today']} imported · "
            f"{agg['submitted']} submitted · {agg['pending']} pending · "
            f"{agg['shipped']} shipped · {agg['tracking_uploaded']} tracking "
            f"uploaded · ⚠ {agg['errors']} errors")
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(SkuMapping)
class SkuMappingAdmin(admin.ModelAdmin):
    list_display = ('walmart_sku', 'amazon_sku', 'enabled', 'notes',
                    'updated_by', 'updated_at')
    list_editable = ('enabled',)
    list_filter = ('enabled',)
    search_fields = ('walmart_sku', 'amazon_sku')
    actions = ['enable_selected', 'disable_selected']

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        obj.walmart_sku = obj.walmart_sku.strip()
        obj.amazon_sku = obj.amazon_sku.strip()
        super().save_model(request, obj, form, change)

    @admin.action(description='Enable selected')
    def enable_selected(self, request, queryset):
        queryset.update(enabled=True)

    @admin.action(description='Disable selected')
    def disable_selected(self, request, queryset):
        queryset.update(enabled=False)


@admin.register(AmazonMCFOrder)
class AmazonMCFOrderAdmin(admin.ModelAdmin):
    list_display = ('fulfillment_order_id', 'order', 'amazon_status',
                    'shipping_speed', 'submitted_at', 'last_status_check')
    list_filter = ('amazon_status', 'shipping_speed')
    search_fields = ('fulfillment_order_id', 'order__purchase_order_id')
    readonly_fields = [f.name for f in AmazonMCFOrder._meta.fields]


@admin.register(ShipmentPackage)
class ShipmentPackageAdmin(admin.ModelAdmin):
    list_display = ('tracking_number', 'carrier_code', 'carrier_walmart',
                    'mcf_order', 'ship_date', 'uploaded_to_walmart_at',
                    'upload_error_short')
    list_filter = ('carrier_code',)
    search_fields = ('tracking_number', 'mcf_order__fulfillment_order_id',
                     'mcf_order__order__purchase_order_id')

    @admin.display(description='Upload error')
    def upload_error_short(self, obj):
        return (obj.upload_error or '')[:60]


@admin.register(APILog)
class APILogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'direction', 'method', 'endpoint',
                    'status_code', 'duration_ms')
    list_filter = ('direction', 'status_code', 'method')
    search_fields = ('endpoint', 'correlation_id', 'request_body',
                     'response_body')
    readonly_fields = [f.name for f in APILog._meta.fields]
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False


@admin.register(ErrorLog)
class ErrorLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'exception', 'endpoint', 'order',
                    'retry_count', 'resolved')
    list_filter = ('resolved',)
    search_fields = ('exception', 'endpoint',
                     'order__purchase_order_id')
    actions = ['mark_resolved']

    @admin.action(description='Mark resolved')
    def mark_resolved(self, request, queryset):
        queryset.update(resolved=True)
