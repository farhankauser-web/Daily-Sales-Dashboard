"""
Pull Amazon's receipt figures for containers sent straight to a fulfilment
centre — the FBA counterpart of sync_awd_receipts.

Why a second command at all: an AWD container carries a STAR-… id and lives in
/awd/2024-05-09/, an FC container carries an FBA… id and lives in
/fba/inbound/v0/. The ids do not resolve across APIs. Without this, an FC
container never produces a receipt: its units stay "in transit" indefinitely,
the planner keeps counting them as inbound, and it never reaches the Receiving
stage.

THE UNIT DIFFERENCE, which is the thing most likely to cause a silent disaster
here: Fulfillment Inbound v0 reports EACHES. AWD reports CASES. sync_awd_receipts
multiplies by the case pack; this command must NOT, or a 1,440-unit line would
be read as 34,560 and the container would look massively over-received. There
is no case-pack conversion anywhere below, deliberately.

    ItemData[].SellerSKU
    ItemData[].QuantityShipped     what we declared to Amazon   (A)
    ItemData[].QuantityReceived    what Amazon has counted in   (C)
    ShipmentData[0].ShipmentStatus WORKING|SHIPPED|RECEIVING|CLOSED|CANCELLED

Variance stays packed − received (B − C), never declared − received (A − C),
for the same reason as the AWD path: we always declare at least as much as we
pack, so an A-based figure invents a shortage out of our own over-declaration.

Reports by default; --apply writes. Stage transitions are opt-in via
--advance-status, since moving a container is an ops decision.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

# Amazon statuses that mean the shipment is finished being counted.
CLOSED_STATUSES = {'CLOSED'}
# Dead ends — never advance a container into 'received' off these.
DEAD_STATUSES = {'CANCELLED', 'DELETED', 'ERROR'}


def parse_items(items, status: str) -> dict:
    """ItemData[] → {status, lines:{SKU: {expected_units, received_units}}}.

    Both figures are already eaches; nothing is scaled.
    """
    lines = {}
    for it in (items or []):
        sku = str(it.get('SellerSKU') or '').strip().upper()
        if not sku:
            continue
        e = int(it.get('QuantityShipped') or 0)
        c = int(it.get('QuantityReceived') or 0)
        prev = lines.get(sku)
        if prev:                       # same SKU listed twice — sum, not replace
            prev['expected_units'] += e
            prev['received_units'] += c
        else:
            lines[sku] = {'expected_units': e, 'received_units': c}
    return {'status': (status or '').upper(), 'lines': lines}


class Command(BaseCommand):
    help = 'Sync FC (FBA) container receipts from the Fulfillment Inbound API.'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--container', default=None,
                            help='Single container number, else all open ones.')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--advance-status', action='store_true',
                            help='Also move →receiving on first receipt, and '
                                 '→received when Amazon CLOSES the shipment.')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import SPAPIClient
        from apps.inventory_planning.models import InTransitShipment

        cfg = AmazonAPIConfig.objects.filter(
            marketplace=opts['marketplace'], is_active=True).first()
        if not cfg or not cfg.has_sp_api_credentials():
            raise CommandError(f'no active SP-API config for {opts["marketplace"]}')
        client = SPAPIClient(cfg)

        qs = InTransitShipment.objects.exclude(shipment_id='')
        if opts['container']:
            qs = qs.filter(container_no__iexact=opts['container'])
        else:
            qs = qs.exclude(status__in=['received', 'cancelled'])
        # AWD ids are STAR-…; anything else is treated as an FBA shipment id.
        # Matching on the prefix rather than the destination warehouse means a
        # container whose destination was never set still gets synced.
        qs = [s for s in qs.order_by('eta_destination')
              if not s.shipment_id.upper().startswith('STAR-')]
        if not qs:
            self.stdout.write('no FC containers with an FBA shipment ID to sync.')
            return

        now = timezone.now()
        for sh in qs:
            ref = sh.container_no or f'#{sh.pk}'
            try:
                header = client.get_fba_inbound_shipment(sh.shipment_id)
                if not header:
                    self.stdout.write(self.style.WARNING(
                        f'{ref} ({sh.shipment_id}): Amazon knows no such FBA '
                        f'shipment — check the ID.'))
                    continue
                items = client.get_fba_inbound_shipment_items(sh.shipment_id)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(
                    f'{ref} ({sh.shipment_id}): {type(exc).__name__}: '
                    f'{str(exc)[:140]}'))
                continue
            data = parse_items(items, header.get('ShipmentStatus'))
            self._report(sh, ref, data, opts, now)

    # ─────────────────────────────────────────────────────────────────────
    def _report(self, sh, ref, data, opts, now):
        lines = {l.sku.upper(): l for l in sh.lines.all()}
        amz = data['lines']
        packed = sum(int(l.units or 0) for l in lines.values())     # B
        exp = sum(v['expected_units'] for v in amz.values())        # A
        rcv = sum(v['received_units'] for v in amz.values())        # C

        self.stdout.write(
            f'{ref} ({sh.shipment_id})  {data["status"] or "—"}  '
            f'packed={packed:,}  declared={exp:,}  received={rcv:,}')

        if rcv == 0:
            self.stdout.write('      not yet received — nothing to reconcile')

        short = over = 0
        for sku, v in amz.items():
            line = lines.get(sku)
            if not line:
                continue
            b, c = int(line.units or 0), v['received_units']
            short += max(0, b - c)
            over += max(0, c - b)
        declared_gap = exp - packed          # A − B, informational only
        bits = []
        if short:
            bits.append(f'shortfall(B−C)={short:>+7,}')
        if over:
            bits.append(f'over-received={over:>+7,}')
        if declared_gap:
            bits.append(f'over-declared(A−B)={declared_gap:>+7,} (not a loss)')
        if bits:
            self.stdout.write('      ' + '   '.join(bits))

        missing = [s for s in amz if s not in lines]
        if missing:
            self.stdout.write(self.style.WARNING(
                f'      {len(missing)} SKU(s) Amazon lists that our packing '
                f'list does not: {", ".join(sorted(missing)[:5])}'))

        return self._maybe_write(sh, ref, data, amz, lines, opts, now)

    def _maybe_write(self, sh, ref, data, amz, lines, opts, now):
        if not opts['apply']:
            return
        rcv = sum(v['received_units'] for v in amz.values())

        n = 0
        for sku, v in amz.items():
            line = lines.get(sku)
            if not line:
                continue
            line.amazon_expected_units = v['expected_units']
            line.amazon_received_units = v['received_units']
            # Deliberately not touching units_per_case: these are eaches, so
            # there is no case pack to learn, and writing one would corrupt the
            # figure for anything that reads it.
            line.save(update_fields=['amazon_expected_units',
                                     'amazon_received_units'])
            n += 1

        sh.amazon_status = data['status'][:32]
        sh.amazon_synced_at = now
        fields = ['amazon_status', 'amazon_synced_at']

        if opts['advance_status']:
            st = data['status']
            if st in DEAD_STATUSES:
                self.stdout.write(self.style.WARNING(
                    f'      {st} — left where it is, not advanced'))
            else:
                if rcv > 0 and sh.status not in ('receiving', 'received',
                                                 'cancelled'):
                    if 'receiving' in dict(sh.STATUSES):
                        sh.status = 'receiving'
                        fields.append('status')
                if st in CLOSED_STATUSES and sh.status != 'received':
                    sh.status = 'received'
                    sh.received_date = sh.received_date or timezone.localdate()
                    sh.received_at = sh.received_at or now
                    fields += ['status', 'received_date', 'received_at']

        sh.save(update_fields=list(dict.fromkeys(fields)))
        self.stdout.write(self.style.SUCCESS(f'      ✓ {n} line(s) updated'))
