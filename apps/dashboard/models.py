"""
P0.2 — FBA Fee Intelligence Phase 2 Models
Stores component-level fee data from Amazon Data Kiosk (economics query).

Schema verified on:
  - Marketplace support matrix (section B)
  - Component taxonomy (section B)
  - Data hazards (section K)
"""
from django.db import models


class FbaFeeComponent(models.Model):
    """
    Per-day per-MSKU per-fee-component breakdown from Data Kiosk economics query.

    One row per (marketplace, date, msku, component_name).
    Aggregated at CHARGE level (not COMPONENT level) to match the business rule
    that package handling costs are the core investigation target.

    Data Hazards (Section K):
    - K1: Amazon-generated MSKUs (amzn.*) are excluded (grade-and-resell units)
    - K2: Zero-quantity and negative-amount rows are stored but marked as
           'adjustment' to exclude them from rate-per-unit derivation
    - K3: Empty-fee rows (no fees charged) are skipped entirely

    Architecture (Section H):
    - Component storage keys on observed `name` string, not an enum
    - quantity is Float and nullable
    - currency is per-row (Amount.currencyCode)
    """
    # ── identity ────────────────────────────────────────────────────────────
    marketplace     = models.CharField(max_length=8, db_index=True,
        help_text='Marketplace code (usa, uk, de, ae, sa)')
    msku            = models.CharField(max_length=128, db_index=True,
        help_text='Merchant SKU (seller-provided, or amzn.* for returns)')
    date            = models.DateField(db_index=True,
        help_text='Transaction date (day-level aggregation)')

    # ── fee component details ───────────────────────────────────────────────
    fee_type        = models.CharField(max_length=32,
        help_text='FEE_TYPE from query: FBA_FULFILLMENT_FEE or FBA_STORAGE_FEE')
    component_name  = models.CharField(max_length=48, db_index=True,
        help_text='FeeComponent.name (free text): BaseFbaFulfilmentFee, '
                  'FuelSurcharge, LowInventoryLevelFee, etc.')

    # ── aggregated detail (rate-card basis per Section J) ──────────────────
    # amount = gross rate-card amount (BEFORE promotion, tax)
    # amountPerUnit = final charge after promotion + tax (NEVER use for size tier)
    quantity        = models.FloatField(default=0.0,
        help_text='Billed units for this component. May be 0.0 or null on '
                  'adjustment rows (reclassification between components).')
    amount          = models.DecimalField(max_digits=14, decimal_places=4,
                                         default=0,
        help_text='Gross amount per rate card (excludes promotion, tax).')
    amount_per_unit = models.DecimalField(max_digits=10, decimal_places=4,
                                         default=0,
        help_text='Final per-unit charge AFTER promotion and tax. Never use '
                  'for packaging analysis per Section J.')
    promotion_amount = models.DecimalField(max_digits=14, decimal_places=4,
                                          default=0,
        help_text='Promotion discount applied (usually 0).')
    tax_amount      = models.DecimalField(max_digits=14, decimal_places=4,
                                         default=0,
        help_text='Tax added (usually 0 for B2B fulfillment).')

    # ── currency ────────────────────────────────────────────────────────────
    currency_code   = models.CharField(max_length=4, default='USD',
        help_text='ISO 4217 code from Amount.currencyCode (USD, GBP, EUR, AED, SAR)')

    # ── data hazard flags (Section K) ──────────────────────────────────────
    is_amzn_generated = models.BooleanField(default=False, db_index=True,
        help_text='K1: msku begins with amzn. (grade-and-resell). Excluded from '
                  'all impact analysis per business rule.')
    is_adjustment   = models.BooleanField(default=False,
        help_text='K2: quantity=0 or amount<0, indicating reclassification between '
                  'components. Stored but excluded from rate derivation.')

    # ── source tracking ────────────────────────────────────────────────────
    query_date_range_start = models.DateField(null=True, blank=True,
        help_text='startDate of the Data Kiosk query this row came from.')
    query_date_range_end = models.DateField(null=True, blank=True,
        help_text='endDate of the Data Kiosk query this row came from.')

    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_fba_fee_component'
        unique_together = [['marketplace', 'msku', 'date', 'fee_type', 'component_name']]
        ordering        = ['marketplace', 'msku', '-date', 'component_name']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'msku', '-date']),
            models.Index(fields=['is_amzn_generated', '-date']),
            models.Index(fields=['is_adjustment', '-date']),
        ]

    def __str__(self):
        sign = '±' if self.is_adjustment else ''
        return (f'{self.marketplace.upper()} {self.msku} {self.date} '
                f'{self.component_name} {sign}{self.amount}{self.currency_code}')


class DataKioskIngestLog(models.Model):
    """
    Audit log for Data Kiosk ingest runs (Phase 2 component pulls).

    Tracks submission, polling, parsing, and persistence of each
    data pull for operational visibility and troubleshooting.
    """
    STATUS_CHOICES = [
        ('queued',      'Queued — waiting to start'),
        ('submitted',   'Submitted — query sent to Amazon'),
        ('polling',     'Polling — waiting for completion'),
        ('parsing',     'Parsing — processing response'),
        ('ingesting',   'Ingesting — writing to database'),
        ('ok',          'OK — completed successfully'),
        ('partial',     'Partial — completed with warnings'),
        ('error',       'Error — failed'),
        ('cancelled',   'Cancelled — stopped by user'),
    ]

    marketplace         = models.CharField(max_length=8, db_index=True)
    fee_type            = models.CharField(max_length=32, default='FBA_FULFILLMENT_FEE',
        help_text='Fee type requested: FBA_FULFILLMENT_FEE or FBA_STORAGE_FEE')

    query_start_date    = models.DateField()
    query_end_date      = models.DateField()

    status              = models.CharField(max_length=12, choices=STATUS_CHOICES,
                                          default='queued', db_index=True)

    # ── Amazon response tracking ───────────────────────────────────────────
    query_id            = models.CharField(max_length=128, blank=True, unique=True,
        help_text='Data Kiosk Query.queryId for idempotency and resumption.')
    response_status     = models.CharField(max_length=32, blank=True,
        help_text='Data Kiosk Query.processingStatus: IN_QUEUE, IN_PROGRESS, DONE')

    # ── parse results ──────────────────────────────────────────────────────
    rows_received       = models.IntegerField(default=0,
        help_text='Total JSONL rows in the response.')
    rows_inserted       = models.IntegerField(default=0,
        help_text='Rows successfully inserted (excluding filtered/amended).')
    rows_filtered_amzn  = models.IntegerField(default=0,
        help_text='K1: amzn.* MSKUs excluded.')
    rows_marked_adjustment = models.IntegerField(default=0,
        help_text='K2: quantity=0 or amount<0 marked as adjustment.')
    rows_skipped_empty  = models.IntegerField(default=0,
        help_text='K3: rows with no fees (skipped, not inserted).')

    # ── timing ─────────────────────────────────────────────────────────────
    submitted_at        = models.DateTimeField(auto_now_add=True)
    completed_at        = models.DateTimeField(null=True, blank=True)
    duration_seconds    = models.IntegerField(default=0)

    # ── error tracking ────────────────────────────────────────────────────
    error_message       = models.TextField(blank=True)
    error_stage         = models.CharField(max_length=32, blank=True,
        help_text='Stage where error occurred: query, polling, parsing, ingesting')

    # ── operational notes ──────────────────────────────────────────────────
    note                = models.TextField(blank=True)
    triggered_by        = models.CharField(max_length=64, blank=True,
        help_text='Who/what triggered this ingest (e.g. "cron", "user:farhan")')

    class Meta:
        db_table        = 'ix_data_kiosk_ingest_log'
        ordering        = ['-submitted_at']
        indexes = [
            models.Index(fields=['marketplace', '-submitted_at']),
            models.Index(fields=['status', '-submitted_at']),
        ]

    def __str__(self):
        return (f'{self.marketplace.upper()} {self.query_start_date} → '
                f'{self.query_end_date} [{self.status}]')
