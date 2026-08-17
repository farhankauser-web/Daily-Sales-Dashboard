"""
apps/dashboard/models.py — COGS, Targets, Product Catalog, Cached Metrics
"""
from django.db import models
from django.conf import settings


# ── PRODUCT CATALOG ───────────────────────────────────────────────────────────
class Product(models.Model):
    MARKETPLACE_CHOICES = [
        ('usa', '🇺🇸 USA'), ('ca', '🇨🇦 Canada'), ('uk', '🇬🇧 UK'),
        ('de', '🇩🇪 Germany'), ('ae', '🇦🇪 UAE'), ('sa', '🇸🇦 KSA'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'), ('inactive', 'Inactive'),
        ('suppressed', 'Suppressed'), ('out_of_stock', 'Out of Stock'),
    ]

    asin        = models.CharField(max_length=16)
    sku         = models.CharField(max_length=64, blank=True)
    marketplace = models.CharField(max_length=8, choices=MARKETPLACE_CHOICES)
    title       = models.CharField(max_length=256)
    category    = models.CharField(max_length=64, blank=True)
    brand       = models.CharField(max_length=64, default='Infinitee Xclusives')
    status      = models.CharField(max_length=16, choices=STATUS_CHOICES, default='active')

    # Dimensions / weight for shipping calc
    weight_lbs  = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    length_in   = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    width_in    = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    height_in   = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # Pricing
    list_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sale_price  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Amazon fees (cached from FBA fee API)
    fba_fee     = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    referral_fee_pct = models.DecimalField(max_digits=5, decimal_places=2, default=15.0)

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    updated_by  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, related_name='products_updated')

    class Meta:
        db_table = 'ix_products'
        unique_together = [['asin', 'marketplace']]
        ordering = ['marketplace', 'asin']

    def __str__(self):
        return f'{self.asin} — {self.title[:40]}'

    @property
    def selling_price(self):
        return self.sale_price or self.list_price or 0

    @property
    def referral_fee(self):
        return float(self.selling_price) * float(self.referral_fee_pct) / 100

    @property
    def net_revenue(self):
        return float(self.selling_price) - float(self.fba_fee or 0) - self.referral_fee


# ── COGS ──────────────────────────────────────────────────────────────────────
class COGSEntry(models.Model):
    """Cost of Goods Sold per SKU per month."""
    product     = models.ForeignKey(Product, on_delete=models.CASCADE,
                                    related_name='cogs_entries')
    month       = models.DateField(help_text='First day of month, e.g. 2026-05-01')

    # Cost breakdown
    unit_cost       = models.DecimalField(max_digits=10, decimal_places=4,
                                          help_text='FOB cost per unit (USD)')
    shipping_cost   = models.DecimalField(max_digits=10, decimal_places=4, default=0,
                                          help_text='Per-unit sea/air freight')
    duties_cost     = models.DecimalField(max_digits=10, decimal_places=4, default=0,
                                          help_text='Per-unit import duties')
    prep_cost       = models.DecimalField(max_digits=10, decimal_places=4, default=0,
                                          help_text='3PL prep / labelling per unit')
    other_cost      = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    notes       = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_cogs'
        unique_together = [['product', 'month']]
        ordering = ['-month', 'product']

    def __str__(self):
        return f'{self.product.asin} — {self.month:%Y-%m} — ${self.total_cost}'

    @property
    def total_cost(self):
        return (float(self.unit_cost) + float(self.shipping_cost) +
                float(self.duties_cost) + float(self.prep_cost) + float(self.other_cost))


# ── FBA FEE RATES ─────────────────────────────────────────────────────────────
class FBAFeeRate(models.Model):
    """
    Per-SKU FBA fulfilment fee with an effective date.

    Lookup at sync time: for an order on date D, pick the most-recent FBAFeeRate
    where effective_from <= D. Falls back to COGSEntry.shipping_cost if no rate
    exists for the order's date — so users who don't track Amazon's peak/off-peak
    cycle separately keep the previous behaviour.

    Typical use: 2 rows per product per year (peak start ~ Oct 15, peak end ~ Jan 15).
    """
    product          = models.ForeignKey(Product, on_delete=models.CASCADE,
                                         related_name='fba_fee_rates')
    effective_from   = models.DateField(
        help_text='First day this rate applies (inclusive). Stays in effect '
                  'until a later FBAFeeRate row for the same product takes over.'
    )
    fba_fee_per_unit = models.DecimalField(max_digits=10, decimal_places=4,
                                           help_text='USD per unit')
    notes            = models.TextField(blank=True)
    uploaded_by      = models.ForeignKey(settings.AUTH_USER_MODEL,
                                         on_delete=models.SET_NULL, null=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_fba_fee_rates'
        unique_together = [['product', 'effective_from']]
        ordering        = ['product', '-effective_from']
        indexes         = [models.Index(fields=['product', 'effective_from'])]

    def __str__(self):
        return f'{self.product.sku or self.product.asin} from {self.effective_from} → ${self.fba_fee_per_unit}'


# ── MONTHLY TARGETS ───────────────────────────────────────────────────────────
class MonthlyTarget(models.Model):
    MARKETPLACE_CHOICES = [
        ('usa', '🇺🇸 United States'),
        ('ca',  '🇨🇦 Canada'),
        ('uk',  '🇬🇧 United Kingdom'),
        ('de',  '🇩🇪 Germany'),
        ('ae',  '🇦🇪 UAE'),
        ('sa',  '🇸🇦 Saudi Arabia'),
    ]

    marketplace = models.CharField(max_length=8, choices=MARKETPLACE_CHOICES)
    month       = models.DateField(help_text='First day of month')

    revenue_target  = models.DecimalField(max_digits=14, decimal_places=2)
    units_target    = models.IntegerField()
    tacos_target    = models.DecimalField(max_digits=5, decimal_places=2,
                                          help_text='TACoS target %, e.g. 14.00')
    gm_target       = models.DecimalField(max_digits=5, decimal_places=2,
                                          help_text='Gross margin % target')
    ppc_budget      = models.DecimalField(max_digits=12, decimal_places=2,
                                          help_text='Monthly PPC budget cap')
    notes           = models.TextField(blank=True)

    created_by  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_monthly_targets'
        unique_together = [['marketplace', 'month']]
        ordering = ['-month', 'marketplace']

    def __str__(self):
        return f'{self.marketplace.upper()} — {self.month:%Y-%m} — Rev: ${self.revenue_target:,.0f}'

    @property
    def daily_revenue_target(self):
        import calendar
        days = calendar.monthrange(self.month.year, self.month.month)[1]
        return float(self.revenue_target) / days


class ProductMonthlyTarget(models.Model):
    """Revenue target per product per month for planning grids."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='monthly_targets')
    month = models.DateField(help_text='First day of month')
    revenue_target = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_product_monthly_targets'
        unique_together = [['product', 'month']]
        ordering = ['month', 'product']

    def __str__(self):
        return f'{self.product.asin} — {self.month:%Y-%m} — ${self.revenue_target:,.0f}'


class ProductTypePackMonthlyTarget(models.Model):
    """Revenue target per product type + pack size per month."""
    marketplace = models.CharField(max_length=8)
    product_type = models.CharField(max_length=128)
    pack_size = models.CharField(max_length=64)
    month = models.DateField(help_text='First day of month')
    revenue_target = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_product_type_pack_monthly_targets'
        unique_together = [['marketplace', 'product_type', 'pack_size', 'month']]
        ordering = ['month', 'marketplace', 'product_type', 'pack_size']

    def __str__(self):
        return (
            f'{self.marketplace.upper()} — {self.product_type} — {self.pack_size} '
            f'— {self.month:%Y-%m} — ${self.revenue_target:,.0f}'
        )

# ── CACHED DAILY METRICS ──────────────────────────────────────────────────────
class DailyMetric(models.Model):
    """
    Stores the SP-API + Ads API pulled data per day per marketplace.
    Acts as the source for historical charts and trend analysis.
    """
    marketplace = models.CharField(max_length=8)
    date        = models.DateField()

    # Sales
    revenue         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # VAT-exclusive revenue (UK/AE/SA: gross / (1+vat); equals revenue where no VAT)
    revenue_net     = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    units           = models.IntegerField(default=0)
    orders          = models.IntegerField(default=0)
    sessions        = models.IntegerField(default=0)
    page_views      = models.IntegerField(default=0)
    conversion_rate = models.DecimalField(max_digits=6, decimal_places=4, default=0)

    # Advertising
    ppc_spend       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    ppc_sales       = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    ppc_impressions = models.IntegerField(default=0)
    ppc_clicks      = models.IntegerField(default=0)
    acos            = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    roas            = models.DecimalField(max_digits=8, decimal_places=4, default=0)

    # Cost breakdown (frozen at sync time so historical reports are stable)
    cgs             = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                          help_text='Sum of COGS unit_cost × qty across all orders this day')
    amazon_fee      = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                          help_text='Amazon referral fee — typically revenue × 15%')
    fba_fee         = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                          help_text='Amazon FBA fulfilment fee — from COGS shipping_cost × qty')

    # Derived
    tacos           = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    gross_margin    = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gm_pct          = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    contribution_margin = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cm_pct          = models.DecimalField(max_digits=6, decimal_places=4, default=0)

    synced_at    = models.DateTimeField(auto_now=True)
    finalized_at = models.DateTimeField(null=True, blank=True,
        help_text="Set by the 00:45 finalize_yesterday cron once the day's "
                  "order data is locked. Hourly cron skips finalized rows. "
                  "PPC fields may still update via backfill_ppc for 7 days.")

    class Meta:
        db_table = 'ix_daily_metrics'
        unique_together = [['marketplace', 'date']]
        ordering = ['-date', 'marketplace']
        indexes = [
            models.Index(fields=['marketplace', 'date']),
            models.Index(fields=['date']),
        ]

    def __str__(self):
        return f'{self.marketplace.upper()} — {self.date} — ${self.revenue:,.2f}'

    @property
    def tacos_pct(self):
        return float(self.tacos) * 100

    @property
    def acos_pct(self):
        return float(self.acos) * 100


# ── DAILY SKU SNAPSHOT ────────────────────────────────────────────────────────
class DailySkuSnapshot(models.Model):
    """
    Per-SKU revenue / cost breakdown for one day.
    Populated by sync_daily_metrics (and its --include-today flag) so the
    daily dashboard can show the Product Performance table from cache
    without waiting for a live Amazon report.
    """
    marketplace = models.CharField(max_length=8, db_index=True)
    date        = models.DateField(db_index=True)
    sku         = models.CharField(max_length=64)
    asin        = models.CharField(max_length=16, blank=True)
    qty         = models.IntegerField(default=0)
    revenue     = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cgs         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amz_fee     = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fulfill     = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cm           = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    synced_at    = models.DateTimeField(auto_now=True)
    finalized_at = models.DateTimeField(null=True, blank=True,
        help_text="Set by finalize_yesterday cron — locks per-SKU row from further writes.")

    class Meta:
        db_table = 'ix_daily_sku_snapshot'
        unique_together = [['marketplace', 'date', 'sku']]
        indexes = [models.Index(fields=['marketplace', 'date'])]

    def __str__(self):
        return f'{self.marketplace.upper()} {self.date} {self.sku} ${self.revenue}'


# ── INVENTORY SNAPSHOT ────────────────────────────────────────────────────────
class InventorySnapshot(models.Model):
    """
    Daily FBA inventory levels per ASIN per marketplace.
    Populated by sync_amazon_data via FBA Inventory API.
    """
    product              = models.ForeignKey(Product, on_delete=models.CASCADE,
                                              related_name='inventory_snapshots')
    date                 = models.DateField()

    # FBA warehouse quantities
    afn_fulfillable      = models.IntegerField(default=0)
    afn_reserved         = models.IntegerField(default=0)
    afn_inbound_working  = models.IntegerField(default=0)
    afn_inbound_shipped  = models.IntegerField(default=0)
    afn_inbound_receiving= models.IntegerField(default=0)
    afn_unsellable       = models.IntegerField(default=0)

    # 3PL / AWD stock
    warehouse_stock      = models.IntegerField(default=0)

    # Computed fields
    days_cover           = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    reorder_point        = models.IntegerField(default=0)
    safety_stock         = models.IntegerField(default=0)

    created_at           = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ix_inventory_snapshots'
        unique_together = [['product', 'date']]
        ordering        = ['-date', 'product']

    def __str__(self):
        return f'{self.product.asin} — {self.date} — {self.afn_fulfillable} units'

    @property
    def total_available(self):
        return (self.afn_fulfillable + self.afn_inbound_working +
                self.afn_inbound_shipped + self.afn_inbound_receiving)

    @property
    def stock_alert(self):
        dc = float(self.days_cover)
        if self.afn_fulfillable <= 0:
            return 'stockout'
        if dc < 14:
            return 'critical'
        if dc < 30:
            return 'low'
        return 'ok'


# ── PPC CAMPAIGN SNAPSHOT ─────────────────────────────────────────────────────
class PPCCampaignSnapshot(models.Model):
    CAMPAIGN_TYPES = [('sp','Sponsored Products'),('sb','Sponsored Brands'),('sd','Sponsored Display')]
    STATE_CHOICES  = [('enabled','Enabled'),('paused','Paused'),('archived','Archived')]

    marketplace   = models.CharField(max_length=8)
    date          = models.DateField()
    campaign_id   = models.CharField(max_length=64)
    campaign_name = models.CharField(max_length=256)
    campaign_type = models.CharField(max_length=4, choices=CAMPAIGN_TYPES, default='sp')
    state         = models.CharField(max_length=12, choices=STATE_CHOICES, default='enabled')
    portfolio     = models.CharField(max_length=128, blank=True)

    impressions   = models.IntegerField(default=0)
    clicks        = models.IntegerField(default=0)
    spend         = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sales_7d      = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    orders_7d     = models.IntegerField(default=0)
    units_7d      = models.IntegerField(default=0)
    acos          = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    roas          = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    ctr           = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cvr           = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cpc           = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    daily_budget  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    budget_consumed = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ix_ppc_snapshots'
        unique_together = [['marketplace', 'date', 'campaign_id']]
        ordering        = ['-date', 'marketplace', '-spend']
        indexes = [
            models.Index(fields=['marketplace', 'date']),
            models.Index(fields=['campaign_id']),
        ]

    def __str__(self):
        return f'{self.campaign_name} — {self.date} — ${self.spend}'

    @property
    def acos_pct(self):
        return float(self.acos) * 100

    @property
    def efficiency_score(self):
        acos_s = max(0, 1 - float(self.acos))
        cvr_s  = min(1, float(self.cvr) * 10)
        return round((acos_s * 0.6 + cvr_s * 0.4) * 100, 1)


# ── PER-ASIN PPC SNAPSHOT ─────────────────────────────────────────────────────
class PPCProductSnapshot(models.Model):
    """
    Daily per-ASIN Sponsored Products spend, sourced from the
    Ads API v3 spAdvertisedProduct report.
    """
    CAMPAIGN_TYPES = [('sp', 'Sponsored Products'), ('sd', 'Sponsored Display'), ('sb', 'Sponsored Brands')]

    marketplace   = models.CharField(max_length=8)
    date          = models.DateField()
    asin          = models.CharField(max_length=16)
    sku           = models.CharField(max_length=64, blank=True)
    campaign_type = models.CharField(max_length=4, choices=CAMPAIGN_TYPES, default='sp')

    impressions   = models.IntegerField(default=0)
    clicks        = models.IntegerField(default=0)
    spend         = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sales_7d      = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    orders_7d     = models.IntegerField(default=0)
    units_7d      = models.IntegerField(default=0)

    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ix_ppc_product_snapshots'
        unique_together = [['marketplace', 'date', 'asin', 'campaign_type']]
        ordering        = ['-date', '-spend']
        indexes = [
            models.Index(fields=['marketplace', 'date']),
            models.Index(fields=['asin']),
            models.Index(fields=['sku']),
        ]

    def __str__(self):
        return f'{self.asin} ({self.sku}) — {self.date} — ${self.spend}'


# ── OPERATIONAL ALERTS ────────────────────────────────────────────────────────
class Alert(models.Model):
    SEVERITY = [('critical','Critical'),('warning','Warning'),('info','Info')]
    CATEGORY = [
        ('inventory','Inventory'),('ppc','PPC'),
        ('performance','Performance'),('system','System'),
    ]

    marketplace  = models.CharField(max_length=8, blank=True)
    severity     = models.CharField(max_length=12, choices=SEVERITY)
    category     = models.CharField(max_length=16, choices=CATEGORY)
    title        = models.CharField(max_length=128)
    message      = models.TextField()
    asin         = models.CharField(max_length=16, blank=True)
    metric_key   = models.CharField(max_length=64, blank=True)
    metric_value = models.CharField(max_length=32, blank=True)
    threshold    = models.CharField(max_length=32, blank=True)

    is_read      = models.BooleanField(default=False)
    is_resolved  = models.BooleanField(default=False)
    resolved_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='resolved_alerts'
    )
    resolved_at  = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ix_alerts'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['marketplace', 'is_read']),
            models.Index(fields=['severity', 'is_resolved']),
        ]

    def __str__(self):
        return f'[{self.severity.upper()}] {self.title}'

    @classmethod
    def create_inventory_alert(cls, product, days_cover, fulfillable):
        severity = 'critical' if days_cover < 7 else 'warning'
        cls.objects.get_or_create(
            marketplace=product.marketplace,
            asin=product.asin,
            metric_key='days_cover',
            is_resolved=False,
            defaults={
                'severity': severity,
                'category': 'inventory',
                'title':   f'{"STOCKOUT RISK" if days_cover < 7 else "Low Stock"}: {product.asin}',
                'message': (
                    f'{product.title[:60]} — {fulfillable} units fulfillable, '
                    f'{days_cover:.0f} days cover. Lead time 45 days.'
                ),
                'metric_value': str(round(days_cover, 1)),
                'threshold':   '30',
            }
        )

    @classmethod
    def create_tacos_alert(cls, marketplace, tacos_pct, target_pct):
        cls.objects.get_or_create(
            marketplace=marketplace,
            metric_key='tacos',
            is_resolved=False,
            defaults={
                'severity':    'warning',
                'category':    'ppc',
                'title':       f'TACoS Spike: {marketplace.upper()} at {tacos_pct:.1f}%',
                'message':     f'TACoS is {tacos_pct:.1f}%, above target of {target_pct:.1f}%. Review PPC bids.',
                'metric_value': str(round(tacos_pct, 1)),
                'threshold':    str(round(target_pct, 1)),
            }
        )


# ── HOURLY SNAPSHOTS ──────────────────────────────────────────────────────────
class HourlyMetricSnapshot(models.Model):
    """
    Per-hour delta snapshot of a marketplace's metrics.
    Each row = "what happened during hour H on date D".
    Hour is 0-23 in the marketplace's local timezone.

    Populated by the snapshot_hourly_metrics command (runs every hour).
    Pruned to 30 days by the prune_hourly_snapshots command (runs weekly).
    """
    marketplace          = models.CharField(max_length=8)
    date                 = models.DateField(help_text="Date in marketplace local TZ")
    hour                 = models.PositiveSmallIntegerField(help_text="0-23, marketplace local TZ")

    # Sales (per-hour delta, not cumulative)
    revenue              = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    units                = models.IntegerField(default=0)
    orders               = models.IntegerField(default=0)

    # Costs
    cgs                  = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amazon_fee           = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fba_fee              = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Derived
    gross_margin         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gm_pct               = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    contribution_margin  = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cm_pct               = models.DecimalField(max_digits=6, decimal_places=4, default=0)

    # PPC (0 until Ads pipeline writes here; placeholder so the page renders)
    ppc_spend            = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    synced_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_hourly_metric_snapshot'
        unique_together = [['marketplace', 'date', 'hour']]
        indexes = [
            models.Index(fields=['marketplace', '-date', 'hour']),
            models.Index(fields=['-date']),
        ]
        ordering = ['-date', 'hour']

    def __str__(self):
        return f'{self.marketplace.upper()} {self.date} h{self.hour:02d} · ${self.revenue}'


class HourlySkuSnapshot(models.Model):
    """
    Per-SKU per-hour delta snapshot.
    Powers: "Last hour units sold per SKU" on daily dashboard,
            click-to-drill on the Hourly Patterns heatmap.
    """
    marketplace          = models.CharField(max_length=8)
    date                 = models.DateField()
    hour                 = models.PositiveSmallIntegerField()
    sku                  = models.CharField(max_length=64)
    asin                 = models.CharField(max_length=16, blank=True)

    qty                  = models.IntegerField(default=0)
    revenue              = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cgs                  = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amazon_fee           = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fba_fee              = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    contribution_margin  = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    synced_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_hourly_sku_snapshot'
        unique_together = [['marketplace', 'date', 'hour', 'sku']]
        indexes = [
            models.Index(fields=['marketplace', 'date', 'hour']),
            models.Index(fields=['marketplace', 'sku', '-date']),
        ]
        ordering = ['-date', 'hour', 'sku']

    def __str__(self):
        return f'{self.marketplace.upper()} {self.date} h{self.hour:02d} {self.sku} · {self.qty}u'


# ── ADS DATA SYNC LOG ─────────────────────────────────────────────────────────
# Single source of truth for whether a (marketplace, date, source) tuple was
# successfully synced. Without this, an empty Amazon response is indistinguishable
# from a missing sync. Required by the layered-completeness architecture:
#
#   - Core completeness (controls whether a day renders on Hourly Patterns)
#     requires sp_hourly AND orders both with status in (ok, empty_from_amazon)
#   - Ads completeness (controls whether SB/SD numbers appear) requires
#     sb_daily and sd_daily independently
#
# Pruned alongside HourlyMetricSnapshot (30-day retention).
class AdsDataSyncLog(models.Model):
    SOURCE_CHOICES = [
        ('sp_hourly', 'SP Hourly (Ads API timeUnit=HOURLY)'),
        ('sb_daily',  'SB Daily Campaign Report'),
        ('sd_daily',  'SD Daily Campaign Report'),
        ('orders',    'SP-API Orders Hourly'),
        # Phase 1 detail reports — keep names <= 32 chars to fit `source` column
        ('sp_search_term_daily',        'SP Search Term Daily'),
        ('sb_search_term_daily',        'SB Search Term Daily'),
        ('sp_targeting_daily',          'SP Targeting Daily'),
        ('sb_targeting_daily',          'SB Targeting Daily'),
        ('sd_targeting_daily',          'SD Targeting Daily'),
        ('sp_advertised_product_daily', 'SP Advertised Product Daily'),
        ('sb_advertised_product_daily', 'SB Purchased Product Daily'),
        ('sd_advertised_product_daily', 'SD Advertised Product Daily'),
        ('sp_placement_daily',          'SP Placement Daily'),
        ('sb_placement_daily',          'SB Placement Daily'),
        ('sp_adgroup_daily',            'SP Ad Group Daily'),
        ('sb_adgroup_daily',            'SB Ad Group Daily'),
        ('sd_adgroup_daily',            'SD Ad Group Daily'),
        # Phase 3 Brand Analytics — weekly cadence (Mon-Sun)
        ('ba_search_query_weekly',      'BA Search Query Performance Weekly'),
        ('ba_item_comparison_weekly',   'BA Item Comparison Weekly (DEPRECATED)'),
        ('ba_market_basket_weekly',     'BA Market Basket Weekly'),
        ('ba_repeat_purchase_weekly',   'BA Repeat Purchase Weekly'),
    ]
    STATUS_CHOICES = [
        ('ok',                 'OK — rows received'),
        ('empty_from_amazon',  'OK — Amazon returned 0 rows (treat as 0 spend)'),
        ('failed',             'Failed — error during fetch / parse'),
        ('pending',            'In-flight — report submitted, waiting for Amazon'),
    ]

    marketplace    = models.CharField(max_length=8)
    date           = models.DateField()
    source         = models.CharField(max_length=32, choices=SOURCE_CHOICES)
    # Phase 3 — per-ASIN reports (Brand Analytics SQP / Item Comparison /
    # Market Basket) keep an asin scope so we can submit/track one report per
    # (week × ASIN). Empty for everything else so the unique key collapses to
    # (marketplace, date, source) for non-BA rows.
    asin           = models.CharField(max_length=16, blank=True, default='')
    status         = models.CharField(max_length=20, choices=STATUS_CHOICES,
                                      default='pending')
    rows_received  = models.IntegerField(default=0)
    error_message  = models.TextField(blank=True)
    report_id      = models.CharField(max_length=64, blank=True,
                                      help_text='Amazon Ads API report_id when applicable')
    last_synced    = models.DateTimeField(auto_now=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ix_ads_data_sync_log'
        unique_together = [['marketplace', 'date', 'source', 'asin']]
        ordering        = ['-date', 'marketplace', 'source']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'date', 'source']),
            models.Index(fields=['status', '-last_synced']),
        ]

    def __str__(self):
        return (f'{self.marketplace.upper()} {self.date} '
                f'{self.source} → {self.status} · {self.rows_received} rows')

    @property
    def is_successful(self) -> bool:
        """Distinguishes 'we know the answer' from 'we don't yet'."""
        return self.status in ('ok', 'empty_from_amazon')


# ── PPC CAMPAIGN HOURLY SNAPSHOT (SP ONLY) ────────────────────────────────────
# Real hourly Sponsored Products campaign data from Amazon Ads API
# (timeUnit = HOURLY). Sponsored Brands and Sponsored Display do NOT support
# hourly granularity from Amazon — they continue to be stored daily in
# PPCCampaignSnapshot and are estimated (uniform allocation) at query time.
#
# Time semantics: `hour` is 0-23 in MARKETPLACE LOCAL TIMEZONE, matching the
# rest of the hourly pipeline (HourlyMetricSnapshot). Amazon's report returns
# `time_window_start` per row; ingestion converts to marketplace local hour.
class PPCCampaignHourlySnapshot(models.Model):
    marketplace      = models.CharField(max_length=8)
    date             = models.DateField(help_text='Date in marketplace local TZ')
    hour             = models.PositiveSmallIntegerField(
        help_text='0-23, marketplace local TZ')
    campaign_id      = models.CharField(max_length=64)
    campaign_name    = models.CharField(max_length=256, blank=True)
    campaign_type    = models.CharField(max_length=4, default='sp',
        help_text="'sp' | 'sb' | 'sd' — populated from AMS event dataset_id")

    spend            = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    impressions      = models.BigIntegerField(default=0)
    clicks           = models.BigIntegerField(default=0)
    orders_7d        = models.IntegerField(default=0)
    sales_7d         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    units_7d         = models.IntegerField(default=0)

    # Provenance — drives the "Real" vs "Manual" badge on the UI.
    SOURCE_CHOICES = [
        ('ams',    'Amazon Marketing Stream (real-time push)'),
        ('manual', 'Manual upload (Seller Central hourly export)'),
    ]
    source           = models.CharField(max_length=8, choices=SOURCE_CHOICES,
                                        default='ams')
    synced_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_ppc_campaign_hourly_snapshot'
        # campaign_type included for defense-in-depth: Amazon campaign IDs are
        # globally unique across SP/SB/SD per their docs, but treating type as
        # part of the unique key prevents silent overwrite if that ever changes.
        unique_together = [['marketplace', 'date', 'hour',
                            'campaign_id', 'campaign_type']]
        ordering        = ['-date', 'hour', 'campaign_id']
        indexes = [
            models.Index(fields=['marketplace', '-date', 'hour']),
            models.Index(fields=['marketplace', 'campaign_id', '-date']),
            models.Index(fields=['-date', 'hour']),
        ]

    def __str__(self):
        return (f'{self.marketplace.upper()} {self.date} h{self.hour:02d} '
                f'{self.campaign_name[:30]} · ${self.spend}')


# ─────────────────────────────────────────────────────────────────────────────
# Amazon Marketing Stream (AMS) — subscription registry + dedup ledger
# ─────────────────────────────────────────────────────────────────────────────

class AdsStreamSubscription(models.Model):
    """
    One row per active AMS subscription (per marketplace, per dataset).

    AMS pushes events through SNS → Firehose → S3. We persist subscription
    metadata so the consumer knows which S3 prefix to poll, and so we can
    re-check status from the Ads API on schedule.
    """
    DATASET_CHOICES = [
        ('sp-traffic',    'SP Traffic (impressions / clicks / cost per minute)'),
        ('sp-conversion', 'SP Conversion (orders / sales, attribution-windowed)'),
        ('sb-traffic',    'SB Traffic'),
        ('sb-conversion', 'SB Conversion'),
        ('sd-traffic',    'SD Traffic'),
        ('sd-conversion', 'SD Conversion'),
        ('budget-usage',  'Budget Usage (% budget consumed)'),
    ]
    STATUS_CHOICES = [
        ('ACTIVE',              'ACTIVE — pushing events'),
        ('FAILED_PROVISIONING', 'FAILED_PROVISIONING'),
        ('ARCHIVED',            'ARCHIVED — stopped'),
        ('UNKNOWN',             'UNKNOWN — never refreshed from API'),
    ]
    marketplace           = models.CharField(max_length=8)
    dataset_id            = models.CharField(max_length=32, choices=DATASET_CHOICES)
    subscription_id       = models.CharField(max_length=64, unique=True)
    status                = models.CharField(max_length=24, choices=STATUS_CHOICES, default='UNKNOWN')
    delivery_stream_arn   = models.CharField(max_length=256, blank=True)
    subscription_role_arn = models.CharField(max_length=256, blank=True)
    subscriber_role_arn   = models.CharField(max_length=256, blank=True)
    s3_bucket             = models.CharField(max_length=128, blank=True,
                              help_text='S3 bucket the Firehose writes to')
    s3_prefix             = models.CharField(max_length=256, blank=True,
                              help_text='Prefix Firehose uses inside the bucket')
    last_status_check     = models.DateTimeField(null=True, blank=True)
    last_ingest_at        = models.DateTimeField(null=True, blank=True,
                              help_text='Last time the consumer processed an object for this sub')
    created_at            = models.DateTimeField(auto_now_add=True)
    updated_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_ads_stream_subscription'
        # No (marketplace, dataset) uniqueness — Amazon allows multiple subs
        # per dataset (e.g. failed-then-active). `subscription_id` is the
        # only true unique key. The consumer filters by status='ACTIVE'.
        indexes = [
            models.Index(fields=['marketplace', 'dataset_id']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.marketplace.upper()} · {self.dataset_id} · {self.status}'


class AmsProcessedObject(models.Model):
    """
    Dedup ledger — every S3 object processed by ingest_ams_s3 is recorded here
    so re-runs don't double-count events.

    Each (bucket, key) is unique. Process-once semantics.
    """
    marketplace      = models.CharField(max_length=8)
    s3_bucket        = models.CharField(max_length=128)
    s3_key           = models.CharField(max_length=1024)
    object_size      = models.BigIntegerField(default=0)
    records_parsed   = models.IntegerField(default=0,
                          help_text='Total inner AMS records found in the object')
    records_used     = models.IntegerField(default=0,
                          help_text='Records that mapped to a known dataset and got aggregated')
    error_message    = models.TextField(blank=True)
    processed_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ix_ams_processed_object'
        unique_together = [['s3_bucket', 's3_key']]
        ordering        = ['-processed_at']
        indexes = [
            models.Index(fields=['marketplace', '-processed_at']),
            models.Index(fields=['s3_bucket', 's3_key']),
        ]

    def __str__(self):
        return f's3://{self.s3_bucket}/{self.s3_key[-60:]}'


# ─────────────────────────────────────────────────────────────────────────────
# Manual hourly CSV upload — audit trail for files imported via the UI
# ─────────────────────────────────────────────────────────────────────────────
class AdsManualHourlyUpload(models.Model):
    """
    One row per CSV the user uploads from Seller Central's hourly report.

    Used to:
      • audit who uploaded what and when
      • re-process or delete an upload (we keep `original_filename` so the user
        can recognise it in the audit list)
      • show "X days backfilled manually" in the Hourly Patterns status bar
    """
    AD_TYPE_CHOICES = [
        ('sp', 'Sponsored Products'),
        ('sb', 'Sponsored Brands'),
        ('sd', 'Sponsored Display'),
    ]
    STATUS_CHOICES = [
        ('ok',     'OK — rows imported'),
        ('failed', 'Failed — see error'),
    ]
    marketplace        = models.CharField(max_length=8)
    ad_type            = models.CharField(max_length=2, choices=AD_TYPE_CHOICES)
    uploaded_by        = models.ForeignKey(settings.AUTH_USER_MODEL,
                                            on_delete=models.SET_NULL,
                                            null=True, blank=True,
                                            related_name='ams_manual_uploads')
    original_filename  = models.CharField(max_length=256)
    date_range_start   = models.DateField(null=True, blank=True)
    date_range_end     = models.DateField(null=True, blank=True)
    rows_in_file       = models.IntegerField(default=0)
    rows_imported      = models.IntegerField(default=0,
                          help_text='(campaign, hour) buckets upserted')
    days_covered       = models.IntegerField(default=0)
    status             = models.CharField(max_length=12,
                                          choices=STATUS_CHOICES,
                                          default='ok')
    error_message      = models.TextField(blank=True)
    uploaded_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table  = 'ix_ads_manual_hourly_upload'
        ordering  = ['-uploaded_at']
        indexes = [
            models.Index(fields=['marketplace', '-uploaded_at']),
            models.Index(fields=['ad_type']),
        ]

    def __str__(self):
        return (f'{self.marketplace.upper()} {self.ad_type.upper()} '
                f'{self.date_range_start}→{self.date_range_end} '
                f'· {self.rows_imported} rows · {self.original_filename}')


# ─────────────────────────────────────────────────────────────────────────────
# SKU-level PPC allocation — financial-grade, reproducible, auditable.
#
# Computed per §1–5 of the spec:
#   PASS 1: Campaign → ASIN  (SP from spAdvertisedProduct; SB/SD from
#           7/30-day revenue mix; group/equal-split fallbacks)
#   PASS 2: ASIN → SKU       (65/25/10 blend of T7 rev / T30 rev / catalog price)
# Reconciliation: per (campaign, day) Σ SKU spend = Campaign spend ±$0.01
# Smoothing: EMA 0.7/0.3 on provisional and settling rows
# Lock at T+3 once data settles → row becomes immutable for finance reports.
# ─────────────────────────────────────────────────────────────────────────────
class SkuPpcAllocation(models.Model):
    ATTRIBUTION_SOURCES = [
        ('sp_advertised_product',  'SP (authoritative spAdvertisedProduct report)'),
        ('sb_revenue_share',       'SB (target-set revenue mix)'),
        ('sd_revenue_share',       'SD (target-set revenue mix)'),
        ('group_revenue_share',    'Fallback (product-group revenue mix)'),
        ('sp_provisional',         "Provisional (carry yesterday's ASIN weights)"),
        ('cold_start_equal',       'Cold-start (equal split, new ASIN)'),
        ('cold_start_catalog',     'Cold-start (catalog price share, new SKU)'),
        ('reconciled',             'Residual reconciled to match campaign spend'),
        ('unallocated',            'Could not be mapped — held aside'),
    ]
    SETTLEMENT_STATES = [
        ('provisional', 'Provisional — data still arriving'),
        ('settling',    'Settling — within T+3 window'),
        ('locked',      'Locked — immutable'),
    ]

    marketplace        = models.CharField(max_length=8)
    date               = models.DateField(help_text='Marketplace local TZ')
    sku                = models.CharField(max_length=64)
    asin               = models.CharField(max_length=16)
    campaign_id        = models.CharField(max_length=64)
    campaign_type      = models.CharField(max_length=4)   # 'sp' | 'sb' | 'sd'

    # Pass 1 inputs / outputs
    campaign_spend     = models.DecimalField(max_digits=12, decimal_places=4, default=0,
                          help_text='Input — campaign total for the day')
    asin_weight        = models.DecimalField(max_digits=8, decimal_places=6, default=0,
                          help_text='Pass 1 weight (0–1)')
    # Pass 2 inputs / outputs
    sku_weight         = models.DecimalField(max_digits=8, decimal_places=6, default=0,
                          help_text='Pass 2 weight (0–1)')

    # Final
    sku_ppc_spend      = models.DecimalField(max_digits=12, decimal_places=4, default=0)

    # Audit — cached signals so the value is reproducible without re-querying
    revenue_t7_sku     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    revenue_t30_sku    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    revenue_t7_asin    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    revenue_t30_asin   = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Provenance + trust
    attribution_source = models.CharField(max_length=28, choices=ATTRIBUTION_SOURCES,
                                          default='unallocated')
    confidence_score   = models.DecimalField(max_digits=3, decimal_places=2, default=0,
                          help_text='0.00 (low) to 1.00 (high)')
    settlement_state   = models.CharField(max_length=12, choices=SETTLEMENT_STATES,
                                          default='provisional')

    computed_at        = models.DateTimeField(auto_now=True)
    locked_at          = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'ix_sku_ppc_allocation'
        unique_together = [['marketplace', 'date', 'sku', 'asin', 'campaign_id']]
        ordering = ['-date', 'marketplace', 'sku']
        indexes = [
            models.Index(fields=['marketplace', '-date', 'sku'],
                         name='ix_sku_ppc_dash_idx'),
            models.Index(fields=['settlement_state', '-date'],
                         name='ix_sku_ppc_state_idx'),
            models.Index(fields=['date', 'campaign_id'],
                         name='ix_sku_ppc_recon_idx'),
        ]

    def __str__(self):
        return (f'{self.marketplace.upper()} {self.date} '
                f'{self.sku}·{self.asin}·{self.campaign_id} = '
                f'${self.sku_ppc_spend} [{self.settlement_state}]')


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1 — CAMPAIGN INTELLIGENCE & SEARCH-TERM ANALYTICS
#
# Reporting-only schema (NO write-back to Amazon). Powers the Campaign
# Performance Center, Campaign Detail, Search Term Intelligence, Targeting,
# Placement Analytics, and per-campaign / per-search-term profitability.
#
# Design constraints:
#   • Stay on SQLite for Phase 1 but design for future Postgres migration —
#     no SQLite-specific functions; all unique constraints fit btree limits
#     (search_term hashed for dedup); all FKs are explicit (no GenericRelation).
#   • AdsAdvertisedProductDailySnapshot reserves nullable BA-share columns so
#     Phase 3 Brand Analytics ingestion can slot in without redesign.
#   • Aggregate caches (CampaignProfitDaily, CampaignSearchTermSummary) are
#     the hot read path — raw fact tables are queried only for drill-downs.
# ═════════════════════════════════════════════════════════════════════════════

_AD_TYPE_CHOICES = [
    ('sp', 'Sponsored Products'),
    ('sb', 'Sponsored Brands'),
    ('sd', 'Sponsored Display'),
]


class Campaign(models.Model):
    """
    Dimension table — one row per campaign that has ever existed in the account.

    Populated by `backfill_campaign_dim` from PPCCampaignSnapshot and refreshed
    daily. Brand / product_family / initials are parsed heuristically from the
    campaign name (see apps.dashboard.campaign_dim) and can be manually
    overridden via the *_locked flags so the parser never clobbers a curated value.

    Why a dim table:
        Avoids re-parsing campaign names on every read for grouping/filtering.
        Brand Analytics (Phase 3), profitability roll-ups by brand, and
        portfolio-level analytics all join from this table.
    """
    marketplace      = models.CharField(max_length=8)
    campaign_id      = models.CharField(max_length=64)
    campaign_name    = models.CharField(max_length=256)
    campaign_type    = models.CharField(max_length=4, choices=_AD_TYPE_CHOICES, default='sp')

    portfolio_id     = models.CharField(max_length=64, blank=True)
    portfolio_name   = models.CharField(max_length=128, blank=True)
    brand            = models.CharField(max_length=64, blank=True,
                        help_text='Parsed or manually set. Used for brand-level rollups.')
    product_family   = models.CharField(max_length=64, blank=True,
                        help_text='Parsed product family / line, e.g. "Bath Towel".')
    initials         = models.CharField(max_length=16, blank=True,
                        help_text='Short product code parsed from campaign name (e.g. "BTW").')

    state            = models.CharField(max_length=12, blank=True,
                        help_text='Latest known state — for reference only; not authoritative.')

    first_seen_date  = models.DateField(null=True, blank=True)
    last_seen_date   = models.DateField(null=True, blank=True)

    # Manual override locks — once set, parser will NOT overwrite the value
    brand_locked            = models.BooleanField(default=False)
    product_family_locked   = models.BooleanField(default=False)
    initials_locked         = models.BooleanField(default=False)

    notes            = models.TextField(blank=True)

    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_campaigns'
        unique_together = [['marketplace', 'campaign_id']]
        ordering        = ['marketplace', 'campaign_name']
        indexes = [
            models.Index(fields=['marketplace', 'campaign_type']),
            models.Index(fields=['marketplace', 'brand']),
            models.Index(fields=['marketplace', 'product_family']),
            models.Index(fields=['marketplace', 'portfolio_id']),
        ]

    def __str__(self):
        return f'{self.marketplace.upper()} · {self.campaign_name} [{self.campaign_type}]'


class AdsAdGroupDailySnapshot(models.Model):
    """One row per (marketplace, date, ad_type, campaign, ad_group)."""

    marketplace      = models.CharField(max_length=8)
    date             = models.DateField()
    source_ad_type   = models.CharField(max_length=4, choices=_AD_TYPE_CHOICES)
    campaign_id      = models.CharField(max_length=64)
    ad_group_id      = models.CharField(max_length=64)
    ad_group_name    = models.CharField(max_length=256, blank=True)

    impressions      = models.BigIntegerField(default=0)
    clicks           = models.BigIntegerField(default=0)
    spend            = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orders_7d        = models.IntegerField(default=0)
    sales_7d         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    units_7d         = models.IntegerField(default=0)
    acos             = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    roas             = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    ctr              = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cvr              = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cpc              = models.DecimalField(max_digits=8, decimal_places=4, default=0)

    synced_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ads_adgroup_daily'
        unique_together = [['marketplace', 'date', 'source_ad_type',
                            'campaign_id', 'ad_group_id']]
        ordering        = ['-date', 'marketplace', '-spend']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'campaign_id', '-date']),
        ]


class AdsTargetingDailySnapshot(models.Model):
    """Per-keyword / per-product-target / per-audience daily snapshot."""

    TARGET_TYPES = [
        ('keyword',          'Keyword'),
        ('product_asin',     'Product Target (ASIN)'),
        ('product_category', 'Product Target (Category)'),
        ('audience',         'Audience'),
        ('contextual',       'Contextual'),
        ('auto',             'Auto Target'),
        ('other',            'Other'),
    ]
    MATCH_CHOICES = [
        ('exact',    'Exact'),
        ('phrase',   'Phrase'),
        ('broad',    'Broad'),
        ('targeted', 'Targeted'),
        ('',         '—'),
    ]

    marketplace      = models.CharField(max_length=8)
    date             = models.DateField()
    source_ad_type   = models.CharField(max_length=4, choices=_AD_TYPE_CHOICES)
    campaign_id      = models.CharField(max_length=64)
    ad_group_id      = models.CharField(max_length=64)
    target_id        = models.CharField(max_length=64)
    target_type      = models.CharField(max_length=20, choices=TARGET_TYPES,
                                        default='keyword')
    expression       = models.CharField(max_length=512, blank=True,
                        help_text='Keyword text, ASIN, category ID, or audience expression.')
    match_type       = models.CharField(max_length=12, choices=MATCH_CHOICES,
                                        blank=True, default='')

    impressions      = models.BigIntegerField(default=0)
    clicks           = models.BigIntegerField(default=0)
    spend            = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orders_7d        = models.IntegerField(default=0)
    sales_7d         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    units_7d         = models.IntegerField(default=0)
    acos             = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    roas             = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    ctr              = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cvr              = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cpc              = models.DecimalField(max_digits=8, decimal_places=4, default=0)

    synced_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ads_targeting_daily'
        unique_together = [['marketplace', 'date', 'source_ad_type',
                            'campaign_id', 'ad_group_id', 'target_id']]
        ordering        = ['-date', 'marketplace', '-spend']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'campaign_id', '-date']),
            models.Index(fields=['marketplace', 'target_type', '-date']),
        ]


class AdsSearchTermDailySnapshot(models.Model):
    """
    Customer search-term × keyword/target × campaign daily snapshot.

    This is the largest fact table in the system — at full backfill it can
    exceed 10M rows. Dedup is via `search_term_hash` (SHA1 of lower-cased term)
    so the unique index stays small even when the search_term itself is long.
    """

    marketplace      = models.CharField(max_length=8)
    date             = models.DateField()
    source_ad_type   = models.CharField(max_length=4, choices=_AD_TYPE_CHOICES)
    campaign_id      = models.CharField(max_length=64)
    ad_group_id      = models.CharField(max_length=64)
    target_id        = models.CharField(max_length=64,
                        help_text='The keyword/target the search term matched against.')
    match_type       = models.CharField(max_length=12, blank=True, default='')
    search_term      = models.CharField(max_length=512, db_index=False)
    search_term_hash = models.CharField(max_length=40,
                        help_text='SHA1(lower(search_term)) — for fast cross-campaign joins.')

    impressions      = models.BigIntegerField(default=0)
    clicks           = models.BigIntegerField(default=0)
    spend            = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orders_7d        = models.IntegerField(default=0)
    sales_7d         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    units_7d         = models.IntegerField(default=0)
    acos             = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    roas             = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    ctr              = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cvr              = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cpc              = models.DecimalField(max_digits=8, decimal_places=4, default=0)

    synced_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ads_search_term_daily'
        unique_together = [['marketplace', 'date', 'source_ad_type',
                            'campaign_id', 'ad_group_id', 'target_id',
                            'search_term_hash']]
        ordering        = ['-date', 'marketplace', '-spend']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'campaign_id', '-date']),
            models.Index(fields=['marketplace', 'search_term_hash', '-date']),
            models.Index(fields=['marketplace', '-date', '-spend']),
        ]


class AdsPlacementDailySnapshot(models.Model):
    """
    Per (campaign, placement, date) breakdown for SP and SB.

    SD has no placement concept; ingestion skips SD entirely. Placement values
    are normalised to {top_of_search, rest_of_search, product_pages, other}.
    """
    PLACEMENT_CHOICES = [
        ('top_of_search',   'Top of Search'),
        ('product_pages',   'Product Pages'),
        ('other_on_amazon', 'Other on-Amazon (incl. Rest of Search, Cart, etc.)'),
        ('off_amazon',      'Off Amazon (Partner / Affiliate sites)'),
        # Legacy — Amazon's v3 spCampaigns placement report no longer surfaces
        # this value, but kept for forward-compat in case they revive it.
        ('rest_of_search',  'Rest of Search (legacy)'),
        ('other',           'Other (legacy)'),
    ]

    marketplace      = models.CharField(max_length=8)
    date             = models.DateField()
    source_ad_type   = models.CharField(max_length=4, choices=_AD_TYPE_CHOICES)
    campaign_id      = models.CharField(max_length=64)
    placement        = models.CharField(max_length=20, choices=PLACEMENT_CHOICES)

    impressions      = models.BigIntegerField(default=0)
    clicks           = models.BigIntegerField(default=0)
    spend            = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orders_7d        = models.IntegerField(default=0)
    sales_7d         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    units_7d         = models.IntegerField(default=0)
    acos             = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    roas             = models.DecimalField(max_digits=8, decimal_places=4, default=0)

    synced_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ads_placement_daily'
        unique_together = [['marketplace', 'date', 'source_ad_type',
                            'campaign_id', 'placement']]
        ordering        = ['-date', 'marketplace', '-spend']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'campaign_id', '-date']),
        ]


class AdsAdvertisedProductDailySnapshot(models.Model):
    """
    Per (campaign, advertised ASIN/SKU, date) snapshot.

    THE KEY TABLE for campaign→SKU profit attribution: combined with
    DailySkuSnapshot's per-unit COGS + per-unit FBA fee + referral fee %, it
    lets us compute net P&L for every campaign without estimation.

    The ba_* columns are reserved for Phase 3 Brand Analytics ingestion
    (click_share / conversion_share / purchase_share / search_volume_rank).
    Storing them on this table avoids a join later: BA metrics are
    anchored on ASIN, and this is already the per-ASIN fact table.
    """

    marketplace      = models.CharField(max_length=8)
    date             = models.DateField()
    source_ad_type   = models.CharField(max_length=4, choices=_AD_TYPE_CHOICES)
    campaign_id      = models.CharField(max_length=64)
    ad_group_id      = models.CharField(max_length=64, blank=True, default='')
    asin             = models.CharField(max_length=16)
    advertised_sku   = models.CharField(max_length=64, blank=True, default='',
                        help_text='Amazon-reported SKU; may be empty for SB purchasedProduct.')

    impressions      = models.BigIntegerField(default=0)
    clicks           = models.BigIntegerField(default=0)
    spend            = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    orders_7d        = models.IntegerField(default=0)
    sales_7d         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    units_7d         = models.IntegerField(default=0)

    # Reserved for Phase 3 Brand Analytics ingestion (null until populated).
    ba_click_share        = models.DecimalField(max_digits=6, decimal_places=4,
                                                null=True, blank=True)
    ba_conversion_share   = models.DecimalField(max_digits=6, decimal_places=4,
                                                null=True, blank=True)
    ba_purchase_share     = models.DecimalField(max_digits=6, decimal_places=4,
                                                null=True, blank=True)
    ba_search_volume_rank = models.IntegerField(null=True, blank=True)

    synced_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ads_advertised_product_daily'
        unique_together = [['marketplace', 'date', 'source_ad_type',
                            'campaign_id', 'asin', 'advertised_sku']]
        ordering        = ['-date', 'marketplace', '-spend']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'campaign_id', '-date']),
            models.Index(fields=['marketplace', 'asin', '-date']),
            models.Index(fields=['marketplace', 'advertised_sku', '-date']),
        ]


# ── AGGREGATE CACHES ─────────────────────────────────────────────────────────

class CampaignProfitDaily(models.Model):
    """
    Pre-aggregated daily P&L per (marketplace, date, campaign).

    Computed nightly by `compute_campaign_profit` from
    AdsAdvertisedProductDailySnapshot + DailySkuSnapshot + PPCCampaignSnapshot.
    Hot read path for the Campaign Performance Center and Detail KPI strip.

    attribution_coverage_pct = percentage of the campaign's sales_7d that
    matched to advertised-product rows whose SKU exists in SKUMaster. A low
    coverage value flags campaigns where the profit number relies on fallback
    margin estimates (referral % only, no SKU-specific COGS/FBA).
    """

    marketplace             = models.CharField(max_length=8)
    date                    = models.DateField()
    campaign_id             = models.CharField(max_length=64)
    source_ad_type          = models.CharField(max_length=4, choices=_AD_TYPE_CHOICES)

    # P&L inputs
    spend                   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    ad_revenue              = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    attributed_units        = models.IntegerField(default=0)
    attributed_orders       = models.IntegerField(default=0)
    cogs_attributed         = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    referral_fee_attributed = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fba_fee_attributed      = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    other_fees_attributed   = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Derived (stored for sort/index efficiency)
    contribution_margin     = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gross_profit            = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    margin_pct              = models.DecimalField(max_digits=7,  decimal_places=4, default=0)
    tacos                   = models.DecimalField(max_digits=7,  decimal_places=4, default=0)
    acos                    = models.DecimalField(max_digits=6,  decimal_places=4, default=0)
    roas                    = models.DecimalField(max_digits=8,  decimal_places=4, default=0)

    # Quality / validation
    sku_count_attributed       = models.IntegerField(default=0)
    attribution_coverage_pct   = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                  help_text='0–100; how much of campaign sales matched to advertised-product rows.')

    computed_at             = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_campaign_profit_daily'
        unique_together = [['marketplace', 'date', 'campaign_id']]
        ordering        = ['-date', '-gross_profit']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'campaign_id', '-date']),
            models.Index(fields=['marketplace', '-date', '-gross_profit']),
            models.Index(fields=['marketplace', '-date', '-margin_pct']),
        ]


class CampaignSearchTermSummary(models.Model):
    """
    Per (campaign, date) rollup of the search-term fact table.

    Why: account-wide / 30-day search-term pages would otherwise scan
    ~10M raw rows. The summary keeps account-level KPIs fast and the
    tag-count columns drive the auto-tag filter pills directly.
    """

    marketplace          = models.CharField(max_length=8)
    date                 = models.DateField()
    campaign_id          = models.CharField(max_length=64)
    source_ad_type       = models.CharField(max_length=4, choices=_AD_TYPE_CHOICES)

    distinct_terms       = models.IntegerField(default=0)

    # Tag counts — drive auto-tag filter pills without re-scanning raw table
    high_spend_no_sales  = models.IntegerField(default=0)
    high_ctr_low_cvr     = models.IntegerField(default=0)
    losing_money         = models.IntegerField(default=0)
    scaling_opportunity  = models.IntegerField(default=0)
    high_profit          = models.IntegerField(default=0)

    # Sums for quick rollups
    impressions          = models.BigIntegerField(default=0)
    clicks               = models.BigIntegerField(default=0)
    spend                = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sales_7d             = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    orders_7d            = models.IntegerField(default=0)

    # "Wasted" spend = Σ spend over terms with spend > $threshold AND zero orders
    wasted_spend         = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    computed_at          = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_campaign_search_term_summary'
        unique_together = [['marketplace', 'date', 'campaign_id']]
        ordering        = ['-date', 'marketplace', '-spend']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'campaign_id', '-date']),
        ]


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3 — AMAZON BRAND ANALYTICS
#
# Weekly cadence — Amazon BA reports use Monday-Sunday windows and land
# ~3-5 days after the week ends. The pipeline:
#   1. ingest_brand_analytics submits one SQP / Item Comparison / Market
#      Basket report per (marketplace × week) and persists raw rows.
#   2. Per-row, we already know which asin is OURS via Product.objects — so
#      the "share" computation (click/conversion/purchase share per query)
#      is a Python-side rollup.
#   3. BABrandShareWeekly is the aggregate cache used by the trend charts;
#      raw BASearchQueryWeekly rows feed the per-query drill-downs.
#
# Why not just compute from raw rows on every read?
#   Search query reports for a brand-registered seller commonly run to
#   50-150k rows per week. Aggregating in Python on each chart render is
#   too slow; the per-week brand-level rollup is tiny by comparison.
# ═════════════════════════════════════════════════════════════════════════════


class BASearchQueryWeekly(models.Model):
    """
    One row per (marketplace × week × search_query) — the raw Amazon SQP
    report shape. For each query Amazon returns: search frequency rank,
    impressions, click-share %, cart-add share %, purchase share %, and the
    top-3 clicked / converted / purchased ASINs.

    Field names mirror Amazon's report JSON keys (snake_cased) so the
    ingester can write rows from a near-verbatim dict.
    """

    marketplace                = models.CharField(max_length=8)
    week_start                 = models.DateField(
        help_text="SUNDAY — Amazon's week runs Sunday to Saturday, and the stored data "
                  "confirms it (2026-05-31 is a Sunday, week_end 2026-06-06 a Saturday). "
                  "This field previously claimed Monday. Never derive the boundary from "
                  "isocalendar(); anything assuming an ISO week is off by one day.")
    week_end                   = models.DateField(help_text='Saturday (end of week)')
    asin                       = models.CharField(max_length=16, default='',
        help_text='Amazon now requires SQP reports to be ASIN-scoped — each '
                  '(week × ASIN) yields one report, and each report can list '
                  'many queries. We persist asin so the per-query share rollup '
                  'knows which of our ASINs this row belongs to.')

    search_query               = models.CharField(max_length=512)
    search_query_hash          = models.CharField(max_length=40,
        help_text='SHA1(lower(search_query)) — for the unique key + cross-week joins.')
    search_query_score         = models.IntegerField(default=0,
        help_text='Amazon search frequency rank — lower = more popular.')
    search_query_volume        = models.BigIntegerField(default=0,
        help_text='Total impressions Amazon saw for this query in the week.')

    impressions_total          = models.BigIntegerField(default=0)
    impressions_asin_count     = models.IntegerField(default=0)
    clicks_total               = models.BigIntegerField(default=0)
    cart_adds_total            = models.BigIntegerField(default=0)
    purchases_total            = models.BigIntegerField(default=0)

    # Brand share — Amazon reports these as PERCENTAGES (0-100), not fractions.
    # max_digits=7 decimal_places=4 fits up to 999.9999, covering edge cases
    # where the share is exactly 100.0000 (only our ASIN appeared in the bucket).
    brand_impressions_share    = models.DecimalField(max_digits=7, decimal_places=4, default=0,
                                  help_text='% of all query impressions our ASIN got (0-100).')
    brand_click_share          = models.DecimalField(max_digits=7, decimal_places=4, default=0,
                                  help_text='% of all query clicks our ASIN got (0-100).')
    brand_cart_add_share       = models.DecimalField(max_digits=7, decimal_places=4, default=0,
                                  help_text='% of all query cart-adds our ASIN got (0-100).')
    brand_purchase_share       = models.DecimalField(max_digits=7, decimal_places=4, default=0,
                                  help_text='% of all query purchases our ASIN got (0-100).')

    asin_impression_count      = models.IntegerField(default=0)
    asin_click_count           = models.IntegerField(default=0)
    asin_cart_add_count        = models.IntegerField(default=0)
    asin_purchase_count        = models.IntegerField(default=0)

    # Top-3 click + conversion + purchase rows from the raw report.
    # Stored as JSON to avoid an explosion of nullable columns.
    top_clicked_asins          = models.JSONField(default=list,
        help_text='[{asin, brand?, click_share, title?}, ...] — up to 3 items.')
    top_converted_asins        = models.JSONField(default=list,
        help_text='[{asin, brand?, conversion_share, title?}, ...] — up to 3 items.')
    top_purchased_asins        = models.JSONField(default=list,
        help_text='[{asin, brand?, purchase_share, title?}, ...] — up to 3 items.')

    synced_at                  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ba_search_query_weekly'
        unique_together = [['marketplace', 'week_start', 'asin', 'search_query_hash']]
        ordering        = ['-week_start', 'asin', 'search_query_score']
        indexes = [
            models.Index(fields=['marketplace', '-week_start']),
            models.Index(fields=['marketplace', 'asin', '-week_start']),
            models.Index(fields=['marketplace', 'search_query_hash', '-week_start']),
            models.Index(fields=['marketplace', '-week_start', 'search_query_score']),
        ]


class BAItemComparisonWeekly(models.Model):
    """
    One row per (marketplace × week × our_asin × compared_asin).

    Source: GET_BRAND_ANALYTICS_ITEM_COMPARISON_REPORT.
    Tells us which competitor ASINs Amazon shoppers compared ours to in the week.
    Field semantics:
      compared_asin     — the OTHER product (likely competitor)
      compared_frequency_rank  — Amazon's relative-comparison strength rank
                                   (1 = most-compared)
    """

    marketplace          = models.CharField(max_length=8)
    week_start           = models.DateField()
    week_end             = models.DateField()
    asin                 = models.CharField(max_length=16,
                              help_text='Our ASIN being compared FROM.')
    compared_asin        = models.CharField(max_length=16,
                              help_text='Competitor / alternate ASIN.')
    compared_title       = models.CharField(max_length=512, blank=True)
    compared_frequency_rank = models.IntegerField(default=0)

    synced_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ba_item_comparison_weekly'
        unique_together = [['marketplace', 'week_start', 'asin', 'compared_asin']]
        ordering        = ['-week_start', 'asin', 'compared_frequency_rank']
        indexes = [
            models.Index(fields=['marketplace', '-week_start']),
            models.Index(fields=['marketplace', 'asin', '-week_start']),
            models.Index(fields=['marketplace', 'compared_asin', '-week_start']),
        ]


class BAMarketBasketWeekly(models.Model):
    """
    One row per (marketplace × week × our_asin × co_purchased_asin).

    Source: GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT.
    Tells us which other ASINs were bought together with ours in the same
    order during the week. Bundle / cross-sell signal.
    """

    marketplace                = models.CharField(max_length=8)
    week_start                 = models.DateField()
    week_end                   = models.DateField()
    asin                       = models.CharField(max_length=16,
                                      help_text='Our ASIN being purchased.')
    purchased_asin             = models.CharField(max_length=16,
                                      help_text='Co-purchased ASIN (any brand).')
    purchased_title            = models.CharField(max_length=512, blank=True)
    purchased_frequency_rank   = models.IntegerField(default=0,
                                      help_text='1 = most-co-purchased.')
    combination_pct            = models.DecimalField(max_digits=8, decimal_places=6, default=0,
                                      help_text='Amazon-reported combination probability (0-1).')

    synced_at                  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ba_market_basket_weekly'
        unique_together = [['marketplace', 'week_start', 'asin', 'purchased_asin']]
        ordering        = ['-week_start', 'asin', 'purchased_frequency_rank']
        indexes = [
            models.Index(fields=['marketplace', '-week_start']),
            models.Index(fields=['marketplace', 'asin', '-week_start']),
            models.Index(fields=['marketplace', 'purchased_asin', '-week_start']),
        ]


class BARepeatPurchaseWeekly(models.Model):
    """
    Per (marketplace × week × our_asin) repeat-customer retention stats.

    Source: GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT.
    Brand-level — Amazon returns rows for ALL of our ASINs in a single weekly
    report, so the ingest fires ONE submission per (marketplace × week).
    """

    marketplace                  = models.CharField(max_length=8)
    week_start                   = models.DateField()
    week_end                     = models.DateField()
    asin                         = models.CharField(max_length=16)

    orders                       = models.IntegerField(default=0)
    unique_customers             = models.IntegerField(default=0)
    repeat_customers_pct         = models.DecimalField(max_digits=8, decimal_places=6, default=0,
                                      help_text='% of customers who bought 2+ times (0-1).')
    repeat_purchase_revenue      = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    repeat_purchase_revenue_pct  = models.DecimalField(max_digits=8, decimal_places=6, default=0)

    synced_at                    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ba_repeat_purchase_weekly'
        unique_together = [['marketplace', 'week_start', 'asin']]
        ordering        = ['-week_start', '-orders']
        indexes = [
            models.Index(fields=['marketplace', '-week_start']),
            models.Index(fields=['marketplace', 'asin', '-week_start']),
        ]


class BABrandShareWeekly(models.Model):
    """
    Per (marketplace × week) brand-level rollup used by the share trend charts.

    Built by `compute_ba_brand_share` from BASearchQueryWeekly. Stores both
    raw and volume-weighted aggregates so the dashboard can show either
    "how is our brand doing across all queries" or "how are we doing on
    queries that matter (heavily weighted by search volume)."
    """

    marketplace                = models.CharField(max_length=8)
    week_start                 = models.DateField()
    week_end                   = models.DateField()
    brand                      = models.CharField(max_length=64,
        help_text='Brand name as stored in Product.brand — same casing.')

    # Unweighted (simple average across queries where we appear in top-3)
    avg_click_share            = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    avg_conversion_share       = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    avg_purchase_share         = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    queries_we_appear_in       = models.IntegerField(default=0)
    queries_we_dominate        = models.IntegerField(default=0,
        help_text='Queries where we hold the #1 click slot.')

    # Volume-weighted (each query weighted by its search volume)
    weighted_click_share       = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    weighted_purchase_share    = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    total_query_volume         = models.BigIntegerField(default=0)

    computed_at                = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ba_brand_share_weekly'
        unique_together = [['marketplace', 'week_start', 'brand']]
        ordering        = ['-week_start', 'marketplace', 'brand']
        indexes = [
            models.Index(fields=['marketplace', '-week_start']),
            models.Index(fields=['marketplace', 'brand', '-week_start']),
        ]


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 4 — AI INSIGHTS
#
# AIRecommendation = persisted, ranked, actionable items generated by Claude
# from the structured briefing data (apps/dashboard/ai_insights.py).
#
# Why persist instead of regenerating on every page-view?
#   1. Claude calls cost money + latency — daily generation is enough
#   2. The user needs a stable workspace to acknowledge/dismiss/snooze items
#      across sessions
#   3. Audit trail: we keep the raw AI response next to each row so a future
#      analyst can see WHY the recommendation was made
# ═════════════════════════════════════════════════════════════════════════════

class AIRecommendation(models.Model):
    SEVERITY = [
        ('critical',    'Critical — act today'),
        ('warning',     'Warning — act this week'),
        ('opportunity', 'Opportunity — scale candidate'),
        ('info',        'Info'),
    ]
    STATUS = [
        ('new',           'New'),
        ('acknowledged',  'Acknowledged'),
        ('done',          'Done'),
        ('snoozed',       'Snoozed'),
        ('dismissed',     'Dismissed'),
    ]
    SCOPE_TYPES = [
        ('campaign',     'Campaign'),
        ('sku',          'SKU / product'),
        ('search_term',  'Search term'),
        ('placement',    'Placement'),
        ('brand',        'Brand-level'),
        ('account',      'Account-level'),
        ('inventory',    'Inventory'),
        ('other',        'Other'),
    ]
    CATEGORIES = [
        ('ppc_scale',         'PPC: scale budget'),
        ('ppc_cut',           'PPC: cut budget'),
        ('ppc_negate',        'PPC: negate term'),
        ('ppc_bid',           'PPC: adjust bid'),
        ('sku_scale',         'SKU: push harder'),
        ('sku_pause',         'SKU: pause / deprioritise'),
        ('margin_fix',        'Margin: fix margin compression'),
        ('inventory',         'Inventory action'),
        ('listing',           'Listing / PDP optimisation'),
        ('cross_sell',        'Cross-sell / bundle'),
        ('competitive',       'Competitive response'),
        ('other',             'Other'),
    ]

    marketplace          = models.CharField(max_length=8)
    generated_at         = models.DateTimeField()
    reference_date       = models.DateField(
        help_text='The "yesterday" anchor the AI was looking at.')

    # Stable hash of (scope_type + scope_id + headline) — used so re-runs
    # update the same row rather than creating duplicates.
    recommendation_id    = models.CharField(max_length=40, db_index=True)

    severity             = models.CharField(max_length=12, choices=SEVERITY,
                                              default='info')
    category             = models.CharField(max_length=20, choices=CATEGORIES,
                                              default='other')
    scope_type           = models.CharField(max_length=16, choices=SCOPE_TYPES,
                                              default='account')
    scope_id             = models.CharField(max_length=64, blank=True,
        help_text='campaign_id / sku / search_term — blank for account-level.')
    scope_name           = models.CharField(max_length=256, blank=True,
        help_text='Human-readable label to surface in the UI.')

    headline             = models.CharField(max_length=256,
        help_text='The action in one short sentence — what to do.')
    evidence             = models.TextField(blank=True,
        help_text='The data that supports the recommendation (numbers, deltas).')
    suggested_action     = models.TextField(blank=True,
        help_text='Concrete next step — what the user should change.')
    projected_impact     = models.CharField(max_length=128, blank=True,
        help_text='Estimated $ impact / week, or a qualitative band.')

    rank_score           = models.DecimalField(max_digits=6, decimal_places=2, default=0,
        help_text='Compound score — severity × impact × confidence. Used for sort.')
    confidence           = models.DecimalField(max_digits=3, decimal_places=2, default=0,
        help_text='0.00 (low) to 1.00 (high).')

    status               = models.CharField(max_length=14, choices=STATUS,
                                              default='new', db_index=True)
    snoozed_until        = models.DateField(null=True, blank=True)
    acknowledged_by      = models.ForeignKey(settings.AUTH_USER_MODEL,
                                              on_delete=models.SET_NULL,
                                              null=True, blank=True,
                                              related_name='ai_recs_acknowledged')
    acknowledged_at      = models.DateTimeField(null=True, blank=True)
    user_notes           = models.TextField(blank=True,
        help_text='Optional user comment when marking done/dismissed.')

    ai_model             = models.CharField(max_length=64, blank=True)
    raw_response         = models.JSONField(default=dict, blank=True,
        help_text='Full structured JSON the AI returned, for debugging.')

    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_ai_recommendations'
        unique_together = [['marketplace', 'recommendation_id']]
        ordering        = ['-rank_score', '-generated_at']
        indexes = [
            models.Index(fields=['marketplace', 'status', '-rank_score']),
            models.Index(fields=['marketplace', '-generated_at']),
            models.Index(fields=['marketplace', 'scope_type', 'scope_id']),
        ]


# ── SETTLEMENT REPORTS / FBA FEE DRIFT ────────────────────────────────────────
class SettlementReport(models.Model):
    """
    Audit row for each SP-API settlement report we've ingested.

    Settlement reports are auto-generated by Amazon on a ~14-day pay cycle —
    we don't request them, we list `GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2`
    reports with processingStatuses=DONE and download anything new.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('ok',      'Ok'),
        ('failed',  'Failed'),
    ]

    marketplace      = models.CharField(max_length=8, db_index=True)
    report_id        = models.CharField(max_length=64,
                                         help_text="Amazon's reportId")
    document_id      = models.CharField(max_length=128, blank=True)

    start_date       = models.DateField(null=True, blank=True,
                                         help_text='Settlement period start (UTC)')
    end_date         = models.DateField(null=True, blank=True,
                                         help_text='Settlement period end (UTC)')

    status           = models.CharField(max_length=10, choices=STATUS_CHOICES,
                                         default='pending')
    rows_processed   = models.IntegerField(default=0)
    fee_rows         = models.IntegerField(default=0,
        help_text='Subset that produced FBA per-unit fulfillment fee rows.')
    error_message    = models.TextField(blank=True)
    synced_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_settlement_reports'
        unique_together = [['marketplace', 'report_id']]
        ordering        = ['-end_date', '-synced_at']
        indexes = [
            models.Index(fields=['marketplace', '-end_date']),
        ]

    def __str__(self):
        return (f'{self.marketplace.upper()} settlement {self.report_id} '
                 f'[{self.start_date}..{self.end_date}] {self.status}')


class SkuFeeActual(models.Model):
    """
    Per-day per-SKU actual FBA fulfillment fee, extracted from settlement
    reports. One row per (marketplace, sku, posted_date).

    fee_per_unit = fba_fee_total / units (computed at write time so reads
    don't need a divide). Drift calculator reads these and compares to
    FBAFeeRate (the uploaded value) for the same date.
    """
    marketplace        = models.CharField(max_length=8, db_index=True)
    sku                = models.CharField(max_length=64, db_index=True)
    date               = models.DateField(db_index=True,
        help_text='posted-date from the settlement report (when the '
                  'transaction settled, not when the order was placed).')

    units              = models.IntegerField(default=0)
    fba_fee_total      = models.DecimalField(max_digits=14, decimal_places=4,
                                              default=0,
        help_text='Sum of |amount| where amount-description == '
                  'FBAPerUnitFulfillmentFee for this SKU on this date.')
    fee_per_unit       = models.DecimalField(max_digits=10, decimal_places=4,
                                              default=0,
        help_text='= fba_fee_total / units. Pre-computed for fast reads.')

    source_settlement  = models.ForeignKey(
        SettlementReport, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sku_fee_actuals',
        help_text='The settlement report this row was extracted from.',
    )

    created_at         = models.DateTimeField(auto_now_add=True)
    updated_at         = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_sku_fee_actuals'
        unique_together = [['marketplace', 'sku', 'date']]
        ordering        = ['marketplace', 'sku', '-date']
        indexes = [
            models.Index(fields=['marketplace', '-date']),
            models.Index(fields=['marketplace', 'sku', '-date']),
        ]

    def __str__(self):
        return (f'{self.marketplace.upper()} {self.sku} {self.date} '
                 f'${self.fee_per_unit}/u × {self.units}')


# ── MANAGEMENT P&L ────────────────────────────────────────────────────────────
class SettlementLineActual(models.Model):
    """
    Per-region per-month aggregated settlement amounts, bucketed by P&L line
    key. One row per (marketplace, month, line_key). Populated by the
    settlement parser; read by the P&L engine to fill the 🟢 auto lines.

    Amounts are in the marketplace's NATIVE settlement currency (USA=USD,
    UK=GBP, …). Stored as positive magnitudes; the line's sign in pnl_lines
    decides add/subtract.
    """
    marketplace   = models.CharField(max_length=8, db_index=True)
    month         = models.DateField(help_text='First day of month (posted-date basis)')
    line_key      = models.CharField(max_length=48,
        help_text="pnl_lines key, e.g. 'gross_sales','commission','fba_fee'")

    amount        = models.DecimalField(max_digits=16, decimal_places=2, default=0,
        help_text='Native-currency magnitude for this line in this month.')
    units         = models.IntegerField(default=0,
        help_text='Unit count where meaningful (gross_sales→sold, returns→returned).')

    currency      = models.CharField(max_length=4, default='USD')
    # per-head composition of this line, e.g. {'Service Fee — Cost of
    # Advertising': -177348.52, ...} — populated by the unified importer
    breakdown   = models.JSONField(default=dict, blank=True)
    source_note   = models.CharField(max_length=64, blank=True,
        help_text="e.g. 'settlement' or 'operational_fallback'.")
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_settlement_line_actuals'
        unique_together = [['marketplace', 'month', 'line_key']]
        ordering        = ['marketplace', '-month', 'line_key']
        indexes = [models.Index(fields=['marketplace', '-month'])]

    def __str__(self):
        return f'{self.marketplace.upper()} {self.month:%Y-%m} {self.line_key}={self.amount}'


class MonthlyPnLEntry(models.Model):
    """
    A single manual P&L line value for one region+month+channel.

    channel: 'amazon' covers the auto-fed Amazon column's manual overrides
             and all the manual overhead (rent, HR, opex, tax …) which live
             under the region. 'retail' covers the Walmart/retail column,
             which is entirely manual.

    amount is in the region's NATIVE currency (regional overhead is regional,
    per the client's instruction). The consolidator converts to USD via
    MonthlyFXRate.
    """
    CHANNELS = [('amazon', 'Amazon'), ('retail', 'Retail / Walmart')]

    marketplace = models.CharField(max_length=8, db_index=True)
    month       = models.DateField(help_text='First day of month')
    channel     = models.CharField(max_length=8, choices=CHANNELS, default='amazon')
    line_key    = models.CharField(max_length=48,
        help_text='pnl_lines key for a source=manual line.')

    amount      = models.DecimalField(max_digits=16, decimal_places=2, default=0,
        help_text='Native-currency amount (signed — negatives allowed for credits).')
    note        = models.CharField(max_length=512, blank=True)
    invoice     = models.FileField(upload_to='pnl_invoices/%Y/%m/',
                                    null=True, blank=True,
        help_text='Optional supporting invoice/receipt for records.')

    updated_by  = models.ForeignKey(settings.AUTH_USER_MODEL,
                                     on_delete=models.SET_NULL, null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_monthly_pnl_entries'
        unique_together = [['marketplace', 'month', 'channel', 'line_key']]
        ordering        = ['marketplace', '-month', 'channel', 'line_key']
        indexes = [models.Index(fields=['marketplace', '-month', 'channel'])]

    def __str__(self):
        return (f'{self.marketplace.upper()} {self.month:%Y-%m} '
                 f'{self.channel}/{self.line_key}={self.amount}')


class MonthlyFXRate(models.Model):
    """
    Month-end FX rate used to convert a region's native currency into USD for
    the global consolidated P&L. Entered manually so the books reconcile to
    whatever rate the accountant used at close.

    rate_to_usd: multiply a native-currency amount by this to get USD.
                 e.g. GBP→USD ≈ 1.27, AED→USD ≈ 0.27.
    """
    month        = models.DateField(help_text='First day of month')
    currency     = models.CharField(max_length=4, help_text='ISO code, e.g. GBP, EUR, AED')
    rate_to_usd  = models.DecimalField(max_digits=12, decimal_places=6,
        help_text='Multiply native amount by this to get USD.')

    updated_by   = models.ForeignKey(settings.AUTH_USER_MODEL,
                                      on_delete=models.SET_NULL, null=True, blank=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_monthly_fx_rates'
        unique_together = [['month', 'currency']]
        ordering        = ['-month', 'currency']

    def __str__(self):
        return f'{self.currency}→USD {self.month:%Y-%m} = {self.rate_to_usd}'


class AmazonPayout(models.Model):
    """
    One Amazon disbursement (a 'Transfer' row in the Unified Transaction
    report) — money Amazon sent to the bank account. Feeds the Cash Flow page.
    Amount stored as positive magnitude (money OUT of the Amazon balance,
    INTO the bank).
    """
    marketplace  = models.CharField(max_length=8, db_index=True)
    month        = models.DateField(help_text='First day of the report month')
    payout_date  = models.DateField()
    amount       = models.DecimalField(max_digits=14, decimal_places=2)
    description  = models.CharField(max_length=128, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ix_amazon_payouts'
        ordering = ['marketplace', '-payout_date']
        indexes = [models.Index(fields=['marketplace', 'month'])]

    def __str__(self):
        return f'{self.marketplace.upper()} {self.payout_date} ${self.amount:,.2f}'


class McfOrder(models.Model):
    """
    A Multi-Channel Fulfillment (MCF) order — created by the seller to ship
    FBA inventory for an off-Amazon sale. Synced from the SP-API Fulfillment
    Outbound API; the main purpose is surfacing carrier tracking numbers.
    packages: [{carrier, tracking, ship_date, eta, package_number}]
    items:    [{sku, qty}]
    """
    marketplace          = models.CharField(max_length=8, db_index=True)
    seller_order_id      = models.CharField(max_length=64)
    displayable_order_id = models.CharField(max_length=64, blank=True)
    status               = models.CharField(max_length=32, blank=True, db_index=True)
    received_date        = models.DateTimeField(null=True, blank=True)
    recipient_name       = models.CharField(max_length=128, blank=True)
    city                 = models.CharField(max_length=64, blank=True)
    state                = models.CharField(max_length=32, blank=True)
    units                = models.IntegerField(default=0)
    items                = models.JSONField(default=list, blank=True)
    packages             = models.JSONField(default=list, blank=True)
    synced_at            = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_mcf_orders'
        unique_together = [['marketplace', 'seller_order_id']]
        ordering        = ['-received_date']
        indexes = [models.Index(fields=['marketplace', '-received_date'])]

    def __str__(self):
        return f'{self.marketplace.upper()} MCF {self.seller_order_id} [{self.status}]'


class UnifiedSkuUnits(models.Model):
    """
    Per-SKU order/refund unit counts from a Unified Transaction upload.
    Persisted so COGS can be RECALCULATED for a month after new COGS rates
    are uploaded, without re-parsing the original CSV.
    """
    marketplace  = models.CharField(max_length=8, db_index=True)
    month        = models.DateField(help_text='First day of the report month')
    sku          = models.CharField(max_length=64)
    order_units  = models.IntegerField(default=0)
    refund_units = models.IntegerField(default=0)

    class Meta:
        db_table        = 'ix_unified_sku_units'
        unique_together = [['marketplace', 'month', 'sku']]
        indexes = [models.Index(fields=['marketplace', 'month'])]

    def __str__(self):
        return (f'{self.marketplace.upper()} {self.month:%Y-%m} {self.sku} '
                 f'+{self.order_units}/-{self.refund_units}')


class ManualPnLUpload(models.Model):
    """Audit row for each Excel P&L import (mirrors AdsManualHourlyUpload)."""
    STATUS_CHOICES = [('ok', 'Ok'), ('failed', 'Failed')]

    marketplace       = models.CharField(max_length=8, db_index=True)
    month             = models.DateField(null=True, blank=True)
    original_filename = models.CharField(max_length=256, blank=True)
    rows_imported     = models.IntegerField(default=0)
    lines_matched     = models.IntegerField(default=0)
    lines_unmatched   = models.IntegerField(default=0)
    status            = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ok')
    error_message     = models.TextField(blank=True)
    uploaded_by       = models.ForeignKey(settings.AUTH_USER_MODEL,
                                           on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ix_manual_pnl_uploads'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.marketplace.upper()} {self.month} {self.original_filename} [{self.status}]'


# ═════════════════════════════════════════════════════════════════════════════
# PHASE 5 — SEARCH INTELLIGENCE CENTER
#
# Design: plans/search-intelligence-center.md (v2).
#
# The Center is a REPORT RUN, not a live page: the user picks
# (product group × marketplace × date range), Pulse computes a complete JSON
# payload, and the UI renders that payload. Rationale:
#   1. The pipeline joins a multi-million-row fact table with weekly Brand
#      Analytics data, a profit proxy and a scoring model — seconds of work.
#      Acceptable per explicit run, not per page-load.
#   2. Stored runs give history, so the diff engine ("what changed since last
#      time?") and outcome tracking become possible at all.
#   3. Opportunities carry a STABLE KEY across runs, so "Capture face towels
#      (USA)" is the same row week after week and can be tracked to outcome.
#
# Phase 1 implements: ProductGroup · SearchTermTag · StiReportRun ·
# StiOpportunity · StiOpportunitySnapshot.
# ═════════════════════════════════════════════════════════════════════════════

class ProductGroup(models.Model):
    """
    The reporting scope for a Search Intelligence run.

    THE CATALOG DEFINES THE GROUP. Membership is `Product.category`, plus the
    manual ASIN overrides. The advertising that belongs to a group is derived
    from which ad groups advertised those ASINs — see
    `apps.dashboard.sti.mapping`. Campaign naming never scopes anything.

    That is not only cleaner, it is more correct. Scoping by campaign-name
    initials covered none of UAE or KSA (the campaign dimension has no rows for
    them) and undercovered the UK, where the dimension held 74 campaigns
    against 315 that actually advertised. The catalog route attributes
    95.5–99.9% of search-term spend in all four marketplaces.

    Groups are marketplace-agnostic — SKU logic is region-blind in Pulse.
    Membership resolves per-marketplace at query time via Product.marketplace.
    """

    name          = models.CharField(max_length=64, unique=True)
    slug          = models.SlugField(max_length=64, unique=True)

    initials      = models.JSONField(default=list, blank=True,
                     help_text='DISPLAY ONLY — campaign initials commonly used for this '
                               'group, e.g. ["BTH"]. Recorded so a report is easier to '
                               'read against Seller Central. Never used to scope a query; '
                               'scoping is by Product.category.')
    categories    = models.JSONField(default=list, blank=True,
                     help_text='Product.category values that define this group. '
                               'THE definition — routes both ASINs and ad spend in.')
    extra_asins   = models.JSONField(default=list, blank=True,
                     help_text='Manual additions — ASINs the category rule misses.')
    excluded_asins= models.JSONField(default=list, blank=True,
                     help_text='Manual subtractions — applied after every other rule.')

    lexicon_key   = models.CharField(max_length=32, default='towel',
                     help_text='Which intent lexicon applies (apps.dashboard.sti.lexicon).')
    active        = models.BooleanField(default=True)

    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_sti_product_group'
        ordering = ['name']

    def __str__(self):
        return self.name


class SearchTermTag(models.Model):
    """
    Persisted multi-dimensional classification of one search term.

    Why persist rather than regex on the fly: the fact table can carry >100k
    distinct terms per marketplace. Classifying inline would re-run the whole
    lexicon on every report. Instead a term is classified once and reused;
    a lexicon version bump triggers reclassification of stale rows only.

    `tags` shape (see apps.dashboard.sti.taxonomy):
        {product_type: str, attributes: [str], room_usage: str|None,
         brand_class: str, is_asin: bool}
    """

    marketplace      = models.CharField(max_length=8)
    search_term_hash = models.CharField(max_length=40,
                        help_text='SHA1(lower(term)) — same recipe as '
                                  'AdsSearchTermDailySnapshot.search_term_hash, so the '
                                  'two join without a text comparison.')
    search_term      = models.CharField(max_length=512)

    tags             = models.JSONField(default=dict)
    lexicon_version  = models.IntegerField(default=1,
                        help_text='Bumping LEXICON_VERSION invalidates every row.')
    classified_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'ix_sti_search_term_tag'
        unique_together = [['marketplace', 'search_term_hash']]
        indexes = [
            models.Index(fields=['marketplace', 'lexicon_version']),
        ]

    def __str__(self):
        return f'{self.marketplace.upper()} {self.search_term[:40]}'


class StiReportRun(models.Model):
    """
    One generation of the Search Intelligence Center.

    `payload` holds every computed section. `schema_version` lets old runs stay
    renderable when later phases add sections — the template renders known keys
    and ignores the rest, so no migration of stored payloads is ever needed.
    """

    STATUS = [
        ('running',  'Running'),
        ('complete', 'Complete'),
        ('failed',   'Failed'),
    ]

    product_group   = models.ForeignKey(ProductGroup, on_delete=models.CASCADE,
                                        related_name='runs')
    marketplace     = models.CharField(max_length=8)

    # The report runs on Amazon's own reporting grid — Sunday-start weeks, or
    # calendar months — rather than a rolling range. A named period is fixed
    # forever, which is what makes "we acted in Week 30; did Week 31 improve?"
    # answerable at all. A rolling window resolves differently every day, so two
    # runs never cover the same days and no comparison between them is valid.
    period_type     = models.CharField(max_length=8, default='weekly',
                       help_text='weekly | monthly — see apps.dashboard.sti.periods')
    period_key      = models.CharField(max_length=16, default='', db_index=True,
                       help_text="'2026-W31' or '2026-07'. Amazon's week numbering, "
                                 "NOT ISO — Amazon's Week 31 of 2026 is ISO week 30.")
    date_from       = models.DateField()
    date_to         = models.DateField()

    status          = models.CharField(max_length=10, choices=STATUS, default='running')
    schema_version  = models.IntegerField(default=1)
    payload         = models.JSONField(default=dict, blank=True)
    error           = models.TextField(blank=True, default='')

    duration_ms     = models.IntegerField(default=0)
    generated_at    = models.DateTimeField(auto_now_add=True)
    generated_by    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name='sti_runs')

    class Meta:
        db_table = 'ix_sti_report_run'
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['product_group', 'marketplace', '-generated_at']),
        ]

    def __str__(self):
        return (f'{self.product_group.name} · {self.marketplace.upper()} · '
                f'{self.date_from}→{self.date_to} [{self.status}]')


class StiOpportunity(models.Model):
    """
    A business case, not a task — the product of the whole Center.

    IDENTITY IS THE POINT. `key` is a stable hash of
    (type · group · marketplace · subject), so the same real-world opportunity
    keeps one row across runs. Without that, "what changed since last report?"
    and outcome measurement are both impossible.

    Money fields are per MONTH in the marketplace's own currency, and profit is
    contribution margin measured on revenue EX-VAT (the Pulse margin invariant).
    """

    TYPES = [
        ('capture_share', 'Capture share'),
        ('product_gap',   'Product gap'),
        ('organic_push',  'Organic push'),
        ('listing_fix',   'Listing fix'),
        ('scale_ppc',     'Scale PPC'),
        ('defend',        'Defend (waste)'),
        ('conquest',      'Conquest'),      # Phase 2 — needs competitor detectors
    ]
    STATUS = [
        ('open',        'Open'),
        ('in_progress', 'In progress'),
        ('done',        'Done'),
        ('dismissed',   'Dismissed'),
        ('expired',     'Expired'),
    ]
    CONFIDENCE = [('high', 'High'), ('medium', 'Medium'), ('low', 'Low')]

    key             = models.CharField(max_length=40, unique=True,
                       help_text='Stable across runs — see class docstring.')
    product_group   = models.ForeignKey(ProductGroup, on_delete=models.CASCADE,
                                        related_name='opportunities')
    marketplace     = models.CharField(max_length=8)
    opp_type        = models.CharField(max_length=16, choices=TYPES)

    title           = models.CharField(max_length=200)
    why             = models.TextField(blank=True, default='')
    subject         = models.CharField(max_length=512, blank=True, default='',
                       help_text='The term / node / SKU this is about.')

    # ── The score and its three factors (design §5: dollars × probability) ──
    score           = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                       help_text='Expected contribution margin per month. THE ranking key.')
    headroom_value  = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                       help_text='Attainable incremental revenue per month, before probability.')
    win_probability = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    margin_factor   = models.DecimalField(max_digits=5, decimal_places=4, default=0)

    difficulty      = models.IntegerField(default=3, help_text='1 (PPC-only) … 5 (new product)')
    confidence      = models.CharField(max_length=6, choices=CONFIDENCE, default='low')
    blocked_reason  = models.CharField(max_length=120, blank=True, default='',
                       help_text='Non-empty when a hard gate fired (stockout, no margin).')

    evidence        = models.JSONField(default=dict, blank=True,
                       help_text='Every number cited in the UI, so the card is auditable.')
    required_actions= models.JSONField(default=list, blank=True)
    dependencies    = models.JSONField(default=list, blank=True)
    timeline        = models.CharField(max_length=32, blank=True, default='')

    status          = models.CharField(max_length=12, choices=STATUS, default='open')
    status_note     = models.TextField(blank=True, default='')
    acted_period_key= models.CharField(max_length=16, blank=True, default='',
                       help_text='The reporting period during which this was marked done — '
                                 'the anchor for measuring whether it worked.')

    first_seen_run  = models.ForeignKey(StiReportRun, on_delete=models.SET_NULL, null=True,
                                        blank=True, related_name='opportunities_first_seen')
    last_seen_run   = models.ForeignKey(StiReportRun, on_delete=models.SET_NULL, null=True,
                                        blank=True, related_name='opportunities_last_seen')
    runs_unseen     = models.IntegerField(default=0,
                       help_text='Consecutive runs where the generator no longer emitted it.')

    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_sti_opportunity'
        ordering = ['-score']
        indexes = [
            models.Index(fields=['product_group', 'marketplace', 'status', '-score']),
            models.Index(fields=['opp_type', '-score']),
        ]

    def __str__(self):
        return f'[{self.opp_type}] {self.title}'


class StiOpportunitySnapshot(models.Model):
    """
    One opportunity's numbers as of one run — the raw material for the
    "what changed?" diff and, later, for outcome measurement.
    """

    opportunity   = models.ForeignKey(StiOpportunity, on_delete=models.CASCADE,
                                      related_name='snapshots')
    run           = models.ForeignKey(StiReportRun, on_delete=models.CASCADE,
                                      related_name='opportunity_snapshots')

    period_key    = models.CharField(max_length=16, default='', db_index=True,
                     help_text='Copied from the run. Makes a snapshot series comparable '
                               'period-to-period rather than run-to-run.')
    score         = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    headroom_value= models.DecimalField(max_digits=14, decimal_places=2, default=0)
    current_share = models.DecimalField(max_digits=7, decimal_places=4, default=0,
                     help_text='Our share of the market pool, 0-100.')
    difficulty    = models.IntegerField(default=3)
    confidence    = models.CharField(max_length=6, default='low')
    evidence      = models.JSONField(default=dict, blank=True)

    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ix_sti_opportunity_snapshot'
        unique_together = [['opportunity', 'run']]
        ordering        = ['-created_at']

    def __str__(self):
        return f'{self.opportunity.title[:40]} @ run {self.run_id}'


class CampaignBudgetUsageDaily(models.Model):
    """Amazon Marketing Stream budget-usage, rolled to one row per campaign/day.

    The stream delivers `percentage_of_budget_used` + `campaign_budget_amount`
    events through the day; we keep the MAX usage % seen (the peak — a day that
    ever reached 100% ran out of budget) and the latest budget value. This makes
    "how many days did this campaign run out of budget" an exact count instead
    of the hourly-spend estimate the Budget Pacing tab falls back to.
    """
    marketplace  = models.CharField(max_length=8)
    date         = models.DateField()
    campaign_id  = models.CharField(max_length=64)
    budget_value = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                       help_text='Daily budget amount for the day.')
    usage_pct    = models.DecimalField(max_digits=7, decimal_places=2, default=0,
                                       help_text='Peak % of daily budget consumed that day.')
    events       = models.IntegerField(default=0,
                                       help_text='Budget-usage events folded into this row.')
    updated_at   = models.DateTimeField(auto_now=True)

    OUT_OF_BUDGET_PCT = 100.0

    class Meta:
        db_table        = 'ix_campaign_budget_usage_daily'
        unique_together = [['marketplace', 'date', 'campaign_id']]
        ordering        = ['-date', 'marketplace', '-usage_pct']
        indexes = [models.Index(fields=['marketplace', '-date'])]

    def __str__(self):
        return f'{self.marketplace}/{self.campaign_id} {self.date} — {self.usage_pct}%'

    @property
    def out_of_budget(self) -> bool:
        return float(self.usage_pct) >= self.OUT_OF_BUDGET_PCT


class AdActionRequest(models.Model):
    """
    P4 — a PROPOSED advertising change awaiting human review.

    This is an approval queue, not an automation engine. A row is created by a
    person acting on an opportunity, moves only through explicit human steps,
    and executes only after an explicit approval. Nothing in Pulse creates,
    approves or executes one of these on a schedule or from an AI agent.

    Every column exists to answer one audit question: what was proposed, on
    what evidence, from which value to which value, who approved it, what
    Amazon actually held at execution time, and what Amazon replied.
    """
    ENTITY_TYPES = [('campaign', 'Campaign')]
    # Only budget is offered: it is the sole entity whose CURRENT value Pulse
    # actually stores (PPCCampaignSnapshot.daily_budget / the budget-usage
    # stream). Target bids are deliberately absent — no bid value is stored
    # anywhere, so a "current → proposed" claim could not be made honestly.
    ACTION_TYPES = [('campaign_budget', 'Campaign daily budget')]
    STATUS = [
        ('proposed',    'Proposed — awaiting review'),
        ('approved',    'Approved — cleared to execute'),
        ('executing',   'Executing'),
        ('executed',    'Executed'),
        ('failed',      'Failed'),
        ('rejected',    'Rejected'),
        ('cancelled',   'Cancelled'),
        ('stale',       'Stale — underlying value moved, re-review required'),
        ('unavailable', 'Execution unavailable — integration is read-only'),
    ]
    OPEN_STATES = ('proposed', 'approved', 'executing')

    # ── identity / idempotency ─────────────────────────────────────────────
    action_id     = models.CharField(max_length=40, unique=True,
                     help_text='Stable id — the idempotency key for execution.')
    marketplace   = models.CharField(max_length=8)
    entity_type   = models.CharField(max_length=16, choices=ENTITY_TYPES,
                                     default='campaign')
    entity_id     = models.CharField(max_length=64)
    entity_name   = models.CharField(max_length=256, blank=True)
    action_type   = models.CharField(max_length=24, choices=ACTION_TYPES)

    # ── provenance: which opportunity + evidence produced this ─────────────
    opportunity_key = models.CharField(max_length=128, blank=True)
    reason          = models.TextField(blank=True)
    evidence        = models.JSONField(default=list, blank=True,
                       help_text='The numbers cited when this was proposed.')
    confidence      = models.CharField(max_length=16, blank=True)
    from_sku        = models.CharField(max_length=64, blank=True,
                       help_text='The SKU investigation this came from, if any.')

    # ── the change ─────────────────────────────────────────────────────────
    current_value  = models.DecimalField(max_digits=12, decimal_places=2,
                      help_text='Value observed when the action was proposed.')
    proposed_value = models.DecimalField(max_digits=12, decimal_places=2)
    value_before   = models.DecimalField(max_digits=12, decimal_places=2,
                      null=True, blank=True,
                      help_text='Value read back immediately before executing.')
    value_after    = models.DecimalField(max_digits=12, decimal_places=2,
                      null=True, blank=True)

    # ── data-freshness provenance (staleness gate) ─────────────────────────
    data_period_start = models.DateField(null=True, blank=True)
    data_period_end   = models.DateField(null=True, blank=True)

    # ── lifecycle ──────────────────────────────────────────────────────────
    status         = models.CharField(max_length=12, choices=STATUS,
                                      default='proposed', db_index=True)
    proposed_at    = models.DateTimeField(auto_now_add=True)
    proposed_by    = models.ForeignKey(settings.AUTH_USER_MODEL,
                      on_delete=models.SET_NULL, null=True, blank=True,
                      related_name='ad_actions_proposed')
    approved_at    = models.DateTimeField(null=True, blank=True)
    approved_by    = models.ForeignKey(settings.AUTH_USER_MODEL,
                      on_delete=models.SET_NULL, null=True, blank=True,
                      related_name='ad_actions_approved')
    executed_at    = models.DateTimeField(null=True, blank=True)

    # ── what Amazon said ───────────────────────────────────────────────────
    amazon_status   = models.CharField(max_length=24, blank=True)
    amazon_response = models.TextField(blank=True)
    failure_reason  = models.TextField(blank=True)
    dry_run         = models.BooleanField(default=False,
                       help_text='True when validated through the pipeline '
                                 'without contacting Amazon.')
    note            = models.TextField(blank=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ix_ad_action_request'
        ordering = ['-proposed_at']
        indexes = [
            models.Index(fields=['marketplace', 'status', '-proposed_at']),
            models.Index(fields=['entity_type', 'entity_id', '-proposed_at']),
        ]

    def __str__(self):
        return (f'{self.action_type} {self.entity_id}: '
                f'{self.current_value} → {self.proposed_value} [{self.status}]')

    @property
    def change_pct(self):
        cur = float(self.current_value or 0)
        return ((float(self.proposed_value) - cur) / cur * 100) if cur else None

    @property
    def is_open(self):
        return self.status in self.OPEN_STATES
