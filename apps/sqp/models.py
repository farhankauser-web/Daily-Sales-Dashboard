"""
apps/sqp/models.py — Search Query Performance storage.

Three tables:
  SQPReport   bookkeeping for each report pull (1 row per pull)
  SQPQuery    normalised search-query strings (deduped)
  SQPSnapshot the data — one row per (asin, query, period_start)

All decimal columns use ratios (0.0–1.0) — multiply by 100 in templates for %.
Schema is Postgres-compatible: only standard fields, no SQLite-only quirks.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


PERIOD_CHOICES = [
    ('WEEK',    'Week'),
    ('MONTH',   'Month'),
    ('QUARTER', 'Quarter'),
]

STATUS_CHOICES = [
    ('pending',     'Pending'),
    ('in_progress', 'In Progress'),
    ('done',        'Done'),
    ('failed',      'Failed'),
    ('empty',       'Empty (no rows)'),
]


class SQPReport(models.Model):
    """
    One row per (marketplace, period_type, period_start, asin?) pull.
    `asin` is NULL for brand-level reports and set for ASIN-scoped reports.
    Acts as the de-dupe key so a re-run finds the existing report instead of
    re-requesting from Amazon.
    """
    marketplace   = models.CharField(max_length=8)
    asin          = models.CharField(max_length=16, blank=True, default='',
                                     help_text='Empty = brand-level report')
    period_type   = models.CharField(max_length=10, choices=PERIOD_CHOICES)
    period_start  = models.DateField()
    period_end    = models.DateField()

    sp_report_id  = models.CharField(max_length=64, blank=True,
                                     help_text='SP-API reportId returned by createReport')
    status        = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    rows_loaded   = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)

    requested_at  = models.DateTimeField(auto_now_add=True)
    completed_at  = models.DateTimeField(null=True, blank=True)
    triggered_by  = models.ForeignKey(settings.AUTH_USER_MODEL,
                                      on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        db_table        = 'ix_sqp_reports'
        unique_together = [['marketplace', 'asin', 'period_type', 'period_start']]
        ordering        = ['-period_start', 'marketplace']
        indexes = [
            models.Index(fields=['marketplace', 'period_type', '-period_start']),
            models.Index(fields=['status', '-requested_at']),
        ]

    def __str__(self):
        scope = f'asin={self.asin}' if self.asin else 'brand'
        return f'{self.marketplace.upper()} · {self.period_type} · {self.period_start} · {scope} · {self.status}'


class SQPQuery(models.Model):
    """
    Normalised search-query string. Deduped across all snapshots so we store
    each phrase once instead of repeating it in every (asin, period) row.
    """
    text         = models.CharField(max_length=512, unique=True)
    text_lower   = models.CharField(max_length=512, db_index=True,
                                    help_text='Lowercased for case-insensitive search')
    first_seen   = models.DateField()
    last_seen    = models.DateField()
    total_volume = models.BigIntegerField(default=0,
                                          help_text='Running sum of search_query_volume across all snapshots')
    snapshot_count = models.IntegerField(default=0,
                                         help_text='How many SQPSnapshot rows reference this query')

    class Meta:
        db_table = 'ix_sqp_queries'
        ordering = ['-last_seen', 'text_lower']
        indexes = [
            models.Index(fields=['-total_volume']),
            models.Index(fields=['-last_seen']),
        ]

    def __str__(self):
        return self.text


class SQPSnapshot(models.Model):
    """
    One row per (marketplace, asin, query, period_type, period_start).
    `asin` is empty string for brand-level rows.

    Ratios (click_rate, atc_rate, purchase_rate, *_share) are stored as
    0.0–1.0 decimals — multiply by 100 in the UI for percentages.
    """
    marketplace        = models.CharField(max_length=8)
    asin               = models.CharField(max_length=16, blank=True, default='',
                                          help_text='Empty = brand-level row')
    query              = models.ForeignKey(SQPQuery, on_delete=models.CASCADE,
                                           related_name='snapshots')
    period_type        = models.CharField(max_length=10, choices=PERIOD_CHOICES)
    period_start       = models.DateField()
    period_end         = models.DateField()
    report             = models.ForeignKey(SQPReport, on_delete=models.CASCADE,
                                           related_name='snapshots')

    # Search query
    search_query_score   = models.IntegerField(default=0,
                                               help_text='Rank within the period — 1 = top')
    search_query_volume  = models.BigIntegerField(default=0)

    # Impressions
    impressions_total      = models.BigIntegerField(default=0)
    impressions_asin_count = models.BigIntegerField(default=0)
    impressions_asin_share = models.DecimalField(max_digits=8, decimal_places=6, default=0)

    # Clicks
    clicks_total          = models.BigIntegerField(default=0)
    clicks_asin_count     = models.BigIntegerField(default=0)
    clicks_asin_share     = models.DecimalField(max_digits=8, decimal_places=6, default=0)
    click_rate            = models.DecimalField(max_digits=8, decimal_places=6, default=0)
    clicks_median_price   = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Cart adds
    atc_total          = models.BigIntegerField(default=0)
    atc_asin_count     = models.BigIntegerField(default=0)
    atc_asin_share     = models.DecimalField(max_digits=8, decimal_places=6, default=0)
    atc_rate           = models.DecimalField(max_digits=8, decimal_places=6, default=0)
    atc_median_price   = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Purchases
    purchases_total      = models.BigIntegerField(default=0)
    purchases_asin_count = models.BigIntegerField(default=0)
    purchases_asin_share = models.DecimalField(max_digits=8, decimal_places=6, default=0)
    purchase_rate        = models.DecimalField(max_digits=8, decimal_places=6, default=0)
    purchases_median_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_sqp_snapshots'
        unique_together = [['marketplace', 'asin', 'query', 'period_type', 'period_start']]
        ordering        = ['-period_start', '-search_query_volume']
        indexes = [
            # Overview tab: KPI strip + top queries for a given period
            models.Index(fields=['marketplace', 'period_type', '-period_start']),
            # Drill-down: one ASIN's queries
            models.Index(fields=['marketplace', 'asin', 'period_type', '-period_start']),
            # Compare tab: same query across multiple periods
            models.Index(fields=['query', 'period_type', '-period_start']),
            # Sort by volume within a period
            models.Index(fields=['period_type', 'period_start', '-search_query_volume']),
        ]

    def __str__(self):
        return f'{self.period_start} · {self.asin or "BRAND"} · {self.query.text[:40]}'

    # ── convenience % accessors ────────────────────────────────────────────
    @property
    def ctr_pct(self) -> float:
        return float(self.click_rate) * 100


# ── AI INSIGHTS ───────────────────────────────────────────────────────────────
INSIGHT_TYPES = [
    ('asin_analysis',     'ASIN Analysis'),
    ('keyword_analysis',  'Keyword Opportunity'),
    ('executive_summary', 'Executive Summary'),
    ('ai_chat',           'AI Chat'),
]


class AIInsightCache(models.Model):
    """
    Deterministic cache keyed by a hash of (insight_type + structured context).
    Same inputs ⇒ same hash ⇒ cache hit (no Claude call).
    Any change in the underlying SQP data shifts the hash and forces a refresh.
    """
    hash_key       = models.CharField(max_length=64, unique=True,
                                      help_text='SHA-256 of canonical context JSON')
    insight_type   = models.CharField(max_length=32, choices=INSIGHT_TYPES)
    marketplace    = models.CharField(max_length=8, blank=True)
    asin           = models.CharField(max_length=16, blank=True)
    period_label   = models.CharField(max_length=32, blank=True,
                                      help_text="Human label e.g. 'WoW 2026-W19 vs 2026-W18'")

    model_name     = models.CharField(max_length=64)
    prompt_tokens  = models.IntegerField(default=0)
    response_tokens= models.IntegerField(default=0)
    latency_ms     = models.IntegerField(default=0)

    context_json   = models.JSONField(help_text='Compressed context sent to Claude')
    response_json  = models.JSONField(help_text='Parsed JSON returned by Claude')

    created_at     = models.DateTimeField(auto_now_add=True)
    hit_count      = models.IntegerField(default=0,
                                         help_text='Times this cache row has been served')
    last_hit_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'ix_sqp_ai_cache'
        indexes = [
            models.Index(fields=['insight_type', 'marketplace', 'asin']),
            models.Index(fields=['-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.insight_type} · {self.marketplace} · {self.asin} · {self.period_label}'


class AIInsightHistory(models.Model):
    """
    Append-only audit log of every AI call (cache hit or miss).
    Used for analytics ("which users use AI most"), cost tracking, and
    rebuilding insight history when the cache is purged.
    """
    user           = models.ForeignKey(settings.AUTH_USER_MODEL,
                                       on_delete=models.SET_NULL, null=True, blank=True)
    insight_type   = models.CharField(max_length=32, choices=INSIGHT_TYPES)
    marketplace    = models.CharField(max_length=8, blank=True)
    asin           = models.CharField(max_length=16, blank=True)
    period_label   = models.CharField(max_length=32, blank=True)

    cache          = models.ForeignKey(AIInsightCache, on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='history')
    cache_hit      = models.BooleanField(default=False)

    request_payload  = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    error_message    = models.TextField(blank=True)

    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ix_sqp_ai_history'
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['insight_type', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} · {self.insight_type} · {"hit" if self.cache_hit else "miss"}'

    @property
    def atc_rate_pct(self) -> float:
        return float(self.atc_rate) * 100

    @property
    def cvr_pct(self) -> float:
        return float(self.purchase_rate) * 100

    @property
    def impressions_share_pct(self) -> float:
        return float(self.impressions_asin_share) * 100

    @property
    def clicks_share_pct(self) -> float:
        return float(self.clicks_asin_share) * 100

    @property
    def purchases_share_pct(self) -> float:
        return float(self.purchases_asin_share) * 100
