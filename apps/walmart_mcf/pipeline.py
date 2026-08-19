"""
Walmart → MCF pipeline. Five entry points, each run by its own cron
command under a job_lock:

    import_orders()    every 5 min   Walmart released orders → NEW (+ack)
    submit_orders()    every 10 min  NEW/HOLD → validate → MCF create
    check_status()     every 15 min  poll Amazon fulfillment status
    upload_tracking()  every 15 min  new packages → Walmart shipping update
    reconcile()        nightly       cross-check + stuck-order alerts

Every function is safe to re-run at any time (idempotent); see models.py
docstring for the uniqueness guarantees.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone as tz

from django.conf import settings
from django.utils import timezone

from .core import FatalAPIError, log_error, notify_admin
from .models import (AmazonMCFOrder, ShipmentPackage, SkuMapping,
                     WalmartOrder, WalmartOrderItem, WalmartOrderState as S)
from .state import transition
from .walmart_client import WalmartClient

logger = logging.getLogger(__name__)

# Walmart methodCode → Amazon shippingSpeedCategory
SPEED_MAP = {
    'Standard': 'Standard', 'Value': 'Standard', 'Freight': 'Standard',
    'Express': 'Expedited', 'Expedited': 'Expedited',
    'OneDay': 'Priority', 'Rush': 'Priority', 'NextDay': 'Priority',
}
# Amazon carrierCode → Walmart carrier enum (fallback: pass through)
CARRIER_MAP = {
    'UPS': 'UPS', 'UPSM': 'UPS', 'UPSMI': 'UPS',
    'USPS': 'USPS', 'FEDEX': 'FedEx', 'FDX': 'FedEx',
    'DHL': 'DHL', 'ONTRAC': 'OnTrac', 'LASERSHIP': 'LaserShip',
    'AMZL': 'AMZL', 'AMZN_US': 'AMZL',
}
AMAZON_OPEN_STATUSES = {'RECEIVED', 'PLANNING', 'PROCESSING', 'NEW'}
AMAZON_DONE_STATUSES = {'COMPLETE', 'COMPLETEPARTIALLED'}
AMAZON_CANCEL_STATUSES = {'CANCELLED', 'UNFULFILLABLE', 'INVALID'}


def _mcf_client():
    from apps.amazon_api.models import AmazonAPIConfig
    from apps.amazon_api.services import SPAPIClient
    mp = settings.WALMART_MCF_MARKETPLACE
    cfg = AmazonAPIConfig.objects.filter(marketplace=mp, is_active=True).first()
    if not cfg:
        raise RuntimeError(f'No active AmazonAPIConfig for {mp}')
    return SPAPIClient(cfg)


def _feature_constraints() -> list[dict]:
    return [{'featureName': name, 'featureFulfillmentPolicy': policy}
            for name, policy in settings.WALMART_MCF_FEATURES.items()
            if policy in ('Required', 'NotRequired')]


# ── Step 1: import ───────────────────────────────────────────────────────────

def _line_statuses(raw: dict) -> set[str]:
    out = set()
    for ln in ((raw.get('orderLines') or {}).get('orderLine')) or []:
        for st in ((ln.get('orderLineStatuses') or {})
                   .get('orderLineStatus')) or []:
            out.add(str(st.get('status') or ''))
    return out


def import_orders(days_back: int = 7) -> dict:
    """
    Fetch Walmart orders that still need fulfillment and insert new ones.

    Sources: /orders/released (unacknowledged) PLUS /orders?status=Created
    and status=Acknowledged — sellers whose current tooling auto-acknowledges
    orders never see them in /released, but Acknowledged-unshipped orders
    are exactly the fulfillment backlog. Fully Shipped/Delivered/Cancelled
    orders are skipped. Acknowledge is only called for Created lines.
    """
    wc = WalmartClient()
    since = (datetime.now(tz.utc) - timedelta(days=days_back)) \
        .strftime('%Y-%m-%dT%H:%M:%SZ')
    by_po: dict[str, dict] = {}
    for raw in wc.get_released_orders(since):
        po = str(raw.get('purchaseOrderId') or '').strip()
        if po:
            by_po.setdefault(po, raw)
    for status in ('Created', 'Acknowledged'):
        for raw in wc.get_all_orders(since, status=status):
            po = str(raw.get('purchaseOrderId') or '').strip()
            if po:
                by_po.setdefault(po, raw)
    raw_orders = list(by_po.values())
    imported, skipped, acked, errors, already_done = 0, 0, 0, 0, 0

    for raw in raw_orders:
        po_id = str(raw.get('purchaseOrderId') or '').strip()
        if not po_id:
            continue
        if WalmartOrder.objects.filter(purchase_order_id=po_id).exists():
            skipped += 1
            continue
        statuses = _line_statuses(raw)
        if statuses and not (statuses & {'Created', 'Acknowledged'}):
            already_done += 1               # fully shipped/delivered/cancelled
            continue
        try:
            ship = raw.get('shippingInfo') or {}
            addr = ship.get('postalAddress') or {}
            odate = raw.get('orderDate')
            if isinstance(odate, (int, float)):        # epoch ms
                order_dt = datetime.fromtimestamp(odate / 1000, tz.utc)
            else:
                order_dt = datetime.fromisoformat(
                    str(odate).replace('Z', '+00:00'))
            order = WalmartOrder.objects.create(
                purchase_order_id=po_id,
                customer_order_id=str(raw.get('customerOrderId') or ''),
                marketplace=settings.WALMART_MCF_MARKETPLACE,
                order_date=order_dt,
                customer_name=str(addr.get('name') or '')[:128],
                phone=str(ship.get('phone') or '')[:32],
                shipping_address=addr,
                shipping_method=str(ship.get('methodCode') or 'Standard'),
                raw_order=raw,
            )
            lines = ((raw.get('orderLines') or {}).get('orderLine')) or []
            for ln in lines:
                item = ln.get('item') or {}
                qty = int(((ln.get('orderLineQuantity') or {}).get('amount')) or 1)
                charges = ((ln.get('charges') or {}).get('charge')) or []
                price = 0
                for ch in charges:
                    if ch.get('chargeType') == 'PRODUCT':
                        price = float(((ch.get('chargeAmount') or {})
                                       .get('amount')) or 0)
                        break
                WalmartOrderItem.objects.create(
                    order=order,
                    line_number=str(ln.get('lineNumber') or '1'),
                    walmart_sku=str(item.get('sku') or '')[:64],
                    product_name=str(item.get('productName') or '')[:256],
                    quantity=qty,
                    unit_price=price,
                )
            imported += 1
            if 'Created' in statuses or not statuses:
                try:
                    wc.acknowledge(po_id)
                    order.acknowledged_at = timezone.now()
                    order.save(update_fields=['acknowledged_at'])
                    acked += 1
                except Exception as exc:  # ack failure is retried on next run
                    log_error(exc, endpoint='acknowledge', order=order)
            else:                          # already acknowledged upstream
                order.acknowledged_at = timezone.now()
                order.save(update_fields=['acknowledged_at'])
        except Exception as exc:
            errors += 1
            log_error(exc, endpoint='import', order=None)
            logger.exception('Import failed for Walmart PO %s', po_id)

    return {'listed': len(raw_orders), 'imported': imported,
            'skipped_existing': skipped, 'already_fulfilled': already_done,
            'acknowledged': acked, 'errors': errors}


# ── Step 2+3+4: validate + inventory + create MCF ────────────────────────────

def submit_orders(order_ids: list[int] | None = None) -> dict:
    """NEW (and retryable HOLD) orders → mapping/inventory checks → MCF.
    order_ids limits the run to selected orders (checkbox submit)."""
    client = _mcf_client()
    wc = WalmartClient()
    submitted, held, errored, skipped = 0, 0, 0, 0

    qs = WalmartOrder.objects.filter(status__in=[S.NEW, S.HOLD])
    if order_ids:
        qs = qs.filter(pk__in=order_ids)
    candidates = list(qs.prefetch_related('items')[:100])
    for order in candidates:
        try:
            result = _submit_one(order, client, wc)
        except Exception as exc:
            log_error(exc, endpoint='submit', order=order)
            logger.exception('submit failed for %s', order.purchase_order_id)
            errored += 1
            continue
        if result == 'submitted':
            submitted += 1
        elif result == 'hold':
            held += 1
        elif result == 'error':
            errored += 1
        else:
            skipped += 1
    return {'candidates': len(candidates), 'submitted': submitted,
            'held': held, 'errored': errored, 'skipped': skipped}


def _submit_one(order: WalmartOrder, client, wc=None) -> str:
    items = list(order.items.all())
    if not items:
        transition(order, S.ERROR, 'submit_orders',
                   {'reason': 'no order lines'},
                   from_states=[S.NEW, S.HOLD],
                   error_reason='Order has no lines')
        notify_admin(f'Order {order.purchase_order_id} has no lines',
                     'Imported without order lines — inspect raw_order in admin.')
        return 'error'

    # ── SKU mapping ──
    mappings = {m.walmart_sku: m for m in
                SkuMapping.objects.filter(
                    walmart_sku__in=[i.walmart_sku for i in items])}
    unmapped = [i.walmart_sku for i in items
                if i.walmart_sku not in mappings
                or not mappings[i.walmart_sku].enabled]
    if unmapped:
        if transition(order, S.ERROR, 'submit_orders',
                      {'unmapped_skus': unmapped},
                      from_states=[S.NEW, S.HOLD],
                      error_reason=f'No enabled SKU mapping: {", ".join(unmapped)}'):
            notify_admin(
                f'Unmapped SKU on Walmart order {order.purchase_order_id}',
                f'SKUs without an enabled mapping: {", ".join(unmapped)}.\n'
                f'Add them in Admin → SKU Mappings, then use "Reprocess".')
        return 'error'

    # ── Inventory (feature-aware: BLANK_BOX stock if required) ──
    feature = 'BLANK_BOX' if settings.WALMART_MCF_FEATURES.get(
        'BLANK_BOX') == 'Required' else None
    shortages = []
    for it in items:
        amazon_sku = mappings[it.walmart_sku].amazon_sku
        try:
            if feature:
                inv = client.get_mcf_feature_sku(feature, amazon_sku)
                if inv.get('isEligible') is False:
                    available = 0
                else:
                    available = int(inv.get('skuCount') or 0)
            else:
                available = None                     # no gate configured
        except FatalAPIError as exc:
            if exc.status_code == 404:               # SKU not feature-eligible
                available = 0
            else:
                raise
        if available is not None and available < it.quantity:
            shortages.append(f'{amazon_sku} (need {it.quantity}, '
                             f'available {available})')
    if shortages:
        was_new = order.status == S.NEW
        if transition(order, S.HOLD, 'submit_orders',
                      {'shortages': shortages},
                      from_states=[S.NEW, S.HOLD],
                      error_reason='Insufficient inventory: '
                                   + '; '.join(shortages)):
            if was_new:                              # alert once, not every retry
                notify_admin(
                    f'Inventory hold — Walmart order {order.purchase_order_id}',
                    'Held until FBA inventory is available:\n'
                    + '\n'.join(shortages))
        return 'hold'

    # ── Duplicate-fulfillment guard: a human may already have created an
    # MCF order for this Walmart PO in Seller Central (the manual process
    # this system replaces). Shipping it again would double-fulfill.
    try:
        manual = _find_manual_mcf(order.purchase_order_id,
                                  order.customer_order_id)
    except Exception:
        manual = None
    if manual:
        if transition(order, S.ERROR, 'submit_orders',
                      {'existing_mcf': manual.seller_order_id},
                      from_states=[S.NEW, S.HOLD],
                      error_reason=f'MCF order already exists in Amazon '
                                   f'({manual.seller_order_id}) — created '
                                   f'manually. Not resubmitting.'):
            notify_admin(
                f'Walmart order {order.purchase_order_id} already has an '
                f'MCF order',
                f'Existing Amazon MCF order {manual.seller_order_id} '
                f'references this PO. Skipped to avoid double fulfillment. '
                f'Use "Reprocess" only if that order was cancelled.')
        return 'error'

    # ── Last-moment Walmart cancellation guard ──
    # The customer may have cancelled on Walmart after import but before we
    # submit. Walmart doesn't push that, so check now — creating an MCF for a
    # cancelled order would ship goods for an order that no longer exists.
    try:
        if _walmart_order_cancelled(wc or WalmartClient(),
                                    order.purchase_order_id):
            transition(order, S.CANCELLED, 'submit_orders',
                       {'note': 'cancelled on Walmart before MCF creation'},
                       from_states=[S.NEW, S.HOLD],
                       error_reason='Cancelled on Walmart — MCF not created')
            return 'skipped'
    except Exception as exc:            # never block a submit on a lookup blip
        logger.warning('cancel-check failed for %s: %s',
                       order.purchase_order_id, exc)

    # ── Claim the order (CAS) — only one worker passes this line ──
    if not transition(order, S.VALIDATED, 'submit_orders',
                      from_states=[S.NEW, S.HOLD]):
        return 'skipped'
    if not transition(order, S.PROCESSING, 'submit_orders'):
        return 'skipped'

    # ── Create the MCF order ──
    fo_id = _fo_id(order)
    addr = order.shipping_address or {}
    destination = {
        'name': (addr.get('name') or order.customer_name or 'Customer')[:50],
        'addressLine1': str(addr.get('address1') or '')[:60],
        'city': str(addr.get('city') or '')[:50],
        # 2020-07-01 uses stateOrRegion (NOT the v0 stateOrProvinceCode —
        # Amazon silently drops unknown fields and rejects the empty state)
        'stateOrRegion': str(addr.get('state') or '')[:10],
        'postalCode': str(addr.get('postalCode') or '')[:20],
        'countryCode': str(addr.get('country') or 'US')[:2],
    }
    if addr.get('address2'):
        destination['addressLine2'] = str(addr['address2'])[:60]
    if order.phone:
        destination['phone'] = order.phone[:20]
    mcf_items = [{
        'sellerSku': mappings[it.walmart_sku].amazon_sku,
        'sellerFulfillmentOrderItemId': f'{order.purchase_order_id}-{it.line_number}',
        'quantity': it.quantity,
    } for it in items]
    speed = SPEED_MAP.get(order.shipping_method, 'Standard')
    constraints = _feature_constraints()

    # ── Address pre-validation. The SP-API has no "suggested address"
    # operation (that's a Seller Central UI feature), so the closest
    # compliant behaviour is: validate via getFulfillmentPreview and retry
    # progressively normalized variants until Amazon accepts one.
    destination, addr_err = _validated_address(client, destination, mcf_items,
                                               speed, constraints)
    if destination is None:
        transition(order, S.ERROR, 'submit_orders',
                   {'address_error': addr_err},
                   error_reason=f'Amazon rejected the shipping address: '
                                f'{addr_err}. Edit shipping_address in '
                                f'Admin, then Reprocess.')
        notify_admin(
            f'Invalid address on Walmart order {order.purchase_order_id}',
            f'Amazon rejected every variant of the customer address.\n'
            f'{addr_err}\nFix it in Admin → Walmart Orders → '
            f'shipping_address, then use "Reprocess".')
        return 'error'

    try:
        client.create_mcf_order(
            seller_fulfillment_order_id=fo_id,
            displayable_order_id=(order.customer_order_id
                                  or order.purchase_order_id),
            displayable_order_date_iso=order.order_date.strftime(
                '%Y-%m-%dT%H:%M:%SZ'),
            shipping_speed=speed,
            destination_address=destination,
            items=mcf_items,
            feature_constraints=constraints,
        )
    except Exception as exc:
        # Ambiguous outcome (timeout / 5xx / duplicate-id 4xx): check whether
        # Amazon actually created it before deciding. Deterministic fo_id makes
        # this adoption safe.
        try:
            existing = _try_get_fulfillment_order(client, fo_id)
        except Exception:
            existing = None      # never leave the order stuck in PROCESSING
        if existing is None:
            import requests as _rq
            status = None
            if isinstance(exc, FatalAPIError):
                status = exc.status_code
            elif isinstance(exc, _rq.HTTPError):
                status = getattr(exc.response, 'status_code', None)
            if status and 400 <= status < 500 and status != 429:
                body = (exc.body if isinstance(exc, FatalAPIError)
                        else (getattr(exc, 'response', None) is not None
                              and exc.response.text or ''))[:800]
                transition(order, S.ERROR, 'submit_orders',
                           {'create_error': str(exc)[:500]},
                           error_reason=f'Amazon rejected order: {exc}')
                notify_admin(
                    f'Amazon rejected MCF order for {order.purchase_order_id}',
                    f'{exc}\n{body}')
                return 'error'
            # transient (429/5xx/network) — roll back to NEW; next run retries
            transition(order, S.NEW, 'submit_orders',
                       {'transient_create_error': str(exc)[:500]})
            raise
        # else: Amazon has it — adopt it below.

    # Verify + persist (getFulfillmentOrder echoes featureConstraints)
    fo = _try_get_fulfillment_order(client, fo_id) or {}
    fo_header = fo.get('fulfillmentOrder', {})
    echoed = fo_header.get('featureConstraints', [])
    AmazonMCFOrder.objects.get_or_create(
        fulfillment_order_id=fo_id,
        defaults={'order': order,
                  'amazon_status': fo_header.get('fulfillmentOrderStatus', ''),
                  'shipping_speed': speed,
                  'feature_constraints': echoed})
    transition(order, S.MCF_CREATED, 'submit_orders',
               {'fulfillment_order_id': fo_id, 'speed': speed,
                'feature_constraints_sent': constraints,
                'feature_constraints_echoed': echoed})
    wanted = {c['featureName'] for c in constraints
              if c['featureFulfillmentPolicy'] == 'Required'}
    got = {c.get('featureName') for c in echoed}
    if wanted - got:
        notify_admin(
            f'Feature constraints not confirmed on {fo_id}',
            f'Requested Required features {sorted(wanted)} but Amazon echoed '
            f'{sorted(got)}. Verify Blank Box / Block AMZL on this order in '
            f'Seller Central.')
    return 'submitted'


def _fo_id(order: WalmartOrder) -> str:
    """
    Deterministic Amazon order id for a Walmart order: the bare Walmart
    customerOrderId (identical to the ops convention for manual orders),
    PO id as fallback. History: first orders used 'WM-{po}', then
    'WM-{customerOrderId}'; the prefix was dropped 2026-07-14. Because the
    id no longer marks an order as ours, "is it one of ours" checks must go
    through AmazonMCFOrder, never the id shape.
    """
    return str(order.customer_order_id or order.purchase_order_id)[:40]


def _find_manual_mcf(po: str, customer_order_id: str = ''):
    """
    A manually-created Amazon MCF order for this Walmart order. Ops re-uses
    Walmart's **customerOrderId** (200015…) as the Amazon order id — check
    both ids to be safe.
    """
    from django.db.models import Q
    from apps.dashboard.models import McfOrder as DashMcf
    q = Q(seller_order_id__icontains=po) | Q(displayable_order_id__icontains=po)
    if customer_order_id:
        q |= (Q(seller_order_id__icontains=customer_order_id) |
              Q(displayable_order_id__icontains=customer_order_id))
    return (DashMcf.objects.filter(q)
            # our own automated orders (any id era): everything we created
            # is registered in AmazonMCFOrder — id shape proves nothing
            .exclude(seller_order_id__in=AmazonMCFOrder.objects
                     .values_list('fulfillment_order_id', flat=True))
            .exclude(seller_order_id__startswith='WM-')   # legacy prefix era
            .exclude(status__iexact='Cancelled')
            .first())


def _address_variants(dest: dict):
    """Progressively normalized variants of a destination address."""
    import re
    yield dest                                        # as provided
    v = dict(dest)                                    # basic cleanup
    for k in ('name', 'addressLine1', 'addressLine2', 'city'):
        if v.get(k):
            v[k] = re.sub(r'[^\x20-\x7E]', '', v[k])      # non-ASCII out
            v[k] = re.sub(r'\s+', ' ', v[k]).strip(' ,.')
    v['stateOrRegion'] = (v.get('stateOrRegion') or '').strip().upper()
    zip5 = re.match(r'(\d{5})', v.get('postalCode') or '')
    if zip5:
        v = dict(v, postalCode=zip5.group(1))         # ZIP+4 → ZIP5
    yield v
    if v.get('addressLine2'):                         # unit into line 1
        merged = dict(v)
        merged['addressLine1'] = f"{v['addressLine1']} {v['addressLine2']}"[:60]
        merged.pop('addressLine2')
        yield merged


def _validated_address(client, dest: dict, items: list[dict], speed: str,
                       constraints: list[dict]):
    """
    Return (address_accepted_by_preview, None) or (None, last_error).
    Non-address preview failures fall through with the original address —
    the create call remains the authority.
    """
    last_err = ''
    for variant in _address_variants(dest):
        try:
            previews = client.get_mcf_fulfillment_preview(
                variant, [{'sellerSku': i['sellerSku'],
                           'sellerFulfillmentOrderItemId':
                               i['sellerFulfillmentOrderItemId'],
                           'quantity': i['quantity']} for i in items],
                speeds=[speed], feature_constraints=constraints)
            if previews:                              # address accepted
                return variant, None
            last_err = 'preview returned no fulfillment options'
        except Exception as exc:
            msg = str(exc)
            body = getattr(getattr(exc, 'response', None), 'text', '') or ''
            if 'address' not in (msg + body).lower():
                return dest, None    # not an address problem — proceed
            last_err = (body or msg)[:300]
    return None, last_err


def _try_get_fulfillment_order(client, fo_id: str) -> dict | None:
    import requests as _rq
    try:
        resp = client._get(f'/fba/outbound/2020-07-01/fulfillmentOrders/{fo_id}')
        return resp.get('payload', resp) or {}
    except _rq.HTTPError as exc:
        if getattr(exc.response, 'status_code', None) == 404:
            return None
        raise
    except Exception:
        return None


# ── Step 6: status monitoring ────────────────────────────────────────────────

def check_status(order_ids: list[int] | None = None) -> dict:
    """Poll Amazon for open MCF orders; harvest packages when shipped.
    order_ids limits the run to those WalmartOrder ids (page selection)."""
    client = _mcf_client()
    checked, shipped, cancelled = 0, 0, 0
    open_orders = (AmazonMCFOrder.objects
                   .filter(order__status__in=[S.MCF_CREATED, S.SHIPPED,
                                              S.TRACKING_UPLOADED])
                   .select_related('order'))
    if order_ids:
        open_orders = open_orders.filter(order_id__in=order_ids)
    open_orders = open_orders[:200]
    for mcf in open_orders:
        fo = _try_get_fulfillment_order(client, mcf.fulfillment_order_id)
        if fo is None:
            continue
        checked += 1
        header = fo.get('fulfillmentOrder', {})
        status = str(header.get('fulfillmentOrderStatus', '')).upper()
        mcf.amazon_status = status
        mcf.last_status_check = timezone.now()
        mcf.save(update_fields=['amazon_status', 'last_status_check'])

        new_pkgs = _harvest_packages(mcf, fo)
        if status in AMAZON_CANCEL_STATUSES:
            if transition(mcf.order, S.CANCELLED, 'check_status',
                          {'amazon_status': status},
                          from_states=[S.MCF_CREATED],
                          error_reason=f'Amazon status {status}'):
                cancelled += 1
                notify_admin(
                    f'MCF order cancelled: {mcf.fulfillment_order_id}',
                    f'Walmart PO {mcf.order.purchase_order_id} — Amazon '
                    f'reported {status}. Decide: resubmit (admin action) or '
                    f'cancel on Walmart.')
        elif new_pkgs or status in AMAZON_DONE_STATUSES:
            # packages exist → order (or part of it) is on the move
            if mcf.order.status == S.MCF_CREATED and mcf.packages.exists():
                if transition(mcf.order, S.SHIPPED, 'check_status',
                              {'amazon_status': status,
                               'new_packages': new_pkgs}):
                    shipped += 1
            elif (mcf.order.status == S.TRACKING_UPLOADED and new_pkgs):
                # late extra package — go back for another upload round
                transition(mcf.order, S.SHIPPED, 'check_status',
                           {'late_packages': new_pkgs})
    return {'checked': checked, 'newly_shipped': shipped,
            'cancelled': cancelled}


def _harvest_packages(mcf: AmazonMCFOrder, fo: dict) -> int:
    """Extract packages from getFulfillmentOrder; insert new ones only."""
    created = 0
    for shipment in fo.get('fulfillmentShipments', []) or []:
        ship_id = str(shipment.get('amazonShipmentId') or '')
        ship_date = _parse_dt(shipment.get('shippingDate'))
        est = _parse_dt(shipment.get('estimatedArrivalDate'))
        items = [{'sellerSku': i.get('sellerSku'),
                  'quantity': i.get('quantity'),
                  'packageNumber': i.get('packageNumber')}
                 for i in (shipment.get('fulfillmentShipmentItem') or [])]
        for pkg in (shipment.get('fulfillmentShipmentPackage') or []):
            tracking = str(pkg.get('trackingNumber') or '').strip()
            if not tracking:
                continue
            carrier = str(pkg.get('carrierCode') or '').strip()
            pkg_no = int(pkg.get('packageNumber') or 0)
            h = hashlib.sha1(
                f'{mcf.order.purchase_order_id}|{pkg_no}|{carrier}|{tracking}'
                .encode()).hexdigest()
            _, was_created = ShipmentPackage.objects.get_or_create(
                upload_hash=h,
                defaults={
                    'mcf_order': mcf,
                    'shipment_id': ship_id,
                    'package_number': pkg_no,
                    'carrier_code': carrier,
                    'carrier_walmart': CARRIER_MAP.get(carrier.upper(),
                                                       carrier or 'Other'),
                    'tracking_number': tracking,
                    'ship_date': ship_date,
                    'estimated_delivery': _parse_dt(
                        pkg.get('estimatedArrivalDate')) or est,
                    'items': [i for i in items
                              if i.get('packageNumber') == pkg_no] or items,
                })
            if was_created:
                created += 1
                if tracking.upper().startswith('TBA') or \
                        carrier.upper() in ('AMZL', 'AMZN_US'):
                    notify_admin(
                        f'AMZL tracking on {mcf.fulfillment_order_id}',
                        f'Package {tracking} shipped via Amazon Logistics '
                        f'despite BLOCK_AMZL. Walmart cannot track TBA '
                        f'numbers — handle PO '
                        f'{mcf.order.purchase_order_id} manually.')
    return created


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    except ValueError:
        return None


# ── Step 7+8: upload tracking to Walmart ─────────────────────────────────────

def _order_units_covered(order, mcf) -> bool:
    """Unit-level reconciliation: is every ordered UNIT covered by a shipped
    package?

    A SKU-set comparison is not enough: an order for 5 units of one SKU with
    only 3 units shipped would wrongly compare as "covered". We therefore sum
    quantities per SKU on both sides.

    If Amazon returned no usable quantity on any shipped item (legacy or
    degraded payload) we fall back to the old SKU-set comparison rather than
    hanging the order forever.
    """
    order_items = list(order.items.all())
    if not order_items:
        return False
    sku_map = {m.walmart_sku: m.amazon_sku for m in
               SkuMapping.objects.filter(
                   walmart_sku__in=[i.walmart_sku for i in order_items])}
    ordered = {}
    for i in order_items:
        key = (sku_map.get(i.walmart_sku) or i.walmart_sku).upper()
        ordered[key] = ordered.get(key, 0) + int(i.quantity or 0)

    shipped_qty = {}
    saw_quantity = False
    for p in mcf.packages.all():
        for pi in (p.items or []):
            s = pi.get('sellerSku') or pi.get('SellerSKU')
            if not s:
                continue
            key = str(s).upper()
            raw = pi.get('quantity', pi.get('Quantity'))
            try:
                qty = int(raw)
            except (TypeError, ValueError):
                qty = 0
            if qty > 0:
                saw_quantity = True
            shipped_qty[key] = shipped_qty.get(key, 0) + qty

    if not saw_quantity:
        # no quantity data at all -> legacy SKU-coverage behaviour
        return set(ordered).issubset(set(shipped_qty))
    return all(shipped_qty.get(sku, 0) >= qty for sku, qty in ordered.items())


def _order_fully_shipped(order) -> bool:
    """True only when no further shipments are coming for this order, so it is
    safe to move it to a terminal (archivable) state.

    A multi-SKU **or multi-unit** order that Amazon has only partially shipped
    must stay Active and keep being polled — archiving it now would hide the
    un-shipped units from ops and from Walmart.

    Decision table on Amazon MCF fulfillmentOrderStatus:
      • COMPLETE                            -> terminal. Every unit fulfilled.
      • CANCELLED / UNFULFILLABLE / INVALID -> terminal. Nothing more is coming.
      • COMPLETEPARTIALLED                  -> AMBIGUOUS. Amazon uses this both
        for "some units are unfulfillable" AND for "some units shipped, the
        rest are still processing". Terminal only when unit-level
        reconciliation shows every ordered unit is already covered.
      • anything else (RECEIVED / PLANNING / PROCESSING / ...) -> terminal only
        if unit coverage is already complete (status lagging reality).

    Note: treating COMPLETEPARTIALLED as terminal on status alone is what
    archived PO 200015153699282 with 2 of 5 units still unshipped.
    """
    mcf = getattr(order, 'mcf', None)
    if mcf is None:
        return False
    st = (mcf.amazon_status or '').upper()
    if st == 'COMPLETE' or st in AMAZON_CANCEL_STATUSES:
        return True
    return _order_units_covered(order, mcf)


def _walmart_order_already_shipped(wc, po: str) -> bool:
    """True when Walmart already shows every line of this order shipped/
    delivered (tracking already on Walmart) — so a shipping update would be
    rejected as a duplicate."""
    try:
        data = wc.get_order(po)
    except Exception:
        return False
    o = data.get('order') or data
    lines = ((o.get('orderLines') or {}).get('orderLine')) or []
    if not lines:
        return False
    saw_line = False
    for ln in lines:
        saw_line = True
        sts = {s.get('status') for s in
               ((ln.get('orderLineStatuses') or {}).get('orderLineStatus')) or []}
        if not (sts & {'Shipped', 'Delivered', 'Cancelled'}):
            return False               # a line is still open → not fully shipped
    return saw_line                    # only "shipped" if we actually saw lines


def _walmart_order_cancelled(wc, po: str) -> bool:
    """True when Walmart shows the *entire* order cancelled — every line is in a
    Cancelled status and none is shipped/delivered. Used to detect a Walmart-side
    cancellation (which Walmart does not push to us) so we can stop the order
    locally before/without creating an Amazon MCF order. Returns False on any
    API error (fail-safe: never cancel on a transient lookup failure)."""
    try:
        data = wc.get_order(po)
    except Exception:
        return False
    o = data.get('order') or data
    lines = ((o.get('orderLines') or {}).get('orderLine')) or []
    if not lines:
        return False
    saw_cancelled = False
    for ln in lines:
        sts = {s.get('status') for s in
               ((ln.get('orderLineStatuses') or {}).get('orderLineStatus')) or []}
        if sts & {'Shipped', 'Delivered', 'Acknowledged'}:
            return False               # something is progressing → not cancelled
        if 'Cancelled' in sts:
            saw_cancelled = True
        elif sts:                      # a line in some other open status
            return False
    return saw_cancelled


# States from which a Walmart-side cancellation can be safely absorbed. Pre-MCF
# states archive with no Amazon action needed; MCF_CREATED is left out on
# purpose — an in-flight Amazon fulfillment must be cancelled on Amazon first,
# so those are surfaced to an admin rather than auto-cancelled here.
CANCELLABLE_STATES = [S.NEW, S.VALIDATED, S.HOLD, S.ERROR]


def sync_walmart_cancellations(order_ids: list[int] | None = None) -> dict:
    """Poll Walmart for orders cancelled on their side and stop them locally.

    Walmart does not push cancellations, so an order the customer cancels while
    it still sits in a pre-MCF state (NEW/HOLD/…) would otherwise be submitted to
    Amazon and shipped. This catches that: for each open pre-MCF order we ask
    Walmart for its current status and, if the whole order is cancelled, move it
    to CANCELLED (which the Active/Archive view then files under Archive).
    """
    wc = WalmartClient()
    cancelled, checked = 0, 0
    qs = WalmartOrder.objects.filter(status__in=CANCELLABLE_STATES)
    if order_ids:
        qs = qs.filter(pk__in=order_ids)
    for order in qs[:200]:
        checked += 1
        if not _walmart_order_cancelled(wc, order.purchase_order_id):
            continue
        if transition(order, S.CANCELLED, 'sync_walmart_cancellations',
                      {'note': 'cancelled on Walmart before fulfillment'},
                      from_states=CANCELLABLE_STATES,
                      error_reason='Cancelled on Walmart — no MCF created'):
            cancelled += 1
            logger.info('Walmart order %s cancelled on Walmart → CANCELLED',
                        order.purchase_order_id)
    return {'checked': checked, 'cancelled': cancelled}


def upload_tracking(order_ids: list[int] | None = None) -> dict:
    """Upload not-yet-uploaded packages to Walmart; per-package dedupe.
    order_ids limits the run to those WalmartOrder ids (page selection).
    Only SHIPPED orders (tracking on Amazon, not yet on Walmart) are touched."""
    wc = WalmartClient()
    uploaded, failed, completed, already_on_walmart = 0, 0, 0, 0

    orders = (WalmartOrder.objects
              .filter(status=S.SHIPPED, mcf__isnull=False)
              .select_related('mcf').prefetch_related('items', 'mcf__packages'))
    if order_ids:
        orders = orders.filter(id__in=order_ids)
    orders = orders[:100]
    for order in orders:
        pending = [p for p in order.mcf.packages.all()
                   if p.uploaded_to_walmart_at is None
                   and not p.tracking_number.upper().startswith('TBA')]
        # If Walmart already shows the order shipped (tracking updated manually
        # or in a prior run), the shipping update fails as a duplicate — treat
        # it as done: mark packages uploaded and archive.
        if pending and _walmart_order_already_shipped(wc, order.purchase_order_id):
            now = timezone.now()
            for p in pending:
                p.uploaded_to_walmart_at = now
                p.upload_error = 'tracking already present on Walmart'
                p.save(update_fields=['uploaded_to_walmart_at', 'upload_error'])
            if transition(order, S.TRACKING_UPLOADED, 'upload_tracking',
                          {'note': 'tracking already present on Walmart'}):
                already_on_walmart += 1
                uploaded += len(pending)
                # Amazon done and every SKU shipped → close out to terminal.
                if (order.mcf.amazon_status.upper() in AMAZON_DONE_STATUSES
                        and _order_fully_shipped(order)):
                    if transition(order, S.COMPLETED, 'upload_tracking'):
                        completed += 1
            continue
        if not pending:
            # nothing new but state says SHIPPED. Only advance to a terminal
            # (archivable) state when every ordered SKU has shipped — a partial
            # multi-SKU order must stay SHIPPED so check_status keeps polling.
            if (order.mcf.packages.exclude(uploaded_to_walmart_at=None).exists()
                    and _order_fully_shipped(order)):
                transition(order, S.TRACKING_UPLOADED, 'upload_tracking',
                           {'note': 'all packages uploaded, order fully shipped'})
            continue

        items_by_sku = {}
        for it in order.items.all():
            items_by_sku[it.walmart_sku] = it
        sku_map = {m.walmart_sku: m.amazon_sku for m in
                   SkuMapping.objects.filter(
                       walmart_sku__in=list(items_by_sku))}
        ok = True
        for pkg in pending:
            lines = _walmart_lines_for_package(order, pkg, items_by_sku,
                                               sku_map)
            if not lines:
                pkg.upload_error = 'Could not map package items to Walmart lines'
                pkg.save(update_fields=['upload_error'])
                ok = False
                continue
            try:
                wc.update_shipping(order.purchase_order_id, lines)
                pkg.uploaded_to_walmart_at = timezone.now()
                pkg.upload_error = ''
                pkg.save(update_fields=['uploaded_to_walmart_at',
                                        'upload_error'])
                uploaded += 1
            except FatalAPIError as exc:
                # Already-shipped/duplicate responses are success-equivalent
                if 'already' in (exc.body or '').lower():
                    pkg.uploaded_to_walmart_at = timezone.now()
                    pkg.upload_error = f'accepted-as-duplicate: {exc}'[:500]
                    pkg.save(update_fields=['uploaded_to_walmart_at',
                                            'upload_error'])
                    uploaded += 1
                else:
                    ok = False
                    failed += 1
                    pkg.upload_error = str(exc)[:500]
                    pkg.save(update_fields=['upload_error'])
                    log_error(exc, endpoint='shipping', order=order)
            except Exception as exc:
                ok = False
                failed += 1
                log_error(exc, endpoint='shipping', order=order)

        # Partial tracking has now been pushed to Walmart. Only mark the order
        # terminal (archivable) once every ordered SKU has shipped — otherwise
        # keep it SHIPPED so the next check_status run harvests the remaining
        # packages for the un-shipped SKUs.
        all_pkgs_uploaded = not order.mcf.packages.filter(
            uploaded_to_walmart_at=None).exclude(
            tracking_number__istartswith='TBA').exists()
        if ok and all_pkgs_uploaded and _order_fully_shipped(order):
            if transition(order, S.TRACKING_UPLOADED, 'upload_tracking',
                          {'packages': order.mcf.packages.count()}):
                # Amazon says complete? → close out
                if order.mcf.amazon_status.upper() in AMAZON_DONE_STATUSES:
                    transition(order, S.COMPLETED, 'upload_tracking')
                    completed += 1
    return {'uploaded_packages': uploaded, 'failed': failed,
            'completed': completed, 'already_on_walmart': already_on_walmart}


def _walmart_lines_for_package(order, pkg, items_by_sku, sku_map) -> list[dict]:
    """Build Walmart shipping-update lines for one package."""
    amazon_to_walmart = {v: k for k, v in sku_map.items()}
    ship_ms = int((pkg.ship_date or timezone.now()).timestamp() * 1000)
    lines = []
    pkg_items = pkg.items or []
    if pkg_items:
        for pi in pkg_items:
            wm_sku = amazon_to_walmart.get(pi.get('sellerSku'))
            it = items_by_sku.get(wm_sku)
            if not it:
                continue
            lines.append(_line(it, pi.get('quantity') or it.quantity,
                               ship_ms, pkg, order))
    else:
        # No per-package item detail → attribute all order lines to this pkg
        for it in items_by_sku.values():
            lines.append(_line(it, it.quantity, ship_ms, pkg, order))
    return lines


def _line(item, qty, ship_ms, pkg, order) -> dict:
    return {
        'line_number': item.line_number,
        'quantity': qty,
        'ship_datetime_ms': ship_ms,
        'carrier': pkg.carrier_walmart or 'Other',
        'method_code': order.shipping_method or 'Standard',
        'tracking_number': pkg.tracking_number,
        'tracking_url': '',
    }


# ── Backfill: tracking for manually-created MCF orders ──────────────────────

def backfill_manual_tracking(days_back: int = 30, dry_run: bool = False) -> dict:
    """
    For Walmart orders still 'Acknowledged' (not Shipped) on Walmart whose
    fulfillment was created MANUALLY in Seller Central (ops re-used the
    Walmart PO as the Amazon order id): find the manual MCF order, take its
    tracking numbers, upload them to Walmart. Fully deduped via
    ShipmentPackage.upload_hash, so re-running never re-uploads.
    """
    from django.db.models import Q
    from apps.dashboard.models import McfOrder as DashMcf

    wc = WalmartClient()
    since = (datetime.now(tz.utc) - timedelta(days=days_back)) \
        .strftime('%Y-%m-%dT%H:%M:%SZ')
    acked = wc.get_all_orders(since, status='Acknowledged')
    res = {'walmart_acknowledged': len(acked), 'uploaded': 0, 'skipped_done': 0,
           'no_mcf_match': 0, 'no_tracking_yet': 0, 'tba_blocked': 0,
           'errors': 0, 'dry_run': dry_run, 'preview': []}

    for raw in acked:
        po = str(raw.get('purchaseOrderId') or '').strip()
        if not po:
            continue
        # our own automated orders are handled by the normal pipeline
        if AmazonMCFOrder.objects.filter(
                order__purchase_order_id=po).exists():
            continue
        customer_order_id = str(raw.get('customerOrderId') or '')
        manual = _find_manual_mcf(po, customer_order_id)
        if not manual:
            res['no_mcf_match'] += 1
            continue
        pkgs = [p for p in (manual.packages or []) if p.get('tracking')]
        if not pkgs:
            res['no_tracking_yet'] += 1
            continue
        if all(str(p['tracking']).upper().startswith('TBA') for p in pkgs):
            res['tba_blocked'] += 1
            notify_admin(f'Backfill: only AMZL tracking for PO {po}',
                         f'Manual MCF order {manual.seller_order_id} shipped '
                         f'via Amazon Logistics — Walmart cannot track TBA '
                         f'numbers. Handle manually.')
            continue
        pkg = next(p for p in pkgs
                   if not str(p['tracking']).upper().startswith('TBA'))

        try:
            order = WalmartOrder.objects.filter(purchase_order_id=po).first()
            if order is None:
                ship = raw.get('shippingInfo') or {}
                addr = ship.get('postalAddress') or {}
                odate = raw.get('orderDate')
                order_dt = (datetime.fromtimestamp(odate / 1000, tz.utc)
                            if isinstance(odate, (int, float)) else
                            datetime.now(tz.utc))
                order = WalmartOrder.objects.create(
                    purchase_order_id=po,
                    marketplace=settings.WALMART_MCF_MARKETPLACE,
                    order_date=order_dt,
                    customer_name=str(addr.get('name') or '')[:128],
                    shipping_address=addr,
                    shipping_method=str(ship.get('methodCode') or 'Standard'),
                    raw_order=raw, status=S.COMPLETED)
            mcf = getattr(order, 'mcf', None)
            if mcf is None:
                mcf = AmazonMCFOrder.objects.create(
                    order=order,
                    fulfillment_order_id=manual.seller_order_id[:48],
                    amazon_status=manual.status or 'Manual')

            carrier = str(pkg.get('carrier') or '')
            tracking = str(pkg.get('tracking') or '')
            h = hashlib.sha1(f'{po}|manual|{carrier}|{tracking}'
                             .encode()).hexdigest()
            sp, created_pkg = ShipmentPackage.objects.get_or_create(
                upload_hash=h,
                defaults={'mcf_order': mcf,
                          'carrier_code': carrier,
                          'carrier_walmart': CARRIER_MAP.get(
                              carrier.upper(), carrier or 'Other'),
                          'tracking_number': tracking,
                          'ship_date': _parse_dt(pkg.get('ship_date'))})
            if sp.uploaded_to_walmart_at:
                res['skipped_done'] += 1
                continue
            lines = []
            ship_ms = int((sp.ship_date or timezone.now()).timestamp() * 1000)
            for ln in ((raw.get('orderLines') or {}).get('orderLine')) or []:
                sts = {s.get('status') for s in
                       ((ln.get('orderLineStatuses') or {})
                        .get('orderLineStatus')) or []}
                if 'Shipped' in sts or 'Delivered' in sts:
                    continue
                qty = int(((ln.get('orderLineQuantity') or {})
                           .get('amount')) or 1)
                lines.append({'line_number': str(ln.get('lineNumber') or '1'),
                              'quantity': qty,
                              'ship_datetime_ms': ship_ms,
                              'carrier': sp.carrier_walmart,
                              'method_code': order.shipping_method or 'Standard',
                              'tracking_number': tracking,
                              'tracking_url': ''})
            if not lines:
                res['skipped_done'] += 1
                continue
            if dry_run:
                res['preview'].append({'po': po, 'tracking': tracking,
                                       'carrier': sp.carrier_walmart,
                                       'lines': len(lines)})
                continue
            try:
                wc.update_shipping(po, lines)
            except FatalAPIError as exc:
                if 'already' not in (exc.body or '').lower():
                    raise
            sp.uploaded_to_walmart_at = timezone.now()
            sp.save(update_fields=['uploaded_to_walmart_at'])
            from .models import AuditEvent
            AuditEvent.objects.create(order=order, from_state=order.status,
                                      to_state=order.status,
                                      actor='backfill_manual_tracking',
                                      detail={'tracking': tracking,
                                              'mcf': manual.seller_order_id})
            res['uploaded'] += 1
        except Exception as exc:
            res['errors'] += 1
            log_error(exc, endpoint='backfill_tracking')
            logger.exception('backfill failed for PO %s', po)
    return res


# ── Daily inventory sync: Amazon (blank-box sellable) → Walmart ──────────────

def sync_inventory(buffer_pct: int = 0, dry_run: bool = False) -> dict:
    """
    Push Amazon MCF-fulfillable stock to Walmart for every enabled SKU
    mapping. Quantity = BLANK_BOX-eligible sellable units (that is what a
    Walmart order can actually ship as), optionally reduced by buffer_pct%.
    """
    client = _mcf_client()
    wc = WalmartClient()
    res = {'updated': 0, 'total_units': 0, 'errors': 0, 'dry_run': dry_run,
           'items': []}
    for m in SkuMapping.objects.filter(enabled=True):
        try:
            inv = client.get_mcf_feature_sku('BLANK_BOX', m.amazon_sku)
            qty = 0 if inv.get('isEligible') is False \
                else int(inv.get('skuCount') or 0)
        except Exception as exc:
            qty = 0
            log_error(exc, endpoint='inventory_amazon')
        qty = max(int(qty * (100 - buffer_pct) / 100), 0)
        res['items'].append({'walmart_sku': m.walmart_sku, 'qty': qty})
        res['total_units'] += qty
        if dry_run:
            continue
        try:
            wc.update_inventory(m.walmart_sku, qty)
            res['updated'] += 1
        except Exception as exc:
            res['errors'] += 1
            log_error(exc, endpoint='inventory_walmart')
            logger.exception('inventory push failed for %s', m.walmart_sku)
    if res['errors']:
        notify_admin(f'Walmart inventory sync: {res["errors"]} error(s)',
                     'See Error Logs in admin for details.')
    return res


# ── Nightly reconciliation ───────────────────────────────────────────────────

def reconcile(stuck_after_hours: int = 24) -> dict:
    """Close finished orders, flag stuck ones, summarize for the admin."""
    now = timezone.now()
    fixed, stuck, closed_manual = 0, [], 0

    # TRACKING_UPLOADED + Amazon done + EVERY ORDERED UNIT SHIPPED → COMPLETED
    # _order_fully_shipped() checks the MCF status *and* unit-level coverage,
    # so an ambiguous COMPLETEPARTIALLED with units still processing stays
    # Active instead of being archived behind ops' back.
    for order in WalmartOrder.objects.filter(
            status=S.TRACKING_UPLOADED).select_related('mcf'):
        if _order_fully_shipped(order):
            if transition(order, S.COMPLETED, 'reconcile'):
                fixed += 1

    # NOTE: an order is archived ONLY after its tracking number has been
    # retrieved from Amazon and successfully uploaded to Walmart (→
    # TRACKING_UPLOADED, then COMPLETED). We deliberately do NOT auto-complete
    # orders on Walmart's shipped-status alone — manually-fulfilled orders are
    # handled by backfill_manual_tracking, which uploads their tracking to
    # Walmart first, so they only archive once tracking is confirmed. Orders
    # without a confirmed Walmart tracking update stay in the active list.

    # Anything unfinished for too long
    cutoff = now - timedelta(hours=stuck_after_hours)
    for order in WalmartOrder.objects.filter(
            status__in=[S.NEW, S.VALIDATED, S.PROCESSING, S.MCF_CREATED,
                        S.SHIPPED, S.TRACKING_UPLOADED, S.HOLD],
            updated_at__lt=cutoff):
        stuck.append(f'{order.purchase_order_id}: {order.status} since '
                     f'{order.updated_at:%Y-%m-%d %H:%M} '
                     f'({order.error_reason[:80]})')
    error_count = WalmartOrder.objects.filter(status=S.ERROR).count()
    if stuck or error_count:
        notify_admin(
            f'Walmart-MCF nightly reconcile: {len(stuck)} stuck, '
            f'{error_count} in ERROR',
            ('Stuck orders (>%dh without progress):\n' % stuck_after_hours)
            + ('\n'.join(stuck) if stuck else '(none)')
            + f'\n\nOrders in ERROR needing attention: {error_count}')
    return {'auto_completed': fixed, 'closed_manual_fulfilled': closed_manual,
            'stuck': len(stuck), 'errors': error_count}
