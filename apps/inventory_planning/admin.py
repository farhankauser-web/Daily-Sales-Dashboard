from django.contrib import admin

from .models import (DemandInput, InTransitLine, InTransitShipment,
                     PackAssembly, PlanningSku, Warehouse, WarehouseStock)


@admin.register(PackAssembly)
class PackAssemblyAdmin(admin.ModelAdmin):
    list_display = ['assembled_sku', 'component_sku', 'component_per_pack',
                    'active']
    list_filter = ['active', 'component_per_pack']
    search_fields = ['assembled_sku', 'component_sku']
    list_editable = ['component_sku', 'component_per_pack', 'active']


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'region', 'kind', 'is_active']
    list_filter = ['region', 'kind', 'is_active']


@admin.register(PlanningSku)
class PlanningSkuAdmin(admin.ModelAdmin):
    list_display = ['sku', 'region', 'category', 'sku_type', 'product_status',
                    'factory_stock', 'factory_production', 'is_active']
    list_filter = ['region', 'sku_type', 'category', 'is_active']
    search_fields = ['sku', 'name']


@admin.register(WarehouseStock)
class WarehouseStockAdmin(admin.ModelAdmin):
    list_display = ['warehouse', 'sku', 'units', 'as_of', 'source']
    list_filter = ['warehouse', 'source']
    search_fields = ['sku']


@admin.register(DemandInput)
class DemandInputAdmin(admin.ModelAdmin):
    list_display = ['sku', 'region', 'pds', 'effective_from', 'effective_to',
                    'entered_by']
    search_fields = ['sku']


class InTransitLineInline(admin.TabularInline):
    model = InTransitLine
    extra = 0


@admin.register(InTransitShipment)
class InTransitShipmentAdmin(admin.ModelAdmin):
    list_display = ['container_no', 'vendor', 'destination', 'departure_date',
                    'eta_destination', 'status', 'region']
    list_filter = ['status', 'region', 'destination']
    search_fields = ['container_no', 'shipment_id']
    inlines = [InTransitLineInline]
