"""
Planner → Draft PO (reorder engine).

Pools demand across ALL regions per SKU, nets against pooled on-hand +
in-transit + the region-blind open-PO balance, and where a gap remains,
recommends a purchase: supplier, quantity (carton/MSQ-rounded), target-ready
date. Approving a suggestion spins up a draft PurchaseOrder.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

from django.db import transaction

REGIONS = ['usa', 'uk', 'ae', 'sa']


def _supplier_and_fob(sku: str):
    """Best supplier for a SKU: the one holding open PO balance → else the most
    recent PO supplier → else cheapest historical. Returns (supplier, fob)."""
    from .models import POLine
    lines = list(POLine.objects.filter(sku__iexact=sku)
                 .select_related('po__supplier', 'group')
                 .order_by('-po__order_date'))
    if not lines:
        return None, 0.0
    open_lines = [l for l in lines if l.remaining_units > 0
                  and l.po.status not in ('closed', 'cancelled', 'short_closed')]
    pool = open_lines or lines
    # cheapest FOB among the candidate pool, tie-break most recent
    best = min(pool, key=lambda l: (float(l.group.fob_rate) or 9e9))
    return best.po.supplier, float(best.group.fob_rate)


@transaction.atomic
def build_suggestions(cover_days: int | None = None) -> dict:
    from .models import (PlanningSku, POLine, ReorderSuggestion, Supplier)
    from .planning import build_projection, ship_lead_days

    # open-PO balance per SKU (region-blind)
    open_po = defaultdict(int)
    for l in (POLine.objects.exclude(
            po__status__in=['cancelled', 'closed', 'short_closed'])):
        open_po[l.sku.upper()] += l.remaining_units

    # pooled demand + on-hand + transit across regions
    agg: dict = {}
    for region in REGIONS:
        proj = build_projection(region)
        sl = ship_lead_days(region)
        for r in proj['rows']:
            d = r['demand_basis']
            if d <= 0:
                continue
            sku = r['sku']
            target = cover_days or (sl + r['target_days'])
            a = agg.setdefault(sku, {'need': 0.0, 'onhand': 0, 'transit': 0,
                                     'demand': 0.0, 'name': r['name'],
                                     'category': r['category'], 'regions': {}})
            a['need'] += d * target
            a['onhand'] += r['total_amazon'] + r['total_wh']
            a['transit'] += r['transit']
            a['demand'] += d
            a['regions'][region] = {
                'demand': round(d, 2),
                'onhand': r['total_amazon'] + r['total_wh'],
                'transit': r['transit'],
                'cover_days': r['cover_total_days']}

    upb = {p.sku.upper(): p.units_per_box for p in PlanningSku.objects.all()}
    msq = {p.sku.upper(): p.msq for p in PlanningSku.objects.all()}

    # wipe prior un-actioned suggestions; keep approved/dismissed history
    ReorderSuggestion.objects.filter(status='suggested').delete()

    res = {'created': 0, 'units': 0}
    for sku, a in agg.items():
        committed = a['onhand'] + a['transit'] + open_po.get(sku, 0)
        gap = a['need'] - committed
        if gap <= 0:
            continue
        qty = int(math.ceil(gap))
        box = upb.get(sku, 0)
        if box:
            qty = math.ceil(qty / box) * box
        if qty < (msq.get(sku, 0) or 0):
            qty = msq[sku]
        supplier, fob = _supplier_and_fob(sku)
        lead = supplier.production_lead_days if supplier else 90
        ReorderSuggestion.objects.create(
            sku=sku, name=a['name'], category=a['category'], supplier=supplier,
            demand_per_day=round(a['demand'], 2), recommended_qty=qty,
            open_po_units=open_po.get(sku, 0), on_hand_units=a['onhand'],
            transit_units=a['transit'], fob_rate=fob,
            target_ready_date=date.today() + timedelta(days=lead),
            regions_detail=a['regions'])
        res['created'] += 1
        res['units'] += qty
    return res


@transaction.atomic
def approve_suggestions(ids: list[int], user=None) -> dict:
    """Turn approved suggestions into draft PurchaseOrder(s), grouped by
    supplier, one category group per product category."""
    from .models import (POLine, POLineGroup, ProductionPlan, PurchaseOrder,
                         ReorderSuggestion)

    sugs = list(ReorderSuggestion.objects.filter(id__in=ids,
                                                 status='suggested')
                .select_related('supplier'))
    by_sup = defaultdict(list)
    for s in sugs:
        if s.supplier_id:
            by_sup[s.supplier_id].append(s)

    res = {'pos': 0, 'lines': 0, 'skipped_no_supplier': 0}
    res['skipped_no_supplier'] = sum(1 for s in sugs if not s.supplier_id)
    today = date.today()
    for sup_id, items in by_sup.items():
        supplier = items[0].supplier
        base = f'DRAFT-{supplier.code}-{today:%y%m%d}'
        po_number = base
        i = 1
        while PurchaseOrder.objects.filter(po_number=po_number).exists():
            i += 1
            po_number = f'{base}-{i}'
        po = PurchaseOrder.objects.create(
            supplier=supplier, po_number=po_number, order_date=today,
            status='draft', notes='Auto-generated from Planner reorder',
            created_by=user)
        groups = {}
        for seq, s in enumerate(items, 1):
            cat = s.category or 'Reorder'
            g = groups.get(cat)
            if g is None:
                g = POLineGroup.objects.create(
                    po=po, category=cat, fob_rate=s.fob_rate,
                    sort_order=len(groups))
                groups[cat] = g
                ProductionPlan.objects.create(
                    group=g, pp_number=f'PP-{len(groups)}',
                    expected_ready_date=s.target_ready_date)
            POLine.objects.create(
                po=po, group=g, sku=s.sku, name=s.name,
                ordered_units=s.recommended_qty,
                expected_ready_date=s.target_ready_date, sort_order=seq)
            g.ordered_units += s.recommended_qty
            g.total_amount = float(g.fob_rate) * g.ordered_units
            g.save(update_fields=['ordered_units', 'total_amount'])
            s.status = 'approved'
            s.save(update_fields=['status'])
            res['lines'] += 1
        res['pos'] += 1
    return res
