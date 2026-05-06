"""
apps/dashboard/models_inventory.py
Append these classes to apps/dashboard/models.py after DailyMetric.
(Kept separate here for clarity — import them in models.py)
"""

# ── INVENTORY SNAPSHOT ────────────────────────────────────────────────────────
# Add to apps/dashboard/models.py:

INVENTORY_MODEL = '''
class InventorySnapshot(models.Model):
    """
    Daily FBA inventory levels per ASIN per marketplace.
    Populated by sync_amazon_data command via FBA Inventory API.
    """
    product             = models.ForeignKey('Product', on_delete=models.CASCADE,
                                             related_name='inventory_snapshots')
    date                = models.DateField()

    # FBA warehouse quantities
    afn_fulfillable     = models.IntegerField(default=0,  help_text='Available to ship')
    afn_reserved        = models.IntegerField(default=0,  help_text='Reserved (orders pending)')
    afn_inbound_working = models.IntegerField(default=0,  help_text='Shipment created, not sent')
    afn_inbound_shipped = models.IntegerField(default=0,  help_text='In transit to FBA')
    afn_inbound_receiving= models.IntegerField(default=0, help_text='Arrived, being received')
    afn_unsellable      = models.IntegerField(default=0,  help_text='Stranded / unfulfillable')

    # 3PL / AWD stock (manual entry or from 3PL API)
    warehouse_stock     = models.IntegerField(default=0)

    # Computed fields
    days_cover          = models.DecimalField(max_digits=6, decimal_places=1, default=0,
                                               help_text='Days of cover at current velocity')
    reorder_point       = models.IntegerField(default=0,
                                               help_text='Units at which to reorder')
    safety_stock        = models.IntegerField(default=0)

    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ix_inventory_snapshots'
        unique_together = [['product', 'date']]
        ordering        = ['-date', 'product']

    def __str__(self):
        return f"{self.product.asin} — {self.date} — {self.afn_fulfillable} units"

    @property
    def total_available(self):
        """Fulfillable + inbound."""
        return (self.afn_fulfillable + self.afn_inbound_working +
                self.afn_inbound_shipped + self.afn_inbound_receiving)

    @property
    def stock_alert(self):
        if self.afn_fulfillable <= 0:
            return 'stockout'
        if self.days_cover < 14:
            return 'critical'
        if self.days_cover < 30:
            return 'low'
        return 'ok'
'''

# ── PPC CAMPAIGN SNAPSHOT ──────────────────────────────────────────────────────
PPC_MODEL = '''
class PPCCampaignSnapshot(models.Model):
    """
    Daily campaign-level advertising metrics.
    One row per campaign per day per marketplace.
    """
    CAMPAIGN_TYPES = [
        ('sp', 'Sponsored Products'),
        ('sb', 'Sponsored Brands'),
        ('sd', 'Sponsored Display'),
    ]
    STATE_CHOICES = [
        ('enabled','Enabled'),('paused','Paused'),('archived','Archived'),
    ]

    marketplace     = models.CharField(max_length=8)
    date            = models.DateField()
    campaign_id     = models.CharField(max_length=64)
    campaign_name   = models.CharField(max_length=256)
    campaign_type   = models.CharField(max_length=4, choices=CAMPAIGN_TYPES, default='sp')
    state           = models.CharField(max_length=12, choices=STATE_CHOICES, default='enabled')
    portfolio       = models.CharField(max_length=128, blank=True)

    # Daily metrics
    impressions     = models.IntegerField(default=0)
    clicks          = models.IntegerField(default=0)
    spend           = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sales_7d        = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    orders_7d       = models.IntegerField(default=0)
    units_7d        = models.IntegerField(default=0)
    acos            = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    roas            = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    ctr             = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cvr             = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    cpc             = models.DecimalField(max_digits=8, decimal_places=4, default=0)

    # Budget
    daily_budget    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    budget_consumed = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                          help_text='% of daily budget consumed')

    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'ix_ppc_snapshots'
        unique_together = [['marketplace', 'date', 'campaign_id']]
        ordering        = ['-date', 'marketplace', '-spend']
        indexes = [
            models.Index(fields=['marketplace', 'date']),
            models.Index(fields=['campaign_id']),
        ]

    def __str__(self):
        return f"{self.marketplace}/{self.campaign_name} — {self.date} — ${self.spend}"

    @property
    def acos_pct(self):
        return float(self.acos) * 100

    @property
    def efficiency_score(self):
        """Simple composite score: lower ACoS + higher CVR = better."""
        acos_score = max(0, 1 - float(self.acos))
        cvr_score  = min(1, float(self.cvr) * 10)
        return round((acos_score * 0.6 + cvr_score * 0.4) * 100, 1)
'''

# ── NOTIFICATION / ALERT ──────────────────────────────────────────────────────
ALERT_MODEL = '''
class Alert(models.Model):
    """
    Auto-generated operational alerts (stockouts, TACoS spikes, etc.)
    """
    SEVERITY = [('critical','Critical'),('warning','Warning'),('info','Info')]
    CATEGORY = [
        ('inventory','Inventory'), ('ppc','PPC'),
        ('performance','Performance'), ('system','System'),
    ]

    marketplace = models.CharField(max_length=8, blank=True)
    severity    = models.CharField(max_length=12, choices=SEVERITY)
    category    = models.CharField(max_length=16, choices=CATEGORY)
    title       = models.CharField(max_length=128)
    message     = models.TextField()
    asin        = models.CharField(max_length=16, blank=True)
    metric_key  = models.CharField(max_length=64, blank=True)
    metric_value= models.CharField(max_length=32, blank=True)
    threshold   = models.CharField(max_length=32, blank=True)

    is_read     = models.BooleanField(default=False)
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        'users.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resolved_alerts'
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ix_alerts'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['marketplace', 'is_read']),
            models.Index(fields=['severity', 'is_resolved']),
        ]

    def __str__(self):
        return f"[{self.severity.upper()}] {self.title}"

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
                'title': f"{'🚨 STOCKOUT RISK' if days_cover < 7 else '⚠️ Low Stock'}: {product.asin}",
                'message': (
                    f"{product.title[:60]} has {fulfillable} units fulfillable, "
                    f"{days_cover:.0f} days of cover. Lead time is 45 days."
                ),
                'metric_value': str(days_cover),
                'threshold': '30',
            }
        )
'''
