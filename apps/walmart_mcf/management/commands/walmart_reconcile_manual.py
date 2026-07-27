"""
Reconcile Walmart orders against the Amazon **MCF orders** (dashboard McfOrder)
that were created manually in Seller Central. For every Walmart order that is
sitting in the Active list without a linked tracking number, find its manual
MCF twin, harvest the real (non-TBA) tracking, and link a confirmed
ShipmentPackage so the order shows its tracking and moves to Archive.

    python manage.py walmart_reconcile_manual [--dry-run]

This is the "Sync / reconcile from MCF orders" action: it never invents data —
it only pulls tracking that already exists on the matched manual MCF order.
Idempotent via ShipmentPackage.upload_hash.
"""
from __future__ import annotations

import hashlib

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone


class Command(BaseCommand):
    help = 'Link manual-MCF tracking to Active Walmart orders that are missing it.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from apps.walmart_mcf.models import (AmazonMCFOrder, AuditEvent,
                                             ShipmentPackage, WalmartOrder,
                                             WalmartOrderState as S)
        from apps.walmart_mcf.pipeline import CARRIER_MAP, _find_manual_mcf

        ARCHIVED_Q = (Q(status__in=[S.TRACKING_UPLOADED, S.COMPLETED])
                      & Q(mcf__packages__uploaded_to_walmart_at__isnull=False))
        active = (WalmartOrder.objects.exclude(ARCHIVED_Q)
                  .select_related('mcf').order_by('-order_date'))

        # MCF states that mean "still being fulfilled — no tracking yet".
        LIVE_MCF = {'planning', 'processing', 'acknowledged', 'new'}

        now = timezone.now()
        dry = opts['dry_run']
        res = {'active_scanned': 0, 'linked': 0, 'no_tracking_yet': 0,
               'reflowed': 0, 'tba_only': 0, 'no_mcf_match': 0,
               'already_linked': 0}

        for o in active:
            res['active_scanned'] += 1
            coid = str(getattr(o, 'customer_order_id', '') or '')
            manual = _find_manual_mcf(o.purchase_order_id, coid)
            if not manual:
                res['no_mcf_match'] += 1
                continue
            pkgs = [p for p in (manual.packages or []) if p.get('tracking')]
            real = [p for p in pkgs
                    if not str(p['tracking']).upper().startswith('TBA')]
            if not real:
                # No usable tracking yet. If this order is wrongly marked
                # COMPLETED while Amazon is still fulfilling it, link the MCF
                # and drop it back into the live pipeline (MCF_CREATED) so the
                # status/tracking cycle finishes it properly.
                res['tba_only' if pkgs else 'no_tracking_yet'] += 1
                if (o.status == S.COMPLETED and
                        str(manual.status or '').lower() in LIVE_MCF):
                    if dry:
                        res['reflowed'] += 1
                        continue
                    mcf = getattr(o, 'mcf', None)
                    if mcf is None:
                        mcf_id = manual.seller_order_id[:48]
                        ex = AmazonMCFOrder.objects.filter(
                            fulfillment_order_id=mcf_id).first()
                        if ex and ex.order_id != o.id:
                            mcf_id = f'MANUAL-{o.purchase_order_id}'[:48]
                        mcf, _ = AmazonMCFOrder.objects.get_or_create(
                            fulfillment_order_id=mcf_id,
                            defaults={'order': o,
                                      'amazon_status': manual.status or ''})
                        if mcf.order_id != o.id:
                            continue
                    WalmartOrder.objects.filter(pk=o.pk).update(
                        status=S.MCF_CREATED)
                    AuditEvent.objects.create(
                        order=o, from_state=S.COMPLETED,
                        to_state=S.MCF_CREATED, actor='reconcile_manual',
                        detail={'reason': 'premature COMPLETED — Amazon still '
                                          f'{manual.status}; back to pipeline',
                                'mcf': manual.seller_order_id})
                    res['reflowed'] += 1
                continue

            if dry:
                res['linked'] += 1
                continue

            # ensure the MCF link
            mcf = getattr(o, 'mcf', None)
            if mcf is None:
                mcf_id = manual.seller_order_id[:48]
                existing = AmazonMCFOrder.objects.filter(
                    fulfillment_order_id=mcf_id).first()
                if existing and existing.order_id != o.id:
                    mcf_id = f'MANUAL-{o.purchase_order_id}'[:48]
                mcf, _ = AmazonMCFOrder.objects.get_or_create(
                    fulfillment_order_id=mcf_id,
                    defaults={'order': o,
                              'amazon_status': manual.status or 'Manual'})
                if mcf.order_id != o.id:
                    res['no_mcf_match'] += 1
                    continue

            linked_here = 0
            for p in real:
                carrier = str(p.get('carrier') or '')
                tracking = str(p.get('tracking') or '')
                h = hashlib.sha1(
                    f'{o.purchase_order_id}|manual|{carrier}|{tracking}'
                    .encode()).hexdigest()
                sp, created = ShipmentPackage.objects.get_or_create(
                    upload_hash=h,
                    defaults={'mcf_order': mcf, 'carrier_code': carrier,
                              'carrier_walmart': CARRIER_MAP.get(
                                  carrier.upper(), carrier or 'Other'),
                              'tracking_number': tracking,
                              'uploaded_to_walmart_at': now,
                              'upload_error':
                                  'reconciled from manual MCF order'})
                if not created and sp.uploaded_to_walmart_at is None:
                    sp.uploaded_to_walmart_at = now
                    sp.save(update_fields=['uploaded_to_walmart_at'])
                linked_here += 1

            if linked_here:
                AuditEvent.objects.create(
                    order=o, from_state=o.status, to_state=o.status,
                    actor='reconcile_manual',
                    detail={'mcf': manual.seller_order_id,
                            'tracking': [p['tracking'] for p in real]})
                res['linked'] += 1

        self.stdout.write(str(res))
