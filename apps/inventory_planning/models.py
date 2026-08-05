"""
Inventory Planning (Phase 1) — sales-side view.

Mirrors ops' "Amazon Required Inventory Status Report" workbook:
per-SKU stock across FBA / AWD / 3PL warehouses + factory pipeline,
containers in transit, PDS from sales, and a 100–120-day projection.

Locations are generic (region × kind) so AWD in other regions or new 3PLs
are config rows, not code changes.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class Warehouse(models.Model):
    """A physical stock location. kind drives how stock is fed:
    fba/awd → SP-API sync; 3pl/factory → manual / Excel import."""
    KINDS = [('fba', 'Amazon FBA'), ('awd', 'Amazon AWD'),
             ('3pl', '3PL Warehouse'), ('factory', 'Factory / Origin')]
    code        = models.CharField(max_length=32, unique=True)
    name        = models.CharField(max_length=64)
    region      = models.CharField(max_length=8, default='usa')   # usa/uk/ae/sa
    kind        = models.CharField(max_length=8, choices=KINDS)
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ['region', 'kind', 'code']

    def __str__(self):
        return f'{self.name} ({self.region})'


class PlanningSku(models.Model):
    """SKU master for planning — seeded from the ops workbook.

    sku_type mirrors the Inventory sheet's tier (Alpha/Beta/Ceta), which drives
    the target coverage days (see planning.TARGET_DAYS)."""
    sku            = models.CharField(max_length=64)
    region         = models.CharField(max_length=8, default='usa')
    name           = models.CharField(max_length=128, blank=True)
    category       = models.CharField(max_length=64, blank=True)
    sku_type       = models.CharField(max_length=16, blank=True)   # Alpha/Beta/Ceta
    product_status = models.CharField(max_length=32, blank=True)   # Continue/…
    product_manager = models.CharField(max_length=64, blank=True)
    units_per_box  = models.PositiveIntegerField(default=0)
    msq            = models.PositiveIntegerField(default=0)        # min ship qty
    # factory pipeline (from ops workbook "Pakistan" columns)
    factory_stock      = models.PositiveIntegerField(default=0)
    factory_production = models.PositiveIntegerField(default=0)
    # region barcode identity (from the Product UPCs workbook). The SKU string
    # is near-universal across regions; the FNSKU is what actually differs, and
    # it is applied at the factory when a container's destination is set.
    fnsku          = models.CharField(max_length=16, blank=True)
    upc            = models.CharField(max_length=20, blank=True)
    asin           = models.CharField(max_length=16, blank=True)
    is_active      = models.BooleanField(default=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('sku', 'region')]
        ordering = ['category', 'sku']

    def __str__(self):
        return self.sku


class WarehouseStock(models.Model):
    """Current units of a SKU at a warehouse. One row per (warehouse, sku) —
    updated in place; as_of shows staleness. detail keeps the FBA split
    (available / reserved / inbound)."""
    warehouse  = models.ForeignKey(Warehouse, on_delete=models.CASCADE,
                                   related_name='stocks')
    sku        = models.CharField(max_length=64)
    units      = models.IntegerField(default=0)
    detail     = models.JSONField(default=dict, blank=True)
    as_of      = models.DateTimeField()
    source     = models.CharField(max_length=16, default='manual')  # api/manual/import

    class Meta:
        unique_together = [('warehouse', 'sku')]
        indexes = [models.Index(fields=['sku'])]


class DemandInput(models.Model):
    """PDS — potential daily sales, entered by the sales team. Date-ranged so
    events/seasonality can be layered; the engine picks the row effective on
    each projected day (latest effective_from wins)."""
    sku            = models.CharField(max_length=64)
    region         = models.CharField(max_length=8, default='usa')
    pds            = models.FloatField()
    effective_from = models.DateField()
    effective_to   = models.DateField(null=True, blank=True)   # open-ended
    note           = models.CharField(max_length=128, blank=True)
    entered_by     = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                       blank=True, on_delete=models.SET_NULL)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['sku', 'region', 'effective_from'])]


# Logistics stages a container passes through (in order) before final receipt.
ACTIVE_STATUSES = [
    ('production_complete', 'Production Complete'),
    ('waiting_pickup',      'Waiting for Pickup'),
    ('in_transit',          'In Transit'),
    ('on_vessel',           'On Vessel'),
    ('at_port',             'At Port'),
    ('customs',             'Customs Clearance'),
    ('inland',              'Inland Transit'),
    ('out_for_delivery',    'Out for Delivery'),
    # Amazon has started counting the container in. Still "active" because the
    # units are not all on hand yet — the planner counts the un-received
    # remainder (packed − received) as inbound while this lasts.
    ('receiving',           'Receiving at Amazon'),
]
TERMINAL_STATUSES = [('received', 'Received'), ('cancelled', 'Cancelled')]
ALL_STATUSES = ACTIVE_STATUSES + TERMINAL_STATUSES
ACTIVE_STATUS_KEYS = [k for k, _ in ACTIVE_STATUSES]
# legacy keys kept working (treated as active)
LEGACY_ACTIVE = ['pending', 'departed']


class InTransitShipment(models.Model):
    """A container (or partial shipment) inbound to a warehouse — ops
    maintained. Terminal statuses (received/cancelled) drop off the active
    Containers page and archive to Container History."""
    STATUSES = ALL_STATUSES
    region        = models.CharField(max_length=8, default='usa')
    container_no  = models.CharField(max_length=32, blank=True)
    shipment_id   = models.CharField(max_length=64, blank=True)   # FBA STA id
    po_number     = models.CharField(max_length=64, blank=True)
    vendor        = models.CharField(max_length=64, blank=True)
    destination   = models.ForeignKey(Warehouse, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='inbound_shipments')
    departure_date  = models.DateField(null=True, blank=True)
    eta_port        = models.DateField(null=True, blank=True)
    eta_destination = models.DateField(null=True, blank=True)
    received_date   = models.DateField(null=True, blank=True)
    # receipt audit trail
    received_at   = models.DateTimeField(null=True, blank=True)
    received_by   = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                      blank=True, on_delete=models.SET_NULL)
    status        = models.CharField(max_length=24, choices=ALL_STATUSES,
                                     default='in_transit')
    notes         = models.CharField(max_length=256, blank=True)
    freight_cost  = models.DecimalField(max_digits=12, decimal_places=2,
                                        null=True, blank=True)
    updated_at    = models.DateTimeField(auto_now=True)

    # ── Amazon's view of the linked inbound shipment (see shipment_id) ──
    # RECEIVING means intake has started — that is what moves a container out
    # of In Transit. CLOSED is what finally moves it to History; our own
    # `status` stays ops-owned so a human can still override.
    amazon_status     = models.CharField(max_length=32, blank=True)
    amazon_updated_at = models.DateTimeField(null=True, blank=True)
    amazon_synced_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['eta_destination', 'eta_port']

    def __str__(self):
        return self.container_no or self.shipment_id or f'shipment #{self.pk}'

    @property
    def total_units(self):
        return sum(l.units for l in self.lines.all())

    @property
    def total_received(self):
        """Units counted in — a person's count where there is one, otherwise
        Amazon's.

        A container received through the Goods Receipt screen has
        received_units filled by hand. One that Amazon counted in and closed by
        itself never gets that, only amazon_received_units. Reading received_units
        alone made every auto-closed container look like a 100% loss.
        Human count wins where it exists; the API is never allowed to overwrite it.
        """
        return sum(l.counted_units for l in self.lines.all())

    @property
    def is_archived(self):
        return self.status in ('received', 'cancelled')


class InTransitLine(models.Model):
    """A Container Allocation: qty of one SKU on one container, attributed to
    the Production Plan it was drawn from. A container therefore spans many
    suppliers/POs — ownership lives on the line, never on the container."""
    shipment = models.ForeignKey(InTransitShipment, on_delete=models.CASCADE,
                                 related_name='lines')
    sku      = models.CharField(max_length=64)
    units    = models.PositiveIntegerField(default=0)          # shipped qty (packing list = B)
    received_units = models.PositiveIntegerField(default=0)    # human count, Goods Receipt

    # ── What Amazon reports for this SKU on the linked inbound shipment ──
    # Kept apart from received_units so an API sync never overwrites someone's
    # manual count — where the two disagree, that is itself worth seeing.
    #
    # Amazon works in CASES and we ship in EACHES, so both are converted using
    # units_per_case from the same payload. Amazon's case-pack wins even when
    # it disagrees with ours (per Farhan): their count is what can actually be
    # sold, so a pack-size difference lands inside the variance rather than
    # being argued about.
    amazon_expected_units = models.PositiveIntegerField(default=0)   # A, eaches
    amazon_received_units = models.PositiveIntegerField(default=0)   # C, eaches
    units_per_case        = models.PositiveIntegerField(default=0)   # Amazon's factor
    # procurement attribution (null = legacy line imported before Phase 2).
    # Points at the SKU line — that is the grain a packing list ships at.
    po_line  = models.ForeignKey('POLine', null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name='allocations')
    fnsku    = models.CharField(max_length=16, blank=True)   # region label applied
    # goods-receipt: reason when received != shipped
    RECEIPT_REASONS = [('damage', 'Damage'), ('short_ship', 'Short-ship'),
                       ('lost', 'Lost in transit'), ('miscount', 'Miscount'),
                       ('other', 'Other')]
    variance_reason = models.CharField(max_length=16, blank=True,
                                       choices=RECEIPT_REASONS)

    @property
    def counted_units(self) -> int:
        """What was actually counted in — the human figure, else Amazon's.

        Two sources, deliberately kept in separate columns so neither can
        overwrite the other: received_units is filled on the Goods Receipt
        screen, amazon_received_units by the AWD/FBA receipt syncs. A container
        Amazon closed by itself only ever has the second, so anything reporting
        a shortfall has to fall back to it or the whole container reads as lost.
        """
        return int(self.received_units or 0) or int(self.amazon_received_units or 0)

    @property
    def shortfall_units(self) -> int:
        """Packed minus counted. Never declared minus counted — we always
        declare at least what we pack, so that would invent a loss."""
        return max(0, int(self.units or 0) - self.counted_units)

    class Meta:
        indexes = [models.Index(fields=['sku'])]

    @property
    def discrepancy(self):
        return self.received_units - self.units


# ── Procurement (Phase 2) ────────────────────────────────────────────────────
# Supplier → Purchase Order → PO Line Group (category, FOB) → PO Line (SKU)
#          → Production Plan → Container Allocation (InTransitLine) → Receipt
# Every inventory movement traces back to one Purchase Order.


class Supplier(models.Model):
    code    = models.CharField(max_length=32, unique=True)
    name    = models.CharField(max_length=128)
    country = models.CharField(max_length=64, blank=True)
    contact = models.CharField(max_length=128, blank=True)
    currency = models.CharField(max_length=8, default='USD')
    payment_terms = models.CharField(max_length=64, blank=True)
    # planning lead times (days) — defaults match ops' current assumptions
    production_lead_days = models.PositiveIntegerField(default=90)
    sea_lead_days        = models.PositiveIntegerField(default=45)
    port_to_wh_days      = models.PositiveIntegerField(default=10)
    monthly_capacity_units = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    notes   = models.CharField(max_length=256, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


PO_STATUSES = [
    ('draft',      'Draft'),
    ('open',       'Open'),
    ('partial',    'Partially Allocated'),
    ('allocated',  'Fully Allocated'),
    ('closed',     'Closed'),
    ('cancelled',  'Cancelled'),
]


class PurchaseOrder(models.Model):
    supplier  = models.ForeignKey(Supplier, on_delete=models.PROTECT,
                                  related_name='purchase_orders')
    po_number = models.CharField(max_length=64, unique=True)
    order_date = models.DateField(null=True, blank=True)
    expected_ready_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=64, blank=True)
    currency  = models.CharField(max_length=8, default='USD')
    status    = models.CharField(max_length=16, choices=PO_STATUSES,
                                 default='open')
    notes     = models.CharField(max_length=256, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-order_date', '-id']

    def __str__(self):
        return self.po_number

    # ledger roll-ups (sum of the SKU lines beneath)
    @property
    def ordered_units(self):
        return sum(g.ordered_units for g in self.groups.all())

    @property
    def fob_value(self):
        return sum((g.total_amount or 0) for g in self.groups.all())

    @property
    def allocated_units(self):
        return sum(l.allocated_units for l in self.lines.all())

    @property
    def received_units(self):
        return sum(l.received_units for l in self.lines.all())

    @property
    def wastage_units(self):
        return sum(l.wastage_units for l in self.lines.all())

    @property
    def remaining_units(self):
        return sum(l.remaining_units for l in self.lines.all())


class POLineGroup(models.Model):
    """A category block on the PO — this is where the FOB rate is agreed
    (mirrors the 'Summary' sheet of the ops PO workbook)."""
    po        = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                                  related_name='groups')
    reference = models.CharField(max_length=64, blank=True)   # INF/030426/1
    category  = models.CharField(max_length=64)
    fob_rate  = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    units_per_box = models.PositiveIntegerField(default=0)
    boxes         = models.PositiveIntegerField(default=0)
    ordered_units = models.PositiveIntegerField(default=0)
    total_amount  = models.DecimalField(max_digits=14, decimal_places=2,
                                        default=0)
    pcs       = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'category']

    def __str__(self):
        return f'{self.po.po_number} · {self.category}'


PO_LINE_STATUSES = [
    ('open',        'Open'),
    ('ready',       'Ready for Allocation'),
    ('partial',     'Partially Allocated'),
    ('allocated',   'Fully Allocated'),
    ('closed',      'Closed'),
    ('short_closed', 'Short-Closed'),
]


PENDING_PICKUP = ('production_complete', 'waiting_pickup')


class POLine(models.Model):
    """One SKU (colour/variant) on the PO — the grain a packing list ships at,
    so allocations and wastage both land here."""
    po      = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                                related_name='lines')
    group   = models.ForeignKey(POLineGroup, on_delete=models.CASCADE,
                                related_name='lines')
    sku     = models.CharField(max_length=64)
    name    = models.CharField(max_length=128, blank=True)
    units_per_box = models.PositiveIntegerField(default=0)
    boxes         = models.PositiveIntegerField(default=0)
    ordered_units = models.PositiveIntegerField(default=0)
    wastage_units = models.PositiveIntegerField(default=0)   # factory fault
    cbm_per_box   = models.FloatField(default=0)
    total_cbm     = models.FloatField(default=0)
    expected_ready_date = models.DateField(null=True, blank=True)
    status  = models.CharField(max_length=16, choices=PO_LINE_STATUSES,
                               default='open')
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'sku']
        indexes = [models.Index(fields=['sku'])]

    def __str__(self):
        return f'{self.po.po_number} · {self.sku}'

    @property
    def fob_rate(self):
        return self.group.fob_rate

    @property
    def fob_value(self):
        return float(self.group.fob_rate) * self.ordered_units

    def _live_allocations(self):
        """Allocations that still count — cancelled containers release qty."""
        return self.allocations.exclude(shipment__status='cancelled')

    @property
    def allocated_units(self):
        return sum(a.units for a in self._live_allocations())

    @property
    def loaded_units(self):
        return sum(a.units for a in self._live_allocations()
                   if a.shipment.status not in PENDING_PICKUP)

    @property
    def received_units(self):
        return sum(a.received_units for a in self._live_allocations())

    @property
    def in_transit_units(self):
        return self.allocated_units - self.received_units

    @property
    def remaining_units(self):
        """Wastage closes balance permanently — we don't pay for it and the
        supplier doesn't remake it."""
        return max(self.ordered_units - self.wastage_units
                   - self.allocated_units, 0)

    @property
    def reserved_units(self):
        return sum(r.units for r in self.reservations.all())

    @property
    def available_to_promise(self):
        """Open balance not yet reserved to a region."""
        return max(self.remaining_units - self.reserved_units, 0)

    @property
    def receipt_variance(self):
        return sum(a.received_units - a.units for a in self._live_allocations()
                   if a.shipment.status == 'received')

    # ── goods-receipt variance ──
    @property
    def is_closed(self):
        return self.status in ('closed', 'short_closed')

    @property
    def production_shortage(self):
        """Units ordered (net of wastage) that were NEVER allocated — only a
        realised shortage once the line is closed; otherwise it's 'remaining'."""
        if not self.is_closed:
            return 0
        return max(self.ordered_units - self.wastage_units
                   - self.allocated_units, 0)

    @property
    def transit_shortage(self):
        """Allocated but not received on containers that HAVE been received —
        lost/damaged/short-shipped between supplier and FC."""
        return sum(max(a.units - a.received_units, 0)
                   for a in self._live_allocations()
                   if a.shipment.status == 'received')

    @property
    def over_receipt(self):
        return sum(max(a.received_units - a.units, 0)
                   for a in self._live_allocations()
                   if a.shipment.status == 'received')


class ProductionPlan(models.Model):
    """Outstanding manufacturing commitment for ONE PRODUCT (category) on a PO
    — e.g. PO-105 → Bath Towel = PP-1, Hand Towel = PP-2. The colour/variant
    SKUs sit inside it as PO Lines.

    We get no live production feed, so this carries no progress %. It moves
    only when ops allocates units to a container or goods are received.
    """
    group     = models.OneToOneField(POLineGroup, on_delete=models.CASCADE,
                                     related_name='plan')
    pp_number = models.CharField(max_length=32, blank=True)   # PP-1, PP-2 …
    expected_ready_date = models.DateField(null=True, blank=True)
    status    = models.CharField(max_length=16, choices=PO_LINE_STATUSES,
                                 default='open')
    notes     = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['group__sort_order', 'pp_number']

    def __str__(self):
        return f'{self.pp_number or "PP"} · {self.group.category}'

    @property
    def category(self):
        return self.group.category

    def _lines(self):
        return self.group.lines.all()

    @property
    def ordered_qty(self):
        return sum(l.ordered_units for l in self._lines())

    @property
    def wastage_qty(self):
        return sum(l.wastage_units for l in self._lines())

    @property
    def allocated_qty(self):
        return sum(l.allocated_units for l in self._lines())

    @property
    def loaded_qty(self):
        return sum(l.loaded_units for l in self._lines())

    @property
    def received_qty(self):
        return sum(l.received_units for l in self._lines())

    @property
    def in_transit_qty(self):
        return self.allocated_qty - self.received_qty

    @property
    def remaining_qty(self):
        return sum(l.remaining_units for l in self._lines())

    @property
    def receipt_variance(self):
        return sum(l.receipt_variance for l in self._lines())


class CashFlowPlan(models.Model):
    """Per-region opening bank position for the forward cash-flow planner."""
    region        = models.CharField(max_length=8, unique=True, default='usa')
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2,
                                          default=0)
    opening_as_of = models.DateField(null=True, blank=True)
    # container supplier payment falls this many days BEFORE the port date
    pay_lead_days = models.PositiveIntegerField(default=0)
    updated_at    = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Cash flow {self.region}'


class CashFlowEntry(models.Model):
    """One dated cash movement. Container payments and Amazon inflows are
    generated automatically (auto_source set) and refreshed; anything the user
    edits is `locked` and preserved. Funds injections / HR / storage / duty are
    manual (auto_source='')."""
    DIRECTIONS = [('in', 'Inflow'), ('out', 'Outflow')]
    CATEGORIES = [('container', 'Container Payment'), ('amazon', 'Amazon Inflow'),
                  ('injection', 'Funds Injection'), ('freight', 'Freight / Duty'),
                  ('hr', 'HR Cost'), ('storage', 'Storage'), ('other', 'Other')]
    region      = models.CharField(max_length=8, default='usa')
    date        = models.DateField()
    direction   = models.CharField(max_length=4, choices=DIRECTIONS)
    category    = models.CharField(max_length=16, choices=CATEGORIES,
                                   default='other')
    description = models.CharField(max_length=128, blank=True)
    vendor      = models.CharField(max_length=64, blank=True)
    amount      = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    container   = models.ForeignKey('InTransitShipment', null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='cashflow_entries')
    auto_source = models.CharField(max_length=16, blank=True)  # container/amazon
    locked      = models.BooleanField(default=False)
    note        = models.CharField(max_length=128, blank=True)
    created_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                    blank=True, on_delete=models.SET_NULL)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        indexes = [models.Index(fields=['region', 'date'])]


class FBATransfer(models.Model):
    """A replenishment movement from a 3PL/AWD warehouse INTO Amazon's FC.

    Distinct from InTransitShipment (factory → warehouse). Shipping one draws
    the units out of the source warehouse's stock; Amazon's own FBA sync then
    reports them as inbound → fulfillable, so we never add to FBA by hand.
    """
    STATUSES = [('draft', 'Draft'), ('shipped', 'Shipped to Amazon'),
                ('received', 'Received at FC'), ('cancelled', 'Cancelled')]
    region       = models.CharField(max_length=8, default='usa')
    source       = models.ForeignKey(Warehouse, on_delete=models.PROTECT,
                                     related_name='fba_transfers')
    fba_shipment_id = models.CharField(max_length=64, blank=True)   # FBA/STA id
    carrier      = models.CharField(max_length=64, blank=True)
    reference    = models.CharField(max_length=64, blank=True)      # BOL / PRO
    status       = models.CharField(max_length=16, choices=STATUSES,
                                    default='draft')
    stock_applied = models.BooleanField(default=False)   # source already drawn
    created_by   = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                     blank=True, on_delete=models.SET_NULL)
    created_at   = models.DateTimeField(auto_now_add=True)
    shipped_date = models.DateField(null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)
    notes        = models.CharField(max_length=256, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.fba_shipment_id or f'FBA transfer #{self.pk}'

    @property
    def total_units(self):
        return sum(l.units for l in self.lines.all())

    @property
    def total_received(self):
        return sum(l.received_units for l in self.lines.all())


class FBATransferLine(models.Model):
    transfer = models.ForeignKey(FBATransfer, on_delete=models.CASCADE,
                                 related_name='lines')
    sku      = models.CharField(max_length=64)
    units    = models.PositiveIntegerField(default=0)
    received_units = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=['sku'])]


class POLineReservation(models.Model):
    """Reserves part of a PO line's open balance to a region, so the same
    open-PO units aren't double-promised across regions in the Loading Plan."""
    po_line = models.ForeignKey('POLine', on_delete=models.CASCADE,
                                related_name='reservations')
    region  = models.CharField(max_length=8, default='usa')
    units   = models.PositiveIntegerField(default=0)
    note    = models.CharField(max_length=128, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['po_line', 'region'])]


class ReorderSuggestion(models.Model):
    """A replenishment recommendation from the Planner — pooled across regions,
    netted against on-hand + in-transit + open-PO balance. Reviewed by
    purchasing; approving spins up a draft PurchaseOrder."""
    STATUSES = [('suggested', 'Suggested'), ('approved', 'Approved'),
                ('dismissed', 'Dismissed')]
    sku          = models.CharField(max_length=64)
    name         = models.CharField(max_length=128, blank=True)
    category     = models.CharField(max_length=64, blank=True)
    supplier     = models.ForeignKey(Supplier, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='reorder_suggestions')
    demand_per_day = models.FloatField(default=0)
    recommended_qty = models.PositiveIntegerField(default=0)
    open_po_units  = models.PositiveIntegerField(default=0)
    on_hand_units  = models.PositiveIntegerField(default=0)
    transit_units  = models.PositiveIntegerField(default=0)
    fob_rate       = models.DecimalField(max_digits=10, decimal_places=4,
                                         default=0)
    target_ready_date = models.DateField(null=True, blank=True)
    regions_detail = models.JSONField(default=dict, blank=True)
    status       = models.CharField(max_length=12, choices=STATUSES,
                                    default='suggested')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recommended_qty']
        indexes = [models.Index(fields=['status', 'sku'])]


class SupplierOpeningBalance(models.Model):
    """Backlog carried in from before the system went live. Seeded at zero;
    ops uploads the real figures later and they back-date cleanly."""
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE,
                                 related_name='opening_balances')
    sku      = models.CharField(max_length=64)
    category = models.CharField(max_length=64, blank=True)
    units    = models.IntegerField(default=0)
    as_of    = models.DateField()
    note     = models.CharField(max_length=128, blank=True)

    class Meta:
        unique_together = [('supplier', 'sku', 'as_of')]
        indexes = [models.Index(fields=['supplier', 'sku'])]
