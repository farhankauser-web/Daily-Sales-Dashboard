#!/usr/bin/env python
"""
Repair packages that were recorded as uploaded to Walmart when they were not.

The old upload_tracking() branch marked EVERY pending package as
`uploaded_to_walmart_at = now` whenever Walmart's order looked settled, and
wrote 'tracking already present on Walmart' into upload_error. That test read
the presence of a "Shipped" status rather than its quantity, so a partially
shipped line looked complete and later tracking numbers were silently dropped.

Those rows still claim to be uploaded, so the new _all_packages_uploaded()
archive gate trusts them. This script asks Walmart what tracking it actually
holds and repairs the rows that lied.

    python fix_false_tracking_uploads.py            # dry run (default)
    python fix_false_tracking_uploads.py --apply
    python fix_false_tracking_uploads.py --apply --po 129123364460162

For each package Walmart does NOT hold, --apply will:
  * clear uploaded_to_walmart_at and record why
  * move the order back to SHIPPED so the fixed upload_tracking() re-evaluates
    it — which will retry, fail cleanly, and raise a proper admin alert

Walmart is only ever read here. Nothing is pushed to Walmart by this script.
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

from apps.walmart_mcf.models import (AuditEvent, ShipmentPackage,   # noqa: E402
                                     WalmartOrder,
                                     WalmartOrderState as S)
from apps.walmart_mcf.pipeline import _walmart_order_snapshot       # noqa: E402
from apps.walmart_mcf.walmart_client import WalmartClient           # noqa: E402

ACTOR = 'fix_false_tracking_uploads'
MARKER = 'tracking already present on Walmart'


def suspect_orders(po_filter=None):
    """Orders holding at least one package marked uploaded via the old branch."""
    pkgs = ShipmentPackage.objects.filter(
        upload_error__icontains=MARKER,
        uploaded_to_walmart_at__isnull=False,
    ).select_related('mcf_order__order')
    orders = {}
    for p in pkgs:
        o = p.mcf_order.order
        if po_filter and o.purchase_order_id not in po_filter:
            continue
        orders.setdefault(o.pk, (o, []))[1].append(p)
    return list(orders.values())


def repair(order, bad_pkgs):
    with transaction.atomic():
        for p in bad_pkgs:
            p.uploaded_to_walmart_at = None
            p.upload_error = (
                'NOT on Walmart: previously recorded as uploaded in error by '
                'the pre-fix already-shipped branch. Verified against Walmart '
                f'on {timezone.now():%Y-%m-%d %H:%M} UTC.')[:500]
            p.save(update_fields=['uploaded_to_walmart_at', 'upload_error'])

        if order.status in (S.COMPLETED, S.TRACKING_UPLOADED):
            updated = (WalmartOrder.objects
                       .filter(pk=order.pk, status=order.status)
                       .update(status=S.SHIPPED, updated_at=timezone.now()))
            if updated:
                AuditEvent.objects.create(
                    order_id=order.pk,
                    from_state=order.status,
                    to_state=S.SHIPPED,
                    actor=ACTOR,
                    detail={'reason': 'tracking was never accepted by Walmart',
                            'tracking': [p.tracking_number for p in bad_pkgs]},
                )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--po', action='append', default=None)
    args = ap.parse_args()

    print('\n' + '=' * 72)
    print('WALMART MCF — packages recorded as uploaded but never accepted')
    print('=' * 72 + '\n')

    found = suspect_orders(args.po)
    if not found:
        print('No packages carry the old marker. Nothing to check.\n')
        return 0

    print(f'{len(found)} order(s) to verify against Walmart...\n')
    wc = WalmartClient()
    to_fix, unreachable = [], []

    for order, pkgs in found:
        snap = _walmart_order_snapshot(wc, order.purchase_order_id)
        if snap is None:
            unreachable.append(order)
            print(f'  ?? PO {order.purchase_order_id}: Walmart lookup failed — '
                  f'skipped (will not guess)')
            continue
        held = snap['tracking']
        bad = [p for p in pkgs if p.tracking_number.upper() not in held]
        state = 'fully settled' if snap['fully_shipped'] else 'still open'
        print(f'  PO {order.purchase_order_id}  [{order.status}]  '
              f'Walmart line: {state}')
        for p in pkgs:
            ok = p.tracking_number.upper() in held
            print(f'     {"HELD    " if ok else "MISSING "} '
                  f'{p.tracking_number}')
        if bad:
            to_fix.append((order, bad))
        print()

    print('-' * 72)
    print(f'{len(to_fix)} order(s) have tracking Walmart never received.')
    if unreachable:
        print(f'{len(unreachable)} order(s) could not be checked (API error).')

    if not to_fix:
        print()
        return 0

    if not args.apply:
        print('DRY RUN — re-run with --apply to un-mark those packages and '
              'send the orders back through upload_tracking.\n')
        return 0

    print('\nRepairing...\n')
    for order, bad in to_fix:
        repair(order, bad)
        print(f'  fixed PO {order.purchase_order_id} '
              f'({len(bad)} package(s) un-marked, order -> SHIPPED)')
    print(f'\nDone. The next upload_tracking run will retry these; any Walmart '
          f'still refuses will land in ERROR with an admin alert.\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
