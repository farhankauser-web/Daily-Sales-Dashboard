"""apps/dashboard/admin.py"""
from django.contrib import admin
from .models import (
    Product, COGSEntry, MonthlyTarget, DailyMetric,
    InventorySnapshot, PPCCampaignSnapshot, Alert
)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display  = ['asin', 'marketplace', 'title', 'status', 'list_price', 'fba_fee']
    list_filter   = ['marketplace', 'status']
    search_fields = ['asin', 'sku', 'title']

@admin.register(COGSEntry)
class COGSEntryAdmin(admin.ModelAdmin):
    list_display  = ['product', 'month', 'unit_cost', 'shipping_cost', 'duties_cost']
    list_filter   = ['month', 'product__marketplace']
    search_fields = ['product__asin']

@admin.register(MonthlyTarget)
class MonthlyTargetAdmin(admin.ModelAdmin):
    list_display  = ['marketplace', 'month', 'revenue_target', 'tacos_target', 'ppc_budget']
    list_filter   = ['marketplace', 'month']

@admin.register(DailyMetric)
class DailyMetricAdmin(admin.ModelAdmin):
    list_display  = ['date', 'marketplace', 'revenue', 'units', 'ppc_spend', 'tacos']
    list_filter   = ['marketplace']
    ordering      = ['-date']

@admin.register(InventorySnapshot)
class InventorySnapshotAdmin(admin.ModelAdmin):
    list_display  = ['product', 'date', 'afn_fulfillable', 'days_cover', 'stock_alert']
    list_filter   = ['product__marketplace']
    ordering      = ['-date']
    search_fields = ['product__asin']

@admin.register(PPCCampaignSnapshot)
class PPCCampaignSnapshotAdmin(admin.ModelAdmin):
    list_display  = ['campaign_name', 'marketplace', 'date', 'spend', 'acos', 'state']
    list_filter   = ['marketplace', 'campaign_type', 'state']
    search_fields = ['campaign_name', 'campaign_id']
    ordering      = ['-date', '-spend']

@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display  = ['created_at', 'severity', 'category', 'marketplace', 'title', 'is_resolved']
    list_filter   = ['severity', 'category', 'is_resolved']
    ordering      = ['-created_at']
    readonly_fields = ['created_at', 'resolved_at', 'resolved_by']
