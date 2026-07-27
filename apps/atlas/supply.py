"""
Atlas Phase 2 services: RFQ lifecycle, purchase-order stage tracking with
TAT breaches, backorders, and inventory forecasting.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import (DEFAULT_PO_STAGES, RFQ, AtlasProduct, Backorder,
                     POStage, PurchaseOrder, PurchaseOrderLine, RFQResponse)

logger = logging.getLogger(__name__)


def _next_ref(model, prefix: str) -> str:
    year = timezone.now().year
    full = f'{prefix}-{year}-'
    last = (model.objects.filter(reference__startswith=full)
            .order_by('-reference').values_list('reference', flat=True).first())
    n = int(last.rsplit('-', 1)[1]) + 1 if last else 1
    return f'{full}{n:04d}'


# ── RFQ ──────────────────────────────────────────────────────────────────────

def create_rfq(company, user=None, **fields) -> RFQ:
    return RFQ.objects.create(
        company=company, requested_by=user,
        reference=_next_ref(RFQ, f'RFQ-{company.code.upper()}'), **fields)


@transaction.atomic
def respond_rfq(rfq: RFQ, user=None, **fields) -> RFQResponse:
    last = rfq.responses.order_by('-number').values_list('number', flat=True).first()
    resp = RFQResponse.objects.create(rfq=rfq, number=(last or 0) + 1,
                                      created_by=user, **fields)
    rfq.status = 'responded'
    rfq.responded_at = timezone.now()
    rfq.save(update_fields=['status', 'responded_at'])
    return resp


def request_revalidation(rfq: RFQ) -> RFQ:
    """Commercial asks supply chain to re-confirm rates (24h TAT restarts)."""
    rfq.status = 'revalidation'
    rfq.revalidation_requested_at = timezone.now()
    rfq.save(update_fields=['status', 'revalidation_requested_at'])
    return rfq


@transaction.atomic
def apply_response_to_cost(resp: RFQResponse, rate_field: str = 'fob') -> None:
    """
    SOW §8-§12: RFQ cost syncs to the product (and thus every price/margin
    calculation). rate_field ∈ fob|cnf|ddp.
    """
    if resp.rfq.product is None:
        raise ValueError('RFQ has no linked product — create the article first.')
    rate = getattr(resp, f'{rate_field}_rate')
    if rate in (None, ''):
        raise ValueError(f'No {rate_field.upper()} rate on this response.')
    product = resp.rfq.product
    product.cost = Decimal(rate)
    product.save(update_fields=['cost', 'updated_at'])
    resp.applied_to_cost = True
    resp.applied_rate = rate_field
    resp.save(update_fields=['applied_to_cost', 'applied_rate'])


def overdue_rfqs(company=None):
    qs = RFQ.objects.filter(status__in=('open', 'revalidation'))
    if company:
        qs = qs.filter(company=company)
    return [r for r in qs if r.is_overdue]


# ── Purchase orders ──────────────────────────────────────────────────────────

@transaction.atomic
def create_po(company, items: list[dict], user=None, *, supplier=None,
              customer=None, quotation=None, notes: str = '',
              stages: list[tuple] | None = None) -> PurchaseOrder:
    """items: [{product_id, quantity, rate}]. Seeds the default TAT stages
    (first stage starts immediately)."""
    po = PurchaseOrder.objects.create(
        company=company, reference=_next_ref(PurchaseOrder,
                                             f'PO-{company.code.upper()}'),
        supplier=supplier, customer=customer, quotation=quotation,
        notes=notes, created_by=user)
    for it in items:
        PurchaseOrderLine.objects.create(
            po=po, product_id=it['product_id'],
            quantity=int(it['quantity']), rate=it.get('rate') or 0)
    for i, (name, tat) in enumerate(stages or DEFAULT_PO_STAGES, start=1):
        POStage.objects.create(po=po, sequence=i, name=name, tat_days=tat,
                               started_at=timezone.now() if i == 1 else None)
    po.status = 'in_progress'
    po.save(update_fields=['status'])
    return po


@transaction.atomic
def complete_stage(po: PurchaseOrder) -> POStage | None:
    """Complete the current stage and start the next; returns the new stage."""
    cur = po.current_stage
    if cur is None:
        return None
    cur.completed_at = timezone.now()
    cur.save(update_fields=['completed_at'])
    nxt = (po.stages.filter(completed_at__isnull=True)
           .exclude(pk=cur.pk).order_by('sequence').first())
    if nxt:
        nxt.started_at = timezone.now()
        nxt.save(update_fields=['started_at'])
    return nxt


@transaction.atomic
def receive_po(po: PurchaseOrder, received: dict[int, int]) -> dict:
    """
    received: {line_id: qty_received_now}. Adds stock to products; creates
    a Backorder for any shortfall (SOW §91). Returns a summary.
    """
    created_backorders = 0
    for line in po.lines.select_related('product'):
        qty = int(received.get(line.pk, 0) or 0)
        if qty <= 0:
            continue
        line.qty_received += qty
        line.save(update_fields=['qty_received'])
        product = line.product
        product.stock_qty += qty
        product.save(update_fields=['stock_qty', 'updated_at'])
    for line in po.lines.all():
        if line.qty_pending > 0 and not po.backorders.filter(
                product=line.product, status='open').exists():
            Backorder.objects.create(
                company=po.company, po=po, customer=po.customer,
                product=line.product, quantity=line.qty_pending)
            created_backorders += 1
    all_received = all(l.qty_pending == 0 for l in po.lines.all())
    if all_received:
        po.status = 'received'
        po.save(update_fields=['status'])
        # short receipts resolved → close their backorders
        po.backorders.filter(status='open').update(
            status='received', resolved_at=timezone.now())
    return {'all_received': all_received,
            'backorders_created': created_backorders}


@transaction.atomic
def resolve_backorder(bo: Backorder, action: str) -> Backorder:
    """action: received | cancelled (SOW §93)."""
    if action not in ('received', 'cancelled'):
        raise ValueError('action must be received or cancelled')
    bo.status = action
    bo.resolved_at = timezone.now()
    bo.save(update_fields=['status', 'resolved_at'])
    if action == 'received':
        line = bo.po.lines.filter(product=bo.product).first()
        if line:
            line.qty_received += bo.quantity
            line.save(update_fields=['qty_received'])
        bo.product.stock_qty += bo.quantity
        bo.product.save(update_fields=['stock_qty', 'updated_at'])
    # close the PO once nothing is pending and no backorder remains open
    po = bo.po
    if (po.status == 'in_progress'
            and all(l.qty_pending == 0 for l in po.lines.all())
            and not po.backorders.filter(status='open').exists()):
        po.status = 'received'
        po.save(update_fields=['status'])
    return bo


def breached_stages(company=None):
    qs = POStage.objects.filter(completed_at__isnull=True,
                                started_at__isnull=False,
                                po__status='in_progress').select_related('po')
    if company:
        qs = qs.filter(po__company=company)
    return [s for s in qs if s.is_overdue]


# ── Forecasting (SOW §1-§4) ──────────────────────────────────────────────────

def forecast_product(p: AtlasProduct) -> dict:
    """
    Refill = sell-through over (production + shipment) lead time, minus what
    is on hand. Peak mode scales demand by the article's multiplier.
    """
    daily = float(p.sell_through_daily or 0)
    if p.is_peak:
        daily *= float(p.peak_multiplier or 1)
    lead = (p.production_lead_days or 0) + (p.shipment_lead_days or 0)
    demand_over_lead = daily * lead
    cover_days = (p.stock_qty / daily) if daily > 0 else None
    reorder_qty = max(round(demand_over_lead - max(p.stock_qty, 0)), 0)
    return {
        'daily_demand': round(daily, 3),
        'lead_days': lead,
        'cover_days': round(cover_days, 1) if cover_days is not None else None,
        'demand_over_lead': round(demand_over_lead, 1),
        'reorder_qty': reorder_qty,
        # refill is due when stock covers less than the lead time
        'refill_due': (cover_days is not None and cover_days < lead
                       and daily > 0),
    }
