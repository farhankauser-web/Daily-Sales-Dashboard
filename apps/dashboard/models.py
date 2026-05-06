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

    # Derived (computed on save from COGS)
    tacos           = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    gross_margin    = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gm_pct          = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    contribution_margin = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cm_pct          = models.DecimalField(max_digits=6, decimal_places=4, default=0)

    synced_at   = models.DateTimeField(auto_now=True)

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
