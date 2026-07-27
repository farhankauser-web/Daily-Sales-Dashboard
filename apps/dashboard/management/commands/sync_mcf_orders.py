"""
Sync Multi-Channel Fulfillment (MCF) orders + tracking numbers from the
SP-API Fulfillment Outbound API into McfOrder.

Detail calls are skipped for orders already stored as Complete/Cancelled WITH
tracking — so re-runs only hit Amazon for new/in-flight orders.

Usage:
    manage.py sync_mcf_orders --marketplace usa --days 30
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone as tz

from django.core.management.base import BaseCommand

TERMINAL = {'Complete', 'CompletePartialled', 'Cancelled', 'Unfulfillable'}


def sync_mcf(marketplace: str, days: int, stdout=None) -> dict:
    """Importable core so the UI sync endpoint shares this logic."""
    from apps.amazon_api.models import AmazonAPIConfig
    from apps.amazon_api.services import SPAPIClient
    from apps.dashboard.models import McfOrder

    cfg = AmazonAPIConfig.objects.filter(
        marketplace=marketplace, is_active=True).first()
    if not cfg:
        return {'status': 'failed', 'error': f'no active config for {marketplace}'}
    client = SPAPIClient(cfg)

    start_iso = (datetime.now(tz.utc) - timedelta(days=days)
                 ).strftime('%Y-%m-%dT%H:%M:%SZ')
    listed = client.list_mcf_orders(start_iso)

    existing = {o.seller_order_id: o for o in McfOrder.objects.filter(
        marketplace=marketplace)}

    n_new = n_updated = n_skipped = 0
    for lo in listed:
        oid = lo.get('sellerFulfillmentOrderId') or ''
        if not oid:
            continue
        status = lo.get('fulfillmentOrderStatus') or ''
        prev = existing.get(oid)
        # Terminal + tracking already stored → nothing can change; skip detail
        if prev and prev.status in TERMINAL and prev.packages:
            n_skipped += 1
            continue

        detail = client.get_mcf_order(oid)
        time.sleep(0.55)                       # 2 req/s limit
        fo    = detail.get('fulfillmentOrder') or lo
        items = detail.get('fulfillmentOrderItems') or []
        ships = detail.get('fulfillmentShipments') or []

        packages = []
        for s in ships:
            for pk in (s.get('fulfillmentShipmentPackage') or []):
                packages.append({
                    'carrier':        pk.get('carrierCode') or '',
                    'tracking':       pk.get('trackingNumber') or '',
                    'ship_date':      str(s.get('shippingDate') or '')[:10],
                    'eta':            str(pk.get('estimatedArrivalDate')
                                          or s.get('estimatedArrivalDate') or '')[:10],
                    'package_number': pk.get('packageNumber'),
                })
        dest = fo.get('destinationAddress') or {}
        item_list = [{'sku': i.get('sellerSku') or '',
                       'qty': int(i.get('quantity') or 0)} for i in items]

        obj, created = McfOrder.objects.update_or_create(
            marketplace=marketplace, seller_order_id=oid,
            defaults={
                'displayable_order_id': fo.get('displayableOrderId') or '',
                'status':               fo.get('fulfillmentOrderStatus') or status,
                'received_date':        fo.get('receivedDate') or None,
                'recipient_name':       (dest.get('name') or '')[:128],
                'city':                 (dest.get('city') or '')[:64],
                'state':                (dest.get('stateOrRegion') or '')[:32],
                'units':                sum(i['qty'] for i in item_list),
                'items':                item_list,
                'packages':             packages,
            })
        if created:
            n_new += 1
        else:
            n_updated += 1
        if stdout:
            stdout.write(f'  {"+" if created else "~"} {oid}  {obj.status}  '
                          f'pkgs={len(packages)}')

    return {'status': 'ok', 'listed': len(listed), 'new': n_new,
            'updated': n_updated, 'skipped_terminal': n_skipped}


class Command(BaseCommand):
    help = 'Sync MCF orders + tracking numbers from SP-API.'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--days', type=int, default=30)

    def handle(self, marketplace, days, **_):
        res = sync_mcf(marketplace, days, stdout=self.stdout)
        if res['status'] != 'ok':
            self.stderr.write(self.style.ERROR(str(res)))
            return
        self.stdout.write(self.style.SUCCESS(
            f'DONE listed={res["listed"]} new={res["new"]} '
            f'updated={res["updated"]} skipped={res["skipped_terminal"]}'))
