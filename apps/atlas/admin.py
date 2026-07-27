from django.contrib import admin

from .models import (AtlasCompany, AtlasCustomer, AtlasProduct,
                     CustomerKgRate, NegativeStockLog, PaymentTerm,
                     Quotation, QuotationRevision)


@admin.register(AtlasCompany)
class AtlasCompanyAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'currency', 'vat_rate', 'is_active')


@admin.register(PaymentTerm)
class PaymentTermAdmin(admin.ModelAdmin):
    list_display = ('name', 'days', 'advance_pct', 'sort_order', 'is_active')
    list_editable = ('sort_order', 'is_active')


@admin.register(CustomerKgRate)
class CustomerKgRateAdmin(admin.ModelAdmin):
    list_display = ('customer', 'product', 'order_type', 'kg_rate')
    list_filter = ('order_type', 'customer__company')
    autocomplete_fields = ('customer', 'product')


@admin.register(AtlasCustomer)
class AtlasCustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'kg_rate_local', 'kg_rate_container',
                    'default_payment_term', 'is_active')
    list_filter = ('company',)
    search_fields = ('name', 'email')


@admin.register(AtlasProduct)
class AtlasProductAdmin(admin.ModelAdmin):
    list_display = ('sku', 'company', 'quality', 'length_cm', 'width_cm',
                    'gsm', 'cost', 'stock_qty', 'is_active')
    list_filter = ('company', 'quality')
    search_fields = ('sku', 'description')


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ('reference', 'company', 'customer', 'order_type',
                    'status', 'has_stock_shortage', 'created_at')
    list_filter = ('company', 'status', 'order_type')
    search_fields = ('reference', 'customer__name')
    readonly_fields = [f.name for f in Quotation._meta.fields]


@admin.register(QuotationRevision)
class QuotationRevisionAdmin(admin.ModelAdmin):
    list_display = ('quotation', 'number', 'change_note', 'changed_by',
                    'created_at')
    readonly_fields = [f.name for f in QuotationRevision._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(NegativeStockLog)
class NegativeStockLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'company', 'quotation', 'product',
                    'qty_short')
    list_filter = ('company',)
