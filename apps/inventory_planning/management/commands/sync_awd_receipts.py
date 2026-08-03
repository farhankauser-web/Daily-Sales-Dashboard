"""
Pull Amazon's receipt figures for containers linked to an AWD inbound shipment.

Field names below were confirmed against a live payload (probe_awd_shipment)
rather than guessed:

    shipmentStatus                                     RECEIVING | CLOSED | …
    shipmentSkuQuantities[].sku
    shipmentSkuQuantities[].expectedQuantity.quantity  in CASES
    shipmentSkuQuantities[].receivedQuantity.quantity  in CASES
    shipmentContainerQuantities[].distributionPackage
        .contents.products[].{sku,quantity}            eaches per case

Amazon counts CASES; we ship EACHES. A container showing "expected 16" is 16
cases of 24 = 384 eaches against a packing list of 13,128 — comparing the raw
numbers would read as a catastrophic shortage. Everything here is converted to
eaches before it is stored or compared.

Where Amazon's case-pack disagrees with ours, Amazon wins: their count is what
can actually be sold. The difference still lands in the variance, but the
report separates a genuine shortage (fewer cases arrived) from a pack-size
disagreement (same cases, different units each), because the remedies differ —
a claim versus a setup fix.

Reports by default; --apply writes. Stage transitions are opt-in via
--advance-status, since moving a container is an ops decision.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


def _qty(node):
    """{'quantity': N, 'unitOfMeasurement': 'CASES'} → (N, 'CASES')."""
    if not isinstance(node, dict):
        return 0, ''
    return int(node.get('quantity') or 0), str(node.get('unitOfMeasurement') or '')


def case_packs(payload) -> dict:
    """
    {sku: eaches-per-case} from the container manifest.

    Amazon repeats the pack in every distributionPackage; the largest wins if
    they ever differ, since a smaller one usually means a partial carton.
    """
    out = {}
    for cq in (payload.get('shipmentContainerQuantities') or []):
        pkg = (cq.get('distributionPackage') or {})
        for p in ((pkg.get('contents') or {}).get('products') or []):
            sku = str(p.get('sku') or '').strip().upper()
            q = int(p.get('quantity') or 0)
            if sku and q > 0:
                out[sku] = max(out.get(sku, 0), q)
    return out


def parse_shipment(payload) -> dict:
    """Amazon payload → {status, updated_at, lines:{sku: {...eaches...}}}."""
    packs = case_packs(payload)
    lines = {}
    for sq in (payload.get('shipmentSkuQuantities') or []):
        sku = str(sq.get('sku') or '').strip().upper()
        if not sku:
            continue
        exp_c, exp_u = _qty(sq.get('expectedQuantity'))
        rcv_c, _     = _qty(sq.get('receivedQuantity'))
        per = packs.get(sku, 0)
        # Trust the stated unit: if Amazon ever reports EACHES, don't multiply.
        if exp_u.upper().startswith('CASE') and per > 0:
            exp_e, rcv_e = exp_c * per, rcv_c * per
        else:
            exp_e, rcv_e, per = exp_c, rcv_c, per or 1
        lines[sku] = {'expected_cases': exp_c, 'received_cases': rcv_c,
                      'units_per_case': per,
                      'expected_units': exp_e, 'received_units': rcv_e}
    return {'status': str(payload.get('shipmentStatus') or ''),
            'updated_at': payload.get('updatedAt'),
            'lines': lines}


class Command(BaseCommand):
    help = ("Sync Amazon's expected/received quantities onto container lines.")

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--container', default=None,
                            help='Single container number, else all open ones.')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--advance-status', action='store_true',
                            help='Also move in_transit→receiving on first '
                                 'receipt, and →received when Amazon CLOSES.')

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
        qs = qs.order_by('eta_destination')
        if not qs.exists():
            self.stdout.write('no containers with an Amazon shipment ID to sync.')
            return

        now = timezone.now()
        for sh in qs:
            ref = sh.container_no or f'#{sh.pk}'
            try:
                payload = client.get_awd_inbound_shipment(sh.shipment_id)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(
                    f'{ref} ({sh.shipment_id}): {type(exc).__name__}: {str(exc)[:140]}'))
                continue
            data = parse_shipment(payload)
            self._report(sh, ref, data, opts, now)

    # ─────────────────────────────────────────────────────────────────────
    def _report(self, sh, ref, data, opts, now):
        lines = {l.sku.upper(): l for l in sh.lines.all()}
        amz = data['lines']
        packed = sum(int(l.units or 0) for l in lines.values())
        exp = sum(v['expected_units'] for v in amz.values())
        rcv = sum(v['received_units'] for v in amz.values())

        self.stdout.write(
            f'\n═══ {ref}  ({sh.status} → Amazon: {data["status"] or "?"})  '
            f'{sh.shipment_id}')
        self.stdout.write(
            f'    packed(B)={packed:>8,}   declared(A)={exp:>8,}   '
            f'received(C)={rcv:>8,}   variance(B−C)={packed - rcv:>+8,}')

        # Nothing counted yet ⇒ still on the water, not a loss. Saying
        # "variance +12,146" for a container Amazon has only CREATED would
        # read as a total write-off.
        if rcv == 0 and data['status'].upper() in ('CREATED', 'SHIPPED', ''):
            self.stdout.write(self.style.NOTICE(
                '      not yet received — in transit, no variance to assess'))
            return self._maybe_write(sh, ref, data, amz, lines, opts, now)

        # Shortfall is packed-minus-received PER SKU. It must never be derived
        # from expectedQuantity: Amazon reconciles against what we declared
        # (A), and A ≥ B always, so an A-based figure invents a shortage out
        # of our own over-declaration and would trigger a bogus claim.
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
            line.units_per_case = v['units_per_case']
            line.save(update_fields=['amazon_expected_units',
                                     'amazon_received_units', 'units_per_case'])
            n += 1
        sh.amazon_status = data['status'][:32]
        sh.amazon_synced_at = now
        fields = ['amazon_status', 'amazon_synced_at']
        if data['updated_at']:
            from django.utils.dateparse import parse_datetime
            dt = parse_datetime(str(data['updated_at']))
            if dt:
                sh.amazon_updated_at = dt
                fields.append('amazon_updated_at')

        if opts['advance_status']:
            st = data['status'].upper()
            # RECEIVING = intake started, so it is no longer in transit.
            if rcv > 0 and sh.status not in ('receiving', 'received', 'cancelled'):
                sh.status = 'receiving' if 'receiving' in dict(sh.STATUSES) else sh.status
                fields.append('status')
            # Only Amazon CLOSING it ends the container.
            if st == 'CLOSED' and sh.status != 'received':
                sh.status = 'received'
                sh.received_date = sh.received_date or timezone.localdate()
                sh.received_at = sh.received_at or now
                fields += ['status', 'received_date', 'received_at']
        sh.save(update_fields=list(dict.fromkeys(fields)))
        self.stdout.write(self.style.SUCCESS(f'      ✓ {n} line(s) updated'))
