"""
Detect containers Amazon has already received, so they stop being counted
twice.

The planner adds warehouse on-hand to every InTransitShipment that isn't
`received`/`cancelled`. Once a container physically lands, Amazon reports its
cartons as on-hand — but the shipment stays open in Pulse until somebody
receives it in Goods Receipt, and until then the same units are counted in
both places. Cover reads high and reorder suggestions get suppressed exactly
when they shouldn't be.

Amazon tells us when this has happened. For AWD it publishes
`totalInboundQuantity` alongside `totalOnhandQuantity`: while a shipment is
genuinely inbound the former is non-zero, and once received it drops to zero
with the units showing up on hand. For FC-bound cartons the equivalent signal
is `inboundReceivingQuantity` on the FBA summary.

Reports by default; only writes with --apply. Closing a container is an
accounting act (any shipped-minus-received difference books as shortage), so
the default is deliberately read-only.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone


class Command(BaseCommand):
    help = ('Flag (or close) in-transit containers Amazon already shows as '
            'received, which would otherwise be double-counted against stock.')

    def add_arguments(self, parser):
        parser.add_argument('--region', default='usa')
        parser.add_argument('--grace-days', type=int, default=3,
                            help='Only consider shipments this many days past '
                                 'ETA (default 3) — avoids acting on cartons '
                                 'still being booked in.')
        parser.add_argument('--min-confidence', type=float, default=0.6,
                            help='Fraction of a container\'s SKUs that must '
                                 'look received before flagging (default 0.6).')
        parser.add_argument('--apply', action='store_true',
                            help='Actually mark matches as received. Without '
                                 'this the command only reports.')

    def handle(self, *args, **opts):
        from apps.inventory_planning.models import (InTransitShipment,
                                                    InTransitLine,
                                                    WarehouseStock)
        region = opts['region']
        today = timezone.localdate()
        cutoff = today - timedelta(days=opts['grace_days'])

        # Amazon's current picture, per SKU.
        onhand, inbound = {}, {}
        for w in (WarehouseStock.objects.select_related('warehouse')
                  .filter(warehouse__region=region, warehouse__is_active=True)):
            sku = (w.sku or '').upper()
            if not sku:
                continue
            d = w.detail if isinstance(w.detail, dict) else {}
            onhand[sku] = onhand.get(sku, 0) + int(w.units or 0)
            inbound[sku] = inbound.get(sku, 0) + int(
                d.get('inbound_to_awd') or d.get('inbound') or 0)

        shipments = (InTransitShipment.objects
                     .filter(region=region)
                     .exclude(status__in=['received', 'cancelled'])
                     .select_related('destination')
                     .order_by('eta_destination'))

        flagged, total_units = [], 0
        for sh in shipments:
            eta = sh.eta_destination or sh.eta_port
            if not eta or eta > cutoff:
                continue                      # still plausibly on the water
            lines = list(InTransitLine.objects.filter(shipment=sh))
            if not lines:
                continue
            units = sum(int(l.units or 0) for l in lines)

            # A SKU "looks received" when Amazon reports no inbound for it but
            # does hold stock — the exact fingerprint of a landed container.
            looks_received = 0
            for l in lines:
                sku = (l.sku or '').upper()
                if sku not in onhand:
                    continue                  # SKU absent from the feed — no evidence
                if inbound.get(sku, 0) == 0 and onhand.get(sku, 0) > 0:
                    looks_received += 1
            known = sum(1 for l in lines if (l.sku or '').upper() in onhand)
            if not known:
                continue
            confidence = looks_received / known
            if confidence < opts['min_confidence']:
                continue
            flagged.append((sh, units, confidence, known, len(lines)))
            total_units += units

        if not flagged:
            self.stdout.write(self.style.SUCCESS(
                f'✅ [{region}] no open container looks already-received.'))
            return

        self.stdout.write(
            f'\n⚠  [{region}] {len(flagged)} container(s) look RECEIVED but are '
            f'still open — {total_units:,} units double-counted against stock:\n')
        self.stdout.write(f'{"container":<18}{"status":<16}{"ETA":<12}'
                          f'{"units":>9}{"confidence":>12}')
        for sh, units, conf, known, n in flagged:
            ref = sh.container_no or sh.shipment_id or f'#{sh.pk}'
            eta = sh.eta_destination or sh.eta_port
            self.stdout.write(f'{ref:<18}{sh.status:<16}{str(eta):<12}'
                              f'{units:>9,}{conf*100:>11.0f}%'
                              f'   ({known}/{n} SKUs matched)')

        if not opts['apply']:
            self.stdout.write(self.style.WARNING(
                '\nReport only. Receive these in Goods Receipt to reconcile '
                'units properly, or re-run with --apply to close them as-is '
                '(shipped-minus-received books as shortage).'))
            return

        now = timezone.now()
        for sh, units, *_ in flagged:
            sh.status = 'received'
            sh.received_date = sh.received_date or today
            sh.received_at = sh.received_at or now
            sh.notes = (sh.notes or '')[:180] + ' | auto-received: Amazon shows on-hand, no inbound'
            sh.save(update_fields=['status', 'received_date', 'received_at', 'notes'])
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Closed {len(flagged)} container(s); {total_units:,} units no '
            f'longer double-counted.'))
