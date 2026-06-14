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
    week_start                 = models.DateField(help_text='Monday (start of week)')
    week_end                   = models.DateField(help_text='Sunday (end of week)')
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
