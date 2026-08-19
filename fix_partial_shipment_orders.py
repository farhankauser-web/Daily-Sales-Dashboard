#!/usr/bin/env python
"""
One-off recovery for Walmart-MCF orders archived (COMPLETED) while units were
still unshipped on Amazon.

Root cause: _order_fully_shipped() treated Amazon's COMPLETEPARTIALLED status
as terminal without checking unit-level coverage, and its fallback compared
SKU *sets* (so 3-of-5 units of a single SKU looked "fully covered").

This script:
  1. Scans COMPLETED orders and sums ordered units vs shipped units per SKU.
  2. Prints every order where shipped < ordered.
  3. With --apply, moves them back to TRACKING_UPLOADED so check_status keeps
     polling Amazon and upload_tracking pushes the remaining tracking numbers
     to Walmart.

COMPLETED is a terminal state in the state machine, so the rollback is written
directly with a queryset update plus an explicit AuditEvent — the state machine
is deliberately not weakened to allow this edge.

Usage (on EC2, repo root):
    python fix_partial_shipment_orders.py                 # dry run
    python fix_partial_shipment_orders.py --apply         # apply to all found
    python fix_partial_shipment_orders.py --apply --po 200015153699282
"""
from __future__ import annotations

import argparse
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'infinitee.settings')
django.setup()

from django.db import transaction                                   # noqa: E402
from django.utils import timezone                                   # noqa: E402

from apps.walmart_mcf.models import (AuditEvent, SkuMapping,        # noqa: E402
                                     WalmartOrder,
                                     WalmartOrderState as S)

ACTOR = 'fix_partial_shipment_orders'


def unit_counts(order, mcf):
    """Return (ordered_by_sku, shipped_by_sku) keyed on upper-case Amazon SKU."""
    items = list(order.items.all())
    sku_map = {m.walmart_sku: m.amazon_sku for m in
               SkuMapping.objects.filter(
                   walmart_sku__in=[i.walmart_sku for i in items])}
    ordered = {}
    for i in items:
        key = (sku_map.get(i.walmart_sku) or i.walmart_sku).upper()
        ordered[key] = ordered.get(key, 0) + int(i.quantity or 0)

    shipped = {}
    for p in mcf.packages.all():
        for pi in (p.items or []):
            s = pi.get('sellerSku') or pi.get('SellerSKU')
            if not s:
                continue
            try:
                qty = int(pi.get('quantity', pi.get('Quantity')))
            except (TypeError, ValueError):
                qty = 0
            key = str(s).upper()
            shipped[key] = shipped.get(key, 0) + qty
    return ordered, shipped


def scan(po_filter=None):
    qs = WalmartOrder.objects.filter(status=S.COMPLETED).select_related('mcf')
    if po_filter:
        qs = qs.filter(purchase_order_id__in=po_filter)

    issues = []
    for order in qs:
        mcf = getattr(order, 'mcf', None)
        if mcf is None:
            continue  # fulfilled outside the system, nothing to reconcile
        ordered, shipped = unit_counts(order, mcf)
        if not ordered:
            continue
        total_ordered = sum(ordered.values())
        total_shipped = sum(shipped.get(k, 0) for k in ordered)
        if total_shipped == 0 and not any(shipped.values()):
            # no quantity data at all -> cannot judge, skip rather than churn
            continue
        if total_shipped < total_ordered:
            issues.append({
                'order': order,
                'mcf': mcf,
                'ordered': ordered,
                'shipped': shipped,
                'total_ordered': total_ordered,
                'total_shipped': total_shipped,
            })
    return issues


def rollback(issue):
    order = issue['order']
    with transaction.atomic():
        updated = (WalmartOrder.objects
                   .filter(pk=order.pk, status=S.COMPLETED)
                   .update(status=S.TRACKING_UPLOADED,
                           updated_at=timezone.now()))
        if not updated:
            return False
        AuditEvent.objects.create(
            order_id=order.pk,
            from_state=S.COMPLETED,
            to_state=S.TRACKING_UPLOADED,
            actor=ACTOR,
            detail={
                'reason': 'archived with unshipped units '
                          '(COMPLETEPARTIALLED false-terminal bug)',
                'amazon_status': issue['mcf'].amazon_status,
                'ordered_units': issue['total_ordered'],
                'shipped_units': issue['total_shipped'],
                'pending_units': issue['total_ordered'] - issue['total_shipped'],
                'per_sku_ordered': issue['ordered'],
                'per_sku_shipped': issue['shipped'],
            },
        )
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='actually roll the orders back (default: dry run)')
    ap.add_argument('--po', action='append', default=None,
                    help='limit to specific Walmart purchase order id(s)')
    args = ap.parse_args()

    print('\n' + '=' * 72)
    print('WALMART MCF — orders archived with unshipped units')
    print('=' * 72 + '\n')

    issues = scan(args.po)
    if not issues:
        print('No COMPLETED orders have unshipped units. Nothing to do.\n')
        return 0

    for it in issues:
        o = it['order']
        pend = it['total_ordered'] - it['total_shipped']
        print(f"  PO {o.purchase_order_id}  (id={o.pk})")
        print(f"     amazon_status : {it['mcf'].amazon_status}")
        print(f"     units         : ordered {it['total_ordered']}, "
              f"shipped {it['total_shipped']}, PENDING {pend}")
        for sku, q in it['ordered'].items():
            print(f"       {sku}: {it['shipped'].get(sku, 0)}/{q}")
        print(f"     archived at   : {o.updated_at:%Y-%m-%d %H:%M} UTC\n")

    if not args.apply:
        print(f'{len(issues)} order(s) affected. DRY RUN — re-run with '
              f'--apply to roll them back to TRACKING_UPLOADED.\n')
        return 0

    print('Applying rollback...\n')
    done = 0
    for it in issues:
        po = it['order'].purchase_order_id
        if rollback(it):
            done += 1
            print(f'  OK   {po} -> TRACKING_UPLOADED')
        else:
            print(f'  SKIP {po} (state changed under us)')

    print(f'\n{"=" * 72}')
    print(f'Rolled back {done}/{len(issues)} order(s).')
    print('=' * 72)
    print('check_status will now keep polling these orders; upload_tracking '
          'pushes the remaining tracking to Walmart; reconcile archives them '
          'once every unit is shipped.\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
