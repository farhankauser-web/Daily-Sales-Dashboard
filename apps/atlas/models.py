"""
Project Atlas — B2B quote-to-cash for the trading entities (Phase 1:
Quotation & Funnel). Multi-company: every record belongs to an
AtlasCompany (Infinitee, RMT, …).

Pricing (per the Rushmore SOW):
    weight_kg  = length_cm × width_cm / 10,000 / 1,000 × GSM
    unit_price = kg_rate × weight_kg
kg_rate lives on the CUSTOMER (flat) with optional per-product overrides,
separately for Local and Container price lists.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class AtlasCompany(models.Model):
    """A selling entity using Atlas (Infinitee, RMT, …)."""
    code       = models.SlugField(max_length=16, unique=True)   # 'infinitee', 'rmt'
    name       = models.CharField(max_length=128)
    currency   = models.CharField(max_length=3, default='AED')
    vat_rate   = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.05'))
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'atlas_companies'
        verbose_name_plural = 'Atlas companies'

    def __str__(self):
        return self.name


class PaymentTerm(models.Model):
    """The fixed set from the SOW (Cash … 30% advance + 120 days)."""
    name         = models.CharField(max_length=64, unique=True)
    days         = models.PositiveIntegerField(default=0)
    advance_pct  = models.PositiveIntegerField(default=0)   # e.g. 30
    sort_order   = models.PositiveIntegerField(default=0)
    is_active    = models.BooleanField(default=True)

    class Meta:
        db_table = 'atlas_payment_terms'
        ordering = ['sort_order']

    def __str__(self):
        return self.name


ORDER_TYPES = [('local', 'Local Order'), ('container', 'Container Order')]


class AtlasCustomer(models.Model):
    company        = models.ForeignKey(AtlasCompany, on_delete=models.CASCADE,
                                       related_name='customers')
    name           = models.CharField(max_length=160)
    contact_person = models.CharField(max_length=120, blank=True)
    email          = models.EmailField(blank=True)
    phone          = models.CharField(max_length=40, blank=True)
    address        = models.TextField(blank=True)
    trn            = models.CharField(max_length=32, blank=True,
                                      help_text='Tax registration number')
    # Kg rates — flat per order type (Option 1); per-article overrides via
    # CustomerKgRate (Option 2). Prices auto-derive from these.
    kg_rate_local     = models.DecimalField(max_digits=10, decimal_places=4,
                                            null=True, blank=True)
    kg_rate_container = models.DecimalField(max_digits=10, decimal_places=4,
                                            null=True, blank=True)
    # Payment terms: default locked to profile; custom may be set per order
    default_payment_term = models.ForeignKey(
        PaymentTerm, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    terms_conditions = models.TextField(
        blank=True, help_text='Customer-specific T&Cs shown on documents')
    notes      = models.TextField(blank=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'atlas_customers'
        unique_together = (('company', 'name'),)
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.company.code})'

    def kg_rate_for(self, order_type: str, product=None) -> Decimal | None:
        """Per-article override wins; else the customer's flat rate."""
        if product is not None:
            o = self.kg_rate_overrides.filter(
                product=product, order_type=order_type).first()
            if o:
                return o.kg_rate
        return (self.kg_rate_local if order_type == 'local'
                else self.kg_rate_container)


class CustomerKgRate(models.Model):
    """Option 2 — variable kg rate per article for one customer."""
    customer   = models.ForeignKey(AtlasCustomer, on_delete=models.CASCADE,
                                   related_name='kg_rate_overrides')
    product    = models.ForeignKey('AtlasProduct', on_delete=models.CASCADE)
    order_type = models.CharField(max_length=12, choices=ORDER_TYPES)
    kg_rate    = models.DecimalField(max_digits=10, decimal_places=4)

    class Meta:
        db_table = 'atlas_customer_kg_rates'
        unique_together = (('customer', 'product', 'order_type'),)


DYEING_CHOICES    = [('vat', 'VAT'), ('reactive', 'Reactive')]
STRUCTURE_CHOICES = [('plain', 'Plain'), ('dobby', 'Dobby'), ('jacquard', 'Jacquard')]


class AtlasProduct(models.Model):
    """A towel article. Quality = the attribute set below (SOW §15)."""
    company     = models.ForeignKey(AtlasCompany, on_delete=models.CASCADE,
                                    related_name='products')
    sku         = models.CharField(max_length=40)       # short convention BT-HSP-20D-600
    description = models.CharField(max_length=200)
    # dimensions drive the weight/price formula
    length_cm   = models.DecimalField(max_digits=8, decimal_places=2)
    width_cm    = models.DecimalField(max_digits=8, decimal_places=2)
    gsm         = models.PositiveIntegerField()
    # quality attributes
    yarn        = models.CharField(max_length=60, blank=True)
    size_label  = models.CharField(max_length=60, blank=True)
    colour      = models.CharField(max_length=60, blank=True)
    dyeing      = models.CharField(max_length=12, choices=DYEING_CHOICES, blank=True)
    structure   = models.CharField(max_length=12, choices=STRUCTURE_CHOICES, blank=True)
    quality     = models.CharField(max_length=80, blank=True, db_index=True,
                                   help_text='Quality family used as the first '
                                             'selector in quotation creation')
    cost        = models.DecimalField(max_digits=12, decimal_places=4, default=0,
                                      help_text='Unit cost (from RFQ/inventory)')
    stock_qty   = models.IntegerField(default=0)
    # ── forecasting (SOW §1-§4): refill = sell-through + stock + lead times ──
    sell_through_daily   = models.DecimalField(
        max_digits=10, decimal_places=3, default=0,
        help_text='Average units sold per day')
    production_lead_days = models.PositiveIntegerField(default=0)
    shipment_lead_days   = models.PositiveIntegerField(default=0)
    is_peak              = models.BooleanField(
        default=False, help_text='Peak/seasonal mode active for this article')
    peak_multiplier      = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal('1.5'),
        help_text='Demand multiplier applied while peak mode is on')
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'atlas_products'
        unique_together = (('company', 'sku'),)
        ordering = ['sku']

    def __str__(self):
        return f'{self.sku} — {self.description[:40]}'

    @property
    def weight_kg(self) -> Decimal:
        """L × W (cm) / 10,000 / 1,000 × GSM — per the SOW formula."""
        if not (self.length_cm and self.width_cm and self.gsm):
            return Decimal('0')
        return (Decimal(self.length_cm) * Decimal(self.width_cm)
                / Decimal(10_000) / Decimal(1_000) * Decimal(self.gsm))


QUOTE_STATUSES = [('draft', 'Draft'), ('sent', 'Sent'),
                  ('won', 'Won'), ('lost', 'Lost')]
CONTAINER_TYPES = [('fob', 'FOB'), ('cnf', 'CNF'), ('ddp', 'DDP')]


class Quotation(models.Model):
    company        = models.ForeignKey(AtlasCompany, on_delete=models.CASCADE,
                                       related_name='quotations')
    reference      = models.CharField(max_length=24, unique=True)  # ATL-RMT-2026-0001
    customer       = models.ForeignKey(AtlasCustomer, on_delete=models.PROTECT,
                                       related_name='quotations')
    order_type     = models.CharField(max_length=12, choices=ORDER_TYPES)
    container_type = models.CharField(max_length=6, choices=CONTAINER_TYPES,
                                      blank=True)                  # container only
    status         = models.CharField(max_length=8, choices=QUOTE_STATUSES,
                                      default='draft', db_index=True)
    payment_term   = models.ForeignKey(PaymentTerm, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name='+')
    # global discounts (per-line discount lives on the line)
    discount_pct    = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expected_delivery = models.DateField(null=True, blank=True)
    is_sample_request = models.BooleanField(default=False)
    remarks        = models.TextField(blank=True)
    lost_reason    = models.CharField(max_length=200, blank=True)
    has_stock_shortage = models.BooleanField(default=False)  # red flag (SOW §19)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at    = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)   # won/lost timestamp

    class Meta:
        db_table = 'atlas_quotations'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.reference} · {self.customer.name} [{self.status}]'

    # ── totals ──
    def totals(self) -> dict:
        sub = Decimal('0'); cost = Decimal('0')
        for ln in self.lines.all():
            sub  += ln.line_total
            cost += (ln.cost or 0) * ln.quantity
        disc = sub * (self.discount_pct or 0) / Decimal(100) + (self.discount_amount or 0)
        net  = sub - disc
        vat  = net * (self.company.vat_rate or 0)
        return {'subtotal': sub, 'discount': disc, 'net': net, 'vat': vat,
                'total': net + vat, 'cost': cost, 'margin': net - cost,
                'margin_pct': (float((net - cost) / net * 100) if net else 0.0)}


class QuotationLine(models.Model):
    quotation  = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                   related_name='lines')
    product    = models.ForeignKey(AtlasProduct, on_delete=models.PROTECT)
    quantity   = models.PositiveIntegerField(default=1)
    kg_rate    = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    weight_kg  = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    cost       = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    discount_pct = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    stock_short  = models.IntegerField(default=0)   # units over available stock

    class Meta:
        db_table = 'atlas_quotation_lines'

    @property
    def line_total(self) -> Decimal:
        gross = (self.unit_price or 0) * self.quantity
        return gross * (Decimal(100) - (self.discount_pct or 0)) / Decimal(100)

    @property
    def margin(self) -> Decimal:
        return self.line_total - (self.cost or 0) * self.quantity


class QuotationRevision(models.Model):
    """Immutable snapshot written on every change (SOW §20/21)."""
    quotation  = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                   related_name='revisions')
    number     = models.PositiveIntegerField()
    snapshot   = models.JSONField()                 # full header + lines
    change_note = models.CharField(max_length=300, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'atlas_quotation_revisions'
        unique_together = (('quotation', 'number'),)
        ordering = ['number']


# ═════════════════════════════ PHASE 2 ══════════════════════════════════════
# RFQ loop, suppliers, purchase orders with TAT-tracked stages, backorders.

RFQ_STATUSES = [('open', 'Open — awaiting response'),
                ('responded', 'Responded'),
                ('revalidation', 'Revalidation requested'),
                ('closed', 'Closed')]
RFQ_KINDS = [('budgetary', 'Budgetary'), ('actual', 'Actual')]
RFQ_TAT_HOURS = 24                       # SOW §13: operations must respond in 24h


class RFQ(models.Model):
    """Commercial → supply-chain request for product costs (SOW §5-§14)."""
    company    = models.ForeignKey(AtlasCompany, on_delete=models.CASCADE,
                                   related_name='rfqs')
    reference  = models.CharField(max_length=24, unique=True)   # RFQ-RMT-2026-0001
    customer   = models.ForeignKey(AtlasCustomer, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='rfqs')
    product    = models.ForeignKey(AtlasProduct, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   help_text='Existing article, if applicable')
    description  = models.CharField(max_length=200, blank=True,
                                    help_text='Free text when no product exists yet')
    size         = models.CharField(max_length=60, blank=True)
    gsm          = models.PositiveIntegerField(null=True, blank=True)
    construction = models.CharField(max_length=120, blank=True)
    quantity     = models.PositiveIntegerField(default=0)
    port         = models.CharField(max_length=80, blank=True,
                                    help_text='Port/city inventory ships from')
    attachment   = models.FileField(upload_to='atlas_rfq/%Y/%m/', blank=True)
    status       = models.CharField(max_length=14, choices=RFQ_STATUSES,
                                    default='open', db_index=True)
    revalidation_requested_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                     on_delete=models.SET_NULL, related_name='+')
    created_at   = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'atlas_rfqs'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.reference} [{self.status}]'

    @property
    def tat_deadline(self):
        from datetime import timedelta
        base = (self.revalidation_requested_at
                if self.status == 'revalidation' else self.created_at)
        return base + timedelta(hours=RFQ_TAT_HOURS) if base else None

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone
        return (self.status in ('open', 'revalidation')
                and self.tat_deadline is not None
                and timezone.now() > self.tat_deadline)


class RFQResponse(models.Model):
    """Supply-chain answer; each re-submission is a new numbered response."""
    rfq        = models.ForeignKey(RFQ, on_delete=models.CASCADE,
                                   related_name='responses')
    number     = models.PositiveIntegerField(default=1)
    kind       = models.CharField(max_length=10, choices=RFQ_KINDS,
                                  default='actual')
    manufacturer_construction = models.CharField(max_length=120, blank=True)
    moq            = models.PositiveIntegerField(null=True, blank=True)
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    inquiry_date   = models.DateField(null=True, blank=True)
    request_date   = models.DateField(null=True, blank=True)
    rates_received_date = models.DateField(null=True, blank=True)
    fob_rate       = models.DecimalField(max_digits=12, decimal_places=4,
                                         null=True, blank=True)
    cnf_rate       = models.DecimalField(max_digits=12, decimal_places=4,
                                         null=True, blank=True)
    ddp_rate       = models.DecimalField(max_digits=12, decimal_places=4,
                                         null=True, blank=True)
    vendor_kg_rate = models.DecimalField(max_digits=10, decimal_places=4,
                                         null=True, blank=True)
    remarks        = models.TextField(blank=True)
    # cost sync (SOW §8-§12): applying writes product.cost from the chosen rate
    applied_to_cost = models.BooleanField(default=False)
    applied_rate    = models.CharField(max_length=4, blank=True)   # fob/cnf/ddp
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'atlas_rfq_responses'
        unique_together = (('rfq', 'number'),)
        ordering = ['number']


class AtlasSupplier(models.Model):
    company    = models.ForeignKey(AtlasCompany, on_delete=models.CASCADE,
                                   related_name='suppliers')
    name       = models.CharField(max_length=160)
    country    = models.CharField(max_length=60, blank=True)
    contact    = models.CharField(max_length=160, blank=True)
    notes      = models.TextField(blank=True)
    is_active  = models.BooleanField(default=True)

    class Meta:
        db_table = 'atlas_suppliers'
        unique_together = (('company', 'name'),)
        ordering = ['name']

    def __str__(self):
        return self.name


PO_STATUSES = [('open', 'Open'), ('in_progress', 'In progress'),
               ('received', 'Received'), ('closed', 'Closed'),
               ('cancelled', 'Cancelled')]

# Default tracking stages seeded onto each new PO (editable per PO).
DEFAULT_PO_STAGES = [('Order confirmed', 2), ('In production', 21),
                     ('Quality check', 3), ('Shipped', 2),
                     ('At destination port', 18), ('Customs cleared', 4),
                     ('Delivered to warehouse', 3)]


class PurchaseOrder(models.Model):
    company    = models.ForeignKey(AtlasCompany, on_delete=models.CASCADE,
                                   related_name='purchase_orders')
    reference  = models.CharField(max_length=24, unique=True)   # PO-RMT-2026-0001
    supplier   = models.ForeignKey(AtlasSupplier, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='purchase_orders')
    customer   = models.ForeignKey(AtlasCustomer, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   help_text='For customer-backed container POs')
    quotation  = models.ForeignKey(Quotation, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='purchase_orders')
    status     = models.CharField(max_length=12, choices=PO_STATUSES,
                                  default='open', db_index=True)
    notes      = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'atlas_purchase_orders'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.reference} [{self.status}]'

    @property
    def current_stage(self):
        return self.stages.filter(completed_at__isnull=True).order_by('sequence').first()


class PurchaseOrderLine(models.Model):
    po        = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                                  related_name='lines')
    product   = models.ForeignKey(AtlasProduct, on_delete=models.PROTECT)
    quantity  = models.PositiveIntegerField()
    rate      = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    qty_received = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'atlas_po_lines'

    @property
    def qty_pending(self) -> int:
        return max(self.quantity - self.qty_received, 0)


class POStage(models.Model):
    """One tracking stage with a TAT; overdue when running past its window."""
    po         = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                                   related_name='stages')
    sequence   = models.PositiveIntegerField()
    name       = models.CharField(max_length=80)
    tat_days   = models.PositiveIntegerField(default=0)
    started_at   = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    alerted      = models.BooleanField(default=False)   # TAT-breach alert sent

    class Meta:
        db_table = 'atlas_po_stages'
        unique_together = (('po', 'sequence'),)
        ordering = ['sequence']

    @property
    def deadline(self):
        from datetime import timedelta
        return (self.started_at + timedelta(days=self.tat_days)
                if self.started_at and self.tat_days else None)

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone
        return (self.completed_at is None and self.deadline is not None
                and timezone.now() > self.deadline)


class Backorder(models.Model):
    """Short receipt against a PO (SOW §91-§93). Surfaces on the customer's
    next quotation until received or cancelled."""
    BO_STATUSES = [('open', 'Open'), ('received', 'Received'),
                   ('cancelled', 'Cancelled')]
    company   = models.ForeignKey(AtlasCompany, on_delete=models.CASCADE)
    po        = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                                  related_name='backorders')
    customer  = models.ForeignKey(AtlasCustomer, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name='backorders')
    product   = models.ForeignKey(AtlasProduct, on_delete=models.CASCADE)
    quantity  = models.PositiveIntegerField()
    status    = models.CharField(max_length=10, choices=BO_STATUSES,
                                 default='open', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'atlas_backorders'


class NegativeStockLog(models.Model):
    """Tracked over-commitments (SOW §26): quote lines exceeding stock."""
    company    = models.ForeignKey(AtlasCompany, on_delete=models.CASCADE)
    quotation  = models.ForeignKey(Quotation, on_delete=models.CASCADE,
                                   related_name='negative_stock')
    product    = models.ForeignKey(AtlasProduct, on_delete=models.CASCADE)
    qty_short  = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'atlas_negative_stock_log'


# ── Phase 3: Finance & Billing ──────────────────────────────────────────────

class AtlasInvoice(models.Model):
    """A customer invoice — typically raised from a won Quotation."""
    STATUSES = [('draft', 'Draft'), ('sent', 'Sent'),
                ('part_paid', 'Part-paid'), ('paid', 'Paid'),
                ('void', 'Void')]
    company    = models.ForeignKey('AtlasCompany', on_delete=models.CASCADE,
                                   related_name='invoices')
    customer   = models.ForeignKey('AtlasCustomer', on_delete=models.PROTECT,
                                   related_name='invoices')
    quotation  = models.ForeignKey('Quotation', null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='invoices')
    number     = models.CharField(max_length=32, unique=True)
    invoice_date = models.DateField()
    due_date   = models.DateField(null=True, blank=True)
    payment_term = models.ForeignKey('PaymentTerm', null=True, blank=True,
                                     on_delete=models.SET_NULL)
    currency   = models.CharField(max_length=8, default='USD')
    subtotal   = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount   = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vat        = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total      = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status     = models.CharField(max_length=12, choices=STATUSES,
                                  default='draft')
    notes      = models.CharField(max_length=256, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-invoice_date', '-id']

    def __str__(self):
        return self.number

    @property
    def paid(self):
        return sum(p.amount for p in self.payments.all())

    @property
    def balance(self):
        return self.total - self.paid

    @property
    def is_overdue(self):
        from datetime import date
        return (self.status not in ('paid', 'void') and self.due_date
                and self.due_date < date.today() and self.balance > 0)

    def recompute_status(self):
        if self.status == 'void':
            return
        p = self.paid
        if p <= 0:
            self.status = 'sent' if self.status != 'draft' else 'draft'
        elif p >= self.total:
            self.status = 'paid'
        else:
            self.status = 'part_paid'


class AtlasInvoiceLine(models.Model):
    invoice     = models.ForeignKey(AtlasInvoice, on_delete=models.CASCADE,
                                    related_name='lines')
    product     = models.ForeignKey('AtlasProduct', null=True, blank=True,
                                    on_delete=models.SET_NULL)
    description = models.CharField(max_length=256)
    quantity    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    unit_price  = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    line_total  = models.DecimalField(max_digits=14, decimal_places=2, default=0)


class AtlasPayment(models.Model):
    METHODS = [('bank', 'Bank transfer'), ('cash', 'Cash'),
               ('cheque', 'Cheque'), ('card', 'Card'), ('other', 'Other')]
    invoice   = models.ForeignKey(AtlasInvoice, on_delete=models.CASCADE,
                                  related_name='payments')
    date      = models.DateField()
    amount    = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    method    = models.CharField(max_length=10, choices=METHODS, default='bank')
    reference = models.CharField(max_length=64, blank=True)
    note      = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
