"""
Walmart Marketplace → Amazon MCF automation.

Order lifecycle (state machine, enforced by state.transition):

    NEW → VALIDATED → PROCESSING → MCF_CREATED → SHIPPED
        → TRACKING_UPLOADED → COMPLETED

    Side states: ERROR (bad mapping/address — needs admin),
                 HOLD (insufficient inventory — auto-retried),
                 CANCELLED (Amazon cancelled the fulfillment order).

Idempotency guarantees:
  * purchase_order_id UNIQUE          → duplicate imports impossible
  * fulfillment_order_id UNIQUE       → duplicate MCF orders impossible
    (and it is derived deterministically as 'WM-{purchase_order_id}')
  * ShipmentPackage.upload_hash UNIQUE→ duplicate tracking uploads impossible
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


from apps.amazon_api.models import EncryptedField


class WalmartAPIConfig(models.Model):
    """
    Walmart Marketplace API credentials (single row). Secrets are encrypted
    at rest with the same Fernet field used for the Amazon credentials.
    Falls back to WALMART_CLIENT_ID/SECRET env vars when no active row exists.
    """
    label         = models.CharField(max_length=64, default='Walmart US Marketplace')
    is_active     = models.BooleanField(default=True)
    client_id     = EncryptedField(blank=True, help_text='Walmart Client ID')
    client_secret = EncryptedField(blank=True, help_text='Walmart Client Secret')
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wm_api_config'
        verbose_name = 'Walmart API Configuration'

    def __str__(self):
        return f'{self.label}{"" if self.is_active else " (inactive)"}'


class WalmartOrderState(models.TextChoices):
    NEW               = 'NEW',               'New'
    VALIDATED         = 'VALIDATED',         'Validated'
    PROCESSING        = 'PROCESSING',        'Processing'
    MCF_CREATED       = 'MCF_CREATED',       'MCF Created'
    SHIPPED           = 'SHIPPED',           'Shipped'
    TRACKING_UPLOADED = 'TRACKING_UPLOADED', 'Tracking Uploaded'
    COMPLETED         = 'COMPLETED',         'Completed'
    ERROR             = 'ERROR',             'Error'
    HOLD              = 'HOLD',              'Hold (inventory)'
    CANCELLED         = 'CANCELLED',         'Cancelled'


class WalmartOrder(models.Model):
    purchase_order_id = models.CharField(max_length=32, unique=True)
    customer_order_id = models.CharField(max_length=32, blank=True)
    marketplace       = models.CharField(max_length=8, default='usa')
    status            = models.CharField(max_length=24,
                                         choices=WalmartOrderState.choices,
                                         default=WalmartOrderState.NEW,
                                         db_index=True)
    order_date        = models.DateTimeField()
    customer_name     = models.CharField(max_length=128, blank=True)
    phone             = models.CharField(max_length=32, blank=True)
    shipping_address  = models.JSONField(default=dict)   # Walmart postalAddress verbatim
    shipping_method   = models.CharField(max_length=24, blank=True)  # Walmart methodCode
    raw_order         = models.JSONField(default=dict)   # full Walmart payload for audit
    error_reason      = models.TextField(blank=True)
    acknowledged_at   = models.DateTimeField(null=True, blank=True)
    imported_at       = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wm_orders'
        ordering = ['-order_date']

    def __str__(self):
        return f'{self.purchase_order_id} [{self.status}]'


class WalmartOrderItem(models.Model):
    order        = models.ForeignKey(WalmartOrder, on_delete=models.CASCADE,
                                     related_name='items')
    line_number  = models.CharField(max_length=8)
    walmart_sku  = models.CharField(max_length=64, db_index=True)
    product_name = models.CharField(max_length=256, blank=True)
    quantity     = models.PositiveIntegerField(default=1)
    unit_price   = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        db_table = 'wm_order_items'
        unique_together = (('order', 'line_number'),)

    def __str__(self):
        return f'{self.order_id}#{self.line_number} {self.walmart_sku} x{self.quantity}'


class SkuMapping(models.Model):
    walmart_sku = models.CharField(max_length=64, unique=True)
    amazon_sku  = models.CharField(max_length=64)
    enabled     = models.BooleanField(default=True)
    notes       = models.CharField(max_length=256, blank=True)
    updated_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wm_sku_mappings'
        verbose_name = 'SKU Mapping'

    def __str__(self):
        state = '' if self.enabled else ' (disabled)'
        return f'{self.walmart_sku} → {self.amazon_sku}{state}'


class AmazonMCFOrder(models.Model):
    order                = models.OneToOneField(WalmartOrder, on_delete=models.CASCADE,
                                                related_name='mcf')
    fulfillment_order_id = models.CharField(max_length=48, unique=True)
    amazon_status        = models.CharField(max_length=32, blank=True, db_index=True)
    shipping_speed       = models.CharField(max_length=24, default='Standard')
    feature_constraints  = models.JSONField(default=list)   # what Amazon echoed back
    submitted_at         = models.DateTimeField(auto_now_add=True)
    last_status_check    = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'wm_mcf_orders'
        verbose_name = 'Amazon MCF Order'

    def __str__(self):
        return f'{self.fulfillment_order_id} [{self.amazon_status}]'


class ShipmentPackage(models.Model):
    """One physical package (one tracking number). Multiple per order OK."""
    mcf_order          = models.ForeignKey(AmazonMCFOrder, on_delete=models.CASCADE,
                                           related_name='packages')
    shipment_id        = models.CharField(max_length=64, blank=True)
    package_number     = models.IntegerField(default=0)
    carrier_code       = models.CharField(max_length=32, blank=True)   # Amazon's name
    carrier_walmart    = models.CharField(max_length=32, blank=True)   # normalized
    tracking_number    = models.CharField(max_length=64, db_index=True)
    ship_date          = models.DateTimeField(null=True, blank=True)
    estimated_delivery = models.DateTimeField(null=True, blank=True)
    items              = models.JSONField(default=list)  # [{sellerSku, quantity}]
    # sha1 of (po_id, line, carrier, tracking) — UNIQUE makes re-upload impossible
    upload_hash        = models.CharField(max_length=48, unique=True)
    uploaded_to_walmart_at = models.DateTimeField(null=True, blank=True)
    upload_error       = models.TextField(blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'wm_shipment_packages'

    def __str__(self):
        return f'{self.tracking_number} ({self.carrier_code})'


class AuditEvent(models.Model):
    order      = models.ForeignKey(WalmartOrder, on_delete=models.CASCADE,
                                   related_name='audit_events')
    from_state = models.CharField(max_length=24, blank=True)
    to_state   = models.CharField(max_length=24)
    actor      = models.CharField(max_length=64)      # task name / admin username
    detail     = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'wm_audit_events'
        ordering = ['created_at']


class APILog(models.Model):
    DIRECTIONS = [('walmart', 'Walmart'), ('amazon', 'Amazon')]
    direction      = models.CharField(max_length=8, choices=DIRECTIONS)
    endpoint       = models.CharField(max_length=256)
    method         = models.CharField(max_length=8)
    request_body   = models.TextField(blank=True)     # truncated, secrets redacted
    response_body  = models.TextField(blank=True)     # truncated
    status_code    = models.IntegerField(null=True)
    duration_ms    = models.IntegerField(default=0)
    correlation_id = models.CharField(max_length=64, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'wm_api_logs'


class ErrorLog(models.Model):
    order       = models.ForeignKey(WalmartOrder, null=True, blank=True,
                                    on_delete=models.SET_NULL)
    endpoint    = models.CharField(max_length=256, blank=True)
    exception   = models.CharField(max_length=256)
    stack_trace = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    resolved    = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'wm_error_logs'
