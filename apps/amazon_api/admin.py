"""
apps/amazon_api/admin.py
"""
from django.contrib import admin
from .models import AmazonAPIConfig, AnthropicConfig, APISyncLog


@admin.register(AmazonAPIConfig)
class AmazonAPIConfigAdmin(admin.ModelAdmin):
    list_display  = ['marketplace', 'label', 'is_active', 'last_test_status', 'last_tested_at']
    list_filter   = ['marketplace', 'is_active', 'last_test_status']
    readonly_fields = ['created_by', 'updated_by', 'last_tested_at', 'last_test_status',
                       'last_test_detail', 'created_at', 'updated_at']

    def get_fields(self, request, obj=None):
        # Mask sensitive fields in admin
        fields = super().get_fields(request, obj)
        return fields


@admin.register(AnthropicConfig)
class AnthropicConfigAdmin(admin.ModelAdmin):
    list_display = ['label', 'model', 'is_active', 'updated_at']


@admin.register(APISyncLog)
class APISyncLogAdmin(admin.ModelAdmin):
    list_display  = ['created_at', 'config', 'data_type', 'status', 'records', 'duration_ms']
    list_filter   = ['status', 'data_type']
    ordering      = ['-created_at']
