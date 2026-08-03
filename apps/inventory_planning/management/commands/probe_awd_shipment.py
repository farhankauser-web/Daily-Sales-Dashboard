"""
Dump what Amazon actually returns for an AWD inbound shipment.

Deliberately does no parsing. Earlier today an AMS integration silently read
zeros for weeks because the code guessed `sales1d` where the payload said
`sales_1d` — a miss returns 0 rather than raising, so it looks like "no
sales" instead of "wrong key". Before any shipped-vs-received logic gets
written against these payloads, we look at the real field names.

    # one container by its STAR- id
    python manage.py probe_awd_shipment --shipment-id STAR-RXZVJV6CJ6ABY

    # or just list what AWD has, newest first
    python manage.py probe_awd_shipment --list --limit 5

    # every open container that has an id, with a quantity summary
    python manage.py probe_awd_shipment --open-containers
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError


def _walk_keys(obj, prefix='', out=None, depth=0):
    """Flatten key paths so nested quantity fields are easy to spot."""
    if out is None:
        out = []
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f'{prefix}.{k}' if prefix else k
            if isinstance(v, (dict, list)):
                _walk_keys(v, p, out, depth + 1)
            else:
                out.append((p, v))
    elif isinstance(obj, list) and obj:
        _walk_keys(obj[0], f'{prefix}[0]', out, depth + 1)
    return out


class Command(BaseCommand):
    help = ('Print the raw AWD inbound-shipment payload so field names can be '
            'confirmed before writing receipt logic against them.')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--shipment-id', default=None,
                            help='A STAR-… id (from the container).')
        parser.add_argument('--list', action='store_true',
                            help='List recent AWD inbound shipments instead.')
        parser.add_argument('--limit', type=int, default=5)
        parser.add_argument('--open-containers', action='store_true',
                            help='Probe every open container that has an id.')
        parser.add_argument('--raw', action='store_true',
                            help='Print full JSON, not just the key summary.')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import SPAPIClient

        cfg = AmazonAPIConfig.objects.filter(
            marketplace=opts['marketplace'], is_active=True).first()
        if not cfg or not cfg.has_sp_api_credentials():
            raise CommandError(f'no active SP-API config for {opts["marketplace"]}')
        client = SPAPIClient(cfg)

        if opts['list']:
            return self._list(client, opts)
        if opts['open_containers']:
            return self._open_containers(client, opts)
        if not opts['shipment_id']:
            raise CommandError('pass --shipment-id, --list or --open-containers')
        self._one(client, opts['shipment_id'], opts['raw'])

    # ─────────────────────────────────────────────────────────────────────
    def _list(self, client, opts):
        try:
            ships = client.get_awd_inbound_shipments(max_results=opts['limit'])
        except Exception as exc:
            return self._explain(exc)
        self.stdout.write(f'AWD inbound shipments returned: {len(ships)}\n')
        for s in ships[:opts['limit']]:
            self.stdout.write(json.dumps(s, indent=1, default=str)[:1200])
            self.stdout.write('-' * 60)

    def _one(self, client, shipment_id, raw):
        self.stdout.write(f'\n═══ {shipment_id} ═══')
        try:
            data = client.get_awd_inbound_shipment(shipment_id)
        except Exception as exc:
            return self._explain(exc)
        if raw:
            self.stdout.write(json.dumps(data, indent=1, default=str))
            return
        self.stdout.write('\nFLATTENED KEYS (path = value):')
        for path, val in _walk_keys(data):
            sval = str(val)
            self.stdout.write(f'   {path:<52} = {sval[:60]}')
        # Point at anything that smells like a quantity — those are the fields
        # the receipt logic will hang off.
        qty = [(p, v) for p, v in _walk_keys(data)
               if any(w in p.lower() for w in
                      ('quantity', 'qty', 'received', 'expected', 'shipped'))]
        if qty:
            self.stdout.write('\nQUANTITY-LIKE FIELDS:')
            for p, v in qty:
                self.stdout.write(self.style.SUCCESS(f'   {p:<52} = {v}'))
        else:
            self.stdout.write(self.style.WARNING(
                '\n⚠ no quantity-like fields — the per-SKU breakdown may need a '
                'different parameter, or this shipment has none yet.'))

    def _open_containers(self, client, opts):
        from apps.inventory_planning.models import InTransitShipment
        qs = (InTransitShipment.objects
              .exclude(status__in=['received', 'cancelled'])
              .exclude(shipment_id='')
              .order_by('eta_destination'))
        self.stdout.write(f'open containers with an Amazon id: {qs.count()}\n')
        for sh in qs:
            self.stdout.write(f'\n### {sh.container_no or sh.pk} '
                              f'({sh.status}, ETA {sh.eta_destination}) '
                              f'→ {sh.shipment_id}')
            self._one(client, sh.shipment_id, opts['raw'])

    def _explain(self, exc):
        msg = str(exc)
        if '403' in msg or 'Unauthorized' in msg or 'Access to requested' in msg:
            self.stderr.write(self.style.ERROR(
                '403 — the SP-API role lacks Amazon Warehousing & Distribution, '
                'or inbound shipments need a separate grant from inventory. '
                'Add the role in Seller Central → Apps & Services → Develop '
                'Apps, then re-authorise.'))
        elif '404' in msg:
            self.stderr.write(self.style.ERROR(
                '404 — no such shipment id in this marketplace. Check the '
                'STAR- id, and that it belongs to this seller account.'))
        else:
            self.stderr.write(self.style.ERROR(f'{type(exc).__name__}: {msg[:300]}'))
