#!/usr/bin/env python
"""
Read-only dump of everything we hold for one Walmart PO: order state, the
Amazon MCF record, ordered lines, SKU mappings, every harvested package with
its raw items JSON, and the full audit trail.

Writes nothing. Use it to decide what actually happened before touching data.

Usage (repo root):
    python inspect_walmart_order.py 200015153699282
    python inspect_walmart_order.py --recent 15
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'infinitee.settings')
django.setup()

from apps.walmart_mcf.models import (APILog, AuditEvent,            # noqa: E402
                                     SkuMapping, WalmartOrder)


def dump(order):
    print('=' * 72)
    print(f'PO {order.purchase_order_id}   (pk={order.pk})')
    print('=' * 72)
    print(f'  customer_order  : {order.customer_order_id}')
    print(f'  status          : {order.status}')
    print(f'  marketplace     : {order.marketplace}')
    print(f'  order_date      : {order.order_date}')
    print(f'  imported_at     : {order.imported_at}')
    print(f'  updated_at      : {order.updated_at}')
    print(f'  error_reason    : {order.error_reason[:200] or "(none)"}')

    mcf = getattr(order, 'mcf', None)
    if mcf is None:
        print('\n  !! no AmazonMCFOrder row attached\n')
    else:
        print(f'\n  MCF fulfillment_order_id : {mcf.fulfillment_order_id}')
        print(f'  MCF amazon_status        : {mcf.amazon_status!r}')
        print(f'  MCF submitted_at         : {mcf.submitted_at}')
        print(f'  MCF last_status_check    : {mcf.last_status_check}')

    items = list(order.items.all())
    print(f'\n  ORDERED LINES ({len(items)})')
    if not items:
        print('    !! no WalmartOrderItem rows')
    smap = {m.walmart_sku: m.amazon_sku for m in SkuMapping.objects.filter(
        walmart_sku__in=[i.walmart_sku for i in items])}
    total_ordered = 0
    for i in items:
        total_ordered += int(i.quantity or 0)
        print(f'    line {i.line_number}: walmart_sku={i.walmart_sku!r} '
              f'qty={i.quantity} -> amazon_sku='
              f'{smap.get(i.walmart_sku) or "(UNMAPPED)"!r}')
    print(f'    TOTAL ORDERED UNITS: {total_ordered}')

    pkgs = list(mcf.packages.all()) if mcf else []
    print(f'\n  PACKAGES ({len(pkgs)})')
    total_shipped = 0
    for p in pkgs:
        print(f'    pkg#{p.package_number} shipment={p.shipment_id!r} '
              f'carrier={p.carrier_code!r}/{p.carrier_walmart!r}')
        print(f'      tracking      : {p.tracking_number}')
        print(f'      ship_date     : {p.ship_date}')
        print(f'      uploaded_to_wm: {p.uploaded_to_walmart_at}')
        print(f'      upload_error  : {p.upload_error[:160] or "(none)"}')
        print(f'      items JSON    : {json.dumps(p.items)}')
        for pi in (p.items or []):
            try:
                total_shipped += int(pi.get('quantity'))
            except (TypeError, ValueError):
                print('      !! item has no parseable quantity')
    print(f'    TOTAL SHIPPED UNITS (summed from items JSON): {total_shipped}')

    if total_ordered:
        print(f'\n  >>> {total_shipped}/{total_ordered} units shipped, '
              f'{total_ordered - total_shipped} pending')

    # The decisive evidence: what quantity did WE declare to Walmart?
    from django.db.models import Q
    po = order.purchase_order_id
    q = Q(endpoint__contains=po) | Q(request_body__contains=po)
    if order.customer_order_id:
        q |= Q(endpoint__contains=order.customer_order_id)
        q |= Q(request_body__contains=order.customer_order_id)
    logs = APILog.objects.filter(q).order_by('created_at')
    print(f'  WALMART API CALLS mentioning this PO ({logs.count()})')
    for lg in logs:
        print(f'    {lg.created_at:%Y-%m-%d %H:%M:%S} {lg.direction} '
              f'{lg.method} {lg.endpoint}  -> {lg.status_code}')
        if lg.request_body and ('shipping' in lg.endpoint.lower()
                                or 'orderShipment' in lg.request_body):
            print(f'      REQUEST : {lg.request_body[:2500]}')
        # For GET /orders/{po} show what WALMART thinks each line's status is
        if lg.response_body:
            body = lg.response_body
            idx = body.find('orderLineStatuses')
            if idx == -1:
                idx = body.find('orderLines')
            if idx != -1:
                print(f'      WALMART LINE STATUS: ...{body[idx:idx + 2000]}')
    print()

    ev = AuditEvent.objects.filter(order_id=order.pk).order_by('created_at')
    print(f'\n  AUDIT TRAIL ({ev.count()})')
    for e in ev:
        print(f'    {e.created_at:%Y-%m-%d %H:%M:%S}  '
              f'{e.from_state or "-"} -> {e.to_state}  by {e.actor}')
        if e.detail:
            print(f'        {json.dumps(e.detail)[:300]}')
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('po', nargs='*', help='Walmart purchase order id(s)')
    ap.add_argument('--recent', type=int, default=0,
                    help='instead, list the N most recently updated orders')
    args = ap.parse_args()

    if args.recent:
        qs = WalmartOrder.objects.order_by('-updated_at')[:args.recent]
        print(f'\n{"PO":<20} {"status":<20} {"amazon_status":<22} updated')
        print('-' * 88)
        for o in qs.select_related('mcf'):
            mcf = getattr(o, 'mcf', None)
            print(f'{o.purchase_order_id:<20} {o.status:<20} '
                  f'{(mcf.amazon_status if mcf else "-"):<22} '
                  f'{o.updated_at:%Y-%m-%d %H:%M}')
        print()
        return 0

    if not args.po:
        ap.error('give a PO id, or use --recent N')

    for po in args.po:
        order = (WalmartOrder.objects.filter(purchase_order_id=po).first()
                 or WalmartOrder.objects.filter(customer_order_id=po).first())
        if order is None:
            print(f'\n!! no WalmartOrder row with purchase_order_id or '
                  f'customer_order_id = {po!r}\n')
            continue
        dump(order)
    return 0


if __name__ == '__main__':
    sys.exit(main())
