"""
Pull FBA + AWD stock into WarehouseStock for the Inventory Planner.
3PL/factory stock stays manual (Excel import on the page).

    python manage.py sync_planning_inventory [--region usa]
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand
from django.utils import timezone

# Amazon auto-generated SKU alias (e.g. "0P-F6DW-OJUD") — a placeholder listing
# Amazon mints for the same ASIN; never our real SKU, usually zero stock.
_AUTO_SKU = re.compile(r'^[0-9A-Z]{2}-[0-9A-Z]{4}-[0-9A-Z]{4}$')


def _norm_sku(s: str) -> str:
    """Normalize a seller SKU: drop the non-breaking spaces and stray quotes
    Amazon sometimes prepends (they create phantom duplicate rows)."""
    return (s or '').replace('\xa0', ' ').replace('"', '').strip().upper()


def _is_new(condition) -> bool:
    """Only brand-new stock counts for replenishment planning. Everything
    graded on a return (UsedLikeNew/VeryGood/Good/Poor/Acceptable) is a
    separate, non-manufactured pool we don't reorder against."""
    return str(condition or '').lower().startswith('new')


def _is_amazon_alias(sku: str) -> bool:
    return sku.startswith('AMZN.') or bool(_AUTO_SKU.match(sku))


def sync_region(region: str) -> dict:
    from apps.amazon_api.models import AmazonAPIConfig
    from apps.amazon_api.services import SPAPIClient
    from apps.inventory_planning.models import Warehouse, WarehouseStock

    cfg = AmazonAPIConfig.objects.filter(marketplace=region,
                                         is_active=True).first()
    if not cfg:
        return {'region': region, 'error': 'no active SP-API config'}
    client = SPAPIClient(cfg)
    now = timezone.now()
    res = {'region': region, 'fba_skus': 0, 'awd_skus': 0, 'awd_error': '',
           'skipped_used': 0, 'skipped_alias': 0, 'raw_summaries': 0}

    # ── FBA — new condition only, deduped by normalized SKU ──
    fba, _ = Warehouse.objects.get_or_create(
        code=f'FBA-{region.upper()}',
        defaults={'name': f'Amazon FBA {region.upper()}',
                  'region': region, 'kind': 'fba'})
    fresh: dict[str, dict] = {}
    for s in client.get_fba_inventory_summaries_all():
        res['raw_summaries'] += 1
        if not _is_new(s.get('condition')):          # used/graded return
            res['skipped_used'] += 1
            continue
        sku = _norm_sku(s.get('sellerSku'))
        if not sku:
            continue
        if _is_amazon_alias(sku):                     # phantom Amazon alias
            res['skipped_alias'] += 1
            continue
        d = s.get('inventoryDetails') or {}
        available = int(d.get('fulfillableQuantity') or 0)
        inbound = (int(d.get('inboundWorkingQuantity') or 0)
                   + int(d.get('inboundShippedQuantity') or 0)
                   + int(d.get('inboundReceivingQuantity') or 0))
        reserved = int((d.get('reservedQuantity') or {})
                       .get('totalReservedQuantity') or 0)
        total = available + inbound + reserved
        prev = fresh.get(sku)
        # nbsp/quote variants of one SKU report the SAME physical stock — keep
        # the larger, never sum (summing would double-count).
        if prev is None or total > prev['units']:
            fresh[sku] = {'units': total, 'available': available,
                          'inbound': inbound, 'reserved': reserved}

    # replace the region's FBA snapshot wholesale (all API-sourced, so old
    # used-condition / duplicate rows from earlier runs are cleared out)
    WarehouseStock.objects.filter(warehouse=fba).delete()
    for sku, v in fresh.items():
        WarehouseStock.objects.create(
            warehouse=fba, sku=sku, units=v['units'],
            detail={'available': v['available'], 'inbound': v['inbound'],
                    'reserved': v['reserved']},
            as_of=now, source='api')
    res['fba_skus'] = len(fresh)

    # ── AWD — USA only. Amazon Warehousing and Distribution does not exist in
    # the UK/AE/SA marketplaces, so calling there just earns a 403 we then have
    # to explain away in the log. Skipping outright is both faster and honest.
    if region != 'usa':
        return res

    awd, _ = Warehouse.objects.get_or_create(
        code=f'AWD-{region.upper()}',
        defaults={'name': f'Amazon AWD {region.upper()}',
                  'region': region, 'kind': 'awd'})
    try:
        # Collected first, then written in one replace — same as FBA above.
        # update_or_create per SKU left a row behind forever once Amazon
        # stopped reporting a SKU, so a discontinued line kept showing stock
        # it no longer had.
        awd_fresh: dict[str, dict] = {}
        for item in client.get_awd_inventory_all():
            sku = _norm_sku(item.get('sku'))
            if not sku or _is_amazon_alias(sku):
                continue
            awd_fresh[sku] = {
                'units': int(item.get('totalOnhandQuantity') or 0),
                'inbound': int(item.get('totalInboundQuantity') or 0),
            }
        # Only replace once the call has fully succeeded — a mid-pagination
        # failure must not leave the region with a half-empty AWD snapshot.
        WarehouseStock.objects.filter(warehouse=awd).delete()
        for sku, v in awd_fresh.items():
            WarehouseStock.objects.create(
                warehouse=awd, sku=sku, units=v['units'],
                detail={'inbound_to_awd': v['inbound']},
                as_of=now, source='api')
            res['awd_skus'] += 1
    except Exception as exc:
        msg = str(exc)
        if '403' in msg or 'Unauthorized' in msg or 'Access to requested' in msg:
            res['awd_error'] = ('AWD access not yet authorized (403). Add the '
                                '"Amazon Warehousing and Distribution" role to '
                                'the SP-API app, then RE-AUTHORIZE the seller so '
                                'a new refresh token includes it. Until then AWD '
                                'stock comes from the uploaded file.')
        else:
            res['awd_error'] = f'{type(exc).__name__}: {msg}'[:200]
    return res


class Command(BaseCommand):
    help = 'Sync FBA + AWD inventory into the Inventory Planner'

    def add_arguments(self, parser):
        parser.add_argument('--region', default='usa')
        parser.add_argument('--all', action='store_true',
                            help='Every configured marketplace in turn.')

    def handle(self, *args, **opts):
        import fcntl
        import os
        import tempfile

        # Single instance. sync_region DELETES a region's FBA rows before
        # rewriting them, so two runs — or a run racing the "⟳ Refresh Amazon
        # Stock" button — can leave the planner reading zero stock for every
        # SKU mid-window. Cheap lock, expensive failure.
        lock_path = os.path.join(tempfile.gettempdir(),
                                 'ix_sync_planning_inventory.lock')
        with open(lock_path, 'w') as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stdout.write('another sync is already running — skipping.')
                return
            self._run(opts)

    def _run(self, opts):
        from apps.amazon_api.models import AmazonAPIConfig
        if opts['all']:
            regions = list(AmazonAPIConfig.objects
                           .filter(is_active=True)
                           .values_list('marketplace', flat=True))
        else:
            regions = [opts['region']]
        for r in regions:
            res = sync_region(r)
            self.stdout.write(str(res))
