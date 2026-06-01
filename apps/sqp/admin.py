from django.contrib import admin

from .models import (
    AIInsightCache, AIInsightHistory,
    SQPQuery, SQPReport, SQPSnapshot,
)


@admin.register(SQPReport)
class SQPReportAdmin(admin.ModelAdmin):
    list_display  = ('marketplace', 'period_type', 'period_start', 'period_end',
                     'asin', 'status', 'rows_loaded', 'requested_at')
    list_filter   = ('marketplace', 'period_type', 'status')
    search_fields = ('asin', 'sp_report_id')
    date_hierarchy = 'period_start'


@admin.register(SQPQuery)
class SQPQueryAdmin(admin.ModelAdmin):
    list_display  = ('text', 'total_volume', 'snapshot_count', 'first_seen', 'last_seen')
    search_fields = ('text', 'text_lower')
    ordering      = ('-total_volume',)


@admin.register(SQPSnapshot)
class SQPSnapshotAdmin(admin.ModelAdmin):
    list_display  = ('period_start', 'marketplace', 'asin', 'query',
                     'search_query_volume', 'clicks_total', 'purchases_total')
    list_filter   = ('marketplace', 'period_type')
    search_fields = ('asin', 'query__text')
    date_hierarchy = 'period_start'
    raw_id_fields = ('query', 'report')


@admin.register(AIInsightCache)
class AIInsightCacheAdmin(admin.ModelAdmin):
    list_display   = ('created_at', 'insight_type', 'marketplace', 'asin',
                      'period_label', 'model_name', 'prompt_tokens',
                      'response_tokens', 'hit_count')
    list_filter    = ('insight_type', 'marketplace', 'model_name')
    search_fields  = ('asin', 'hash_key', 'period_label')
    readonly_fields = ('hash_key', 'created_at', 'last_hit_at',
                       'prompt_tokens', 'response_tokens', 'latency_ms',
                       'context_json', 'response_json')


@admin.register(AIInsightHistory)
class AIInsightHistoryAdmin(admin.ModelAdmin):
    list_display   = ('created_at', 'user', 'insight_type', 'marketplace',
                      'asin', 'period_label', 'cache_hit')
    list_filter    = ('insight_type', 'cache_hit', 'marketplace')
    search_fields  = ('asin', 'period_label', 'user__username')
    date_hierarchy = 'created_at'
    raw_id_fields  = ('cache',)
    readonly_fields = ('created_at', 'request_payload', 'response_payload')
