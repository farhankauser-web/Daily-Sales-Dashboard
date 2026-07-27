"""
apps/dashboard/finances_importer.py — pull a month's P&L from Amazon's
Finances API (financialEvents) so the Management P&L can be synced with one
click, without a manual Unified Transaction upload.

IMPORTANT basis note: the Finances API is posted-date / released-transaction
based (like settlement flat-v2), so it carries the same ~1% deferred-
transaction variance vs the Seller Central Date-Range Transaction report that
ties exactly to the books. Months synced this way are marked provisional; a
later manual Unified upload for the same month overwrites and becomes the
authoritative figure.

Produces the SAME line keys the P&L engine reads (see pnl_lines / unified
importer): gross_sales, returns, promo, commission, fba_fee, ppc,
storage_fee, awd_*, inbound_transportation, subscription, account_management,
other_income, cogs — stored in SettlementLineActual (source_note='finances_api')
plus UnifiedSkuUnits for COGS recalc.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as date_cls, timedelta

_INCOME_KEYS = {'gross_sales', 'other_income',
                'sales_tax', 'sales_tax_refunds'}


def _amt(d) -> float:
    try:
        return float((d or {}).get('CurrencyAmount') or 0)
    except (TypeError, ValueError):
        return 0.0


def _classify_service_fee(desc: str, ftype: str) -> str:
    d = (desc or '').lower() + ' ' + (ftype or '').lower()
    if 'awd transportation' in d:
        return 'awd_transportation'
    if 'awd processing' in d:
        return 'awd_processing'
    if 'awd storage' in d:
        return 'awd_storage'
    if 'inbound transportation' in d or 'inbound placement' in d:
        return 'inbound_transportation'
    if 'storage' in d:
        return 'storage_fee'
    if 'subscription' in d:
        return 'subscription'
    if 'premium services' in d or 'strategic account' in d or 'account management' in d:
        return 'account_management'
    if 'advertis' in d or 'deal ' in d:
        return 'ppc'
    if 'removal' in d or 'disposal' in d or 'grade and resell' in d or 'liquidation' in d:
        return 'other_logistics'
    return 'other_logistics'


def _add_item_charges_fees(signed, item, head):
    """Charges + fees + promotions for one shipment item (order side).
    `head(line_key, label, amt)` accumulates the per-head composition."""
    for ch in (item.get('ItemChargeList') or []):
        t = (ch.get('ChargeType') or '')
        a = _amt(ch.get('ChargeAmount'))
        tl = t.lower()
        if t == 'Tax':
            # VAT itemized separately from Principal (e.g. UK) — captured so
            # the engine grosses up sales and deducts the actual VAT once.
            signed['sales_tax'] += a
            continue
        if 'tax' in tl:
            continue                                  # other taxes pass-through
        if t == 'Principal':
            signed['gross_sales'] += a
            head('gross_sales', 'Orders — product sales', a)
        elif t in ('ShippingCharge', 'GiftWrap'):
            signed['other_income'] += a               # buyer-paid ship/giftwrap
            head('other_income',
                 'Shipping credits' if t == 'ShippingCharge'
                 else 'Gift wrap credits', a)
    for fe in (item.get('ItemFeeList') or []):
        t = (fe.get('FeeType') or '')
        a = _amt(fe.get('FeeAmount'))
        if t in ('Commission', 'FixedClosingFee', 'VariableClosingFee',
                 'RefundCommission'):
            signed['commission'] += a
            head('commission', 'Orders — selling fees (referral)', a)
        elif t == 'GiftwrapChargeback':
            pass                                      # giftwrap pass-through
        else:                                         # FBA* / ShippingChargeback / misc
            signed['fba_fee'] += a
            head('fba_fee', f'Orders — {t or "other fee"}', a)
    for pr in (item.get('PromotionList') or []):
        a = _amt(pr.get('PromotionAmount'))
        signed['promo'] += a
        head('promo', 'Promotional rebates — orders', a)


def fetch_finances_month(marketplace: str, month: date_cls) -> dict:
    """Pull + aggregate financialEvents for the whole month into line keys."""
    from apps.amazon_api.models import AmazonAPIConfig
    from apps.amazon_api.services import SPAPIClient
    from .cogs_recalc import month_cogs_unit_map

    cfg = AmazonAPIConfig.objects.filter(
        marketplace=marketplace, is_active=True).first()
    if not cfg:
        raise RuntimeError(f'no active SP-API config for {marketplace}')
    client = SPAPIClient(cfg)

    start = month.replace(day=1)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    # Current month: the API 400s if PostedBefore is in the future — clamp.
    from datetime import datetime, timezone as _tz
    end_dt = datetime(nxt.year, nxt.month, nxt.day, tzinfo=_tz.utc)
    now_clamp = datetime.now(_tz.utc) - timedelta(minutes=5)
    if end_dt > now_clamp:
        end_dt = now_clamp
    events = client.list_financial_events(
        start.strftime('%Y-%m-%dT00:00:00Z'),
        end_dt.strftime('%Y-%m-%dT%H:%M:%SZ'))

    signed = defaultdict(float)
    heads: dict = defaultdict(lambda: defaultdict(float))
    order_units = defaultdict(int)
    refund_units = defaultdict(int)

    def _head(line_key, label, amt):
        if amt:
            heads[line_key][label] += amt

    # Shipments (sales)
    for e in events.get('ShipmentEventList', []):
        for it in (e.get('ShipmentItemList') or []):
            sku = (it.get('SellerSKU') or '').upper()
            qty = int(it.get('QuantityShipped') or 0)
            if sku and qty:
                order_units[sku] += qty
            _add_item_charges_fees(signed, it, _head)

    # Refunds (returns) — amounts come negative
    for e in events.get('RefundEventList', []):
        for it in (e.get('ShipmentItemAdjustmentList') or []):
            sku = (it.get('SellerSKU') or '').upper()
            qty = abs(int(it.get('QuantityShipped') or 0))
            if sku and qty:
                refund_units[sku] += qty
            # principal → returns (magnitude); fees/promos net into their lines
            for ch in (it.get('ItemChargeAdjustmentList') or []):
                t = ch.get('ChargeType') or ''
                a = _amt(ch.get('ChargeAmount'))
                if t == 'Tax':
                    signed['sales_tax_refunds'] += a   # negative
                    continue
                if 'tax' in t.lower():
                    continue
                if t == 'Principal':
                    signed['returns'] += -a           # a is negative → returns positive magnitude
                    _head('returns', 'Refunds — product sales', a)
                elif t in ('ShippingCharge', 'GiftWrap'):
                    signed['other_income'] += a
                    _head('other_income',
                          'Shipping credits' if t == 'ShippingCharge'
                          else 'Gift wrap credits', a)
            for fe in (it.get('ItemFeeAdjustmentList') or []):
                t = fe.get('FeeType') or ''
                a = _amt(fe.get('FeeAmount'))
                if t in ('Commission', 'FixedClosingFee', 'VariableClosingFee',
                         'RefundCommission'):
                    signed['commission'] += a
                    _head('commission', 'Refunds — selling fee credits', a)
                elif t.startswith('FBA') or t == 'ShippingChargeback':
                    signed['fba_fee'] += a
                    _head('fba_fee', 'Refunds — FBA fee credits', a)
            for pr in (it.get('PromotionAdjustmentList') or []):
                a = _amt(pr.get('PromotionAmount'))
                signed['promo'] += a
                _head('promo', 'Promotional rebates — refunds', a)

    # Advertising
    for e in events.get('ProductAdsPaymentEventList', []):
        a = _amt(e.get('transactionValue') or e.get('TransactionValue'))
        signed['ppc'] += a
        _head('ppc', 'Cost of Advertising', a)

    # Service fees (storage / SAS / subscription / AWD / etc.)
    for e in events.get('ServiceFeeEventList', []):
        for fe in (e.get('FeeList') or []):
            desc = e.get('FeeDescription') or fe.get('FeeType') or ''
            key = _classify_service_fee(desc, fe.get('FeeType'))
            a = _amt(fe.get('FeeAmount'))
            signed[key] += a
            _head(key, f'Service Fee — {desc[:80] or "(no description)"}', a)

    # Adjustments / reimbursements → other income
    for e in events.get('AdjustmentEventList', []):
        t = (e.get('AdjustmentType') or 'Adjustment')[:60]
        a = _amt(e.get('AdjustmentAmount'))
        signed['other_income'] += a
        _head('other_income', f'Adjustment — {t}', a)
        for it in (e.get('AdjustmentItemList') or []):
            a = _amt(it.get('TotalAmount'))
            signed['other_income'] += a
            _head('other_income', f'Adjustment — {t}', a)

    # COGS (client method): net units × month-effective cost
    cost = month_cogs_unit_map(marketplace, start)
    def uc(s): return cost.get(s, 0.0)
    cogs_gross = sum(order_units[s] * uc(s) for s in order_units)
    cogs_ret   = sum(refund_units[s] * uc(s) for s in refund_units)
    missing = sorted(s for s in order_units if s not in cost)

    lines = {}
    for k, v in signed.items():
        bd = {lbl: round(a, 2) for lbl, a in
              sorted(heads.get(k, {}).items(), key=lambda x: -abs(x[1]))
              if abs(a) >= 0.005}
        lines[k] = {'amount': round(v if k in _INCOME_KEYS else abs(v), 2),
                     'units': 0, 'breakdown': bd}
    lines.setdefault('gross_sales', {'amount': 0.0, 'units': 0})['units'] = sum(order_units.values())
    lines.setdefault('returns', {'amount': 0.0, 'units': 0})['units'] = sum(refund_units.values())
    lines['cogs'] = {'amount': round(cogs_gross - cogs_ret, 2), 'units': 0}

    return {'lines': lines,
            'order_units_sku': dict(order_units),
            'refund_units_sku': dict(refund_units),
            'missing_cogs': missing,
            'event_counts': {k: len(v) for k, v in events.items()}}


def sync_finances_month(marketplace: str, month: date_cls, user=None) -> dict:
    """Fetch + store one month from the Finances API (provisional actuals)."""
    from django.db import transaction
    from django.conf import settings
    from .models import SettlementLineActual, UnifiedSkuUnits

    res = fetch_finances_month(marketplace, month)
    month_start = month.replace(day=1)
    ccy = (getattr(settings, 'AMAZON_MARKETPLACES', {})
           .get(marketplace, {}).get('currency', 'USD'))

    with transaction.atomic():
        SettlementLineActual.objects.filter(
            marketplace=marketplace, month=month_start).delete()
        for k, v in res['lines'].items():
            SettlementLineActual.objects.create(
                marketplace=marketplace, month=month_start, line_key=k,
                amount=v['amount'], units=v.get('units', 0),
                breakdown=v.get('breakdown', {}),
                currency=ccy, source_note='finances_api')
        UnifiedSkuUnits.objects.filter(
            marketplace=marketplace, month=month_start).delete()
        all_skus = set(res['order_units_sku']) | set(res['refund_units_sku'])
        UnifiedSkuUnits.objects.bulk_create([
            UnifiedSkuUnits(marketplace=marketplace, month=month_start, sku=s,
                            order_units=res['order_units_sku'].get(s, 0),
                            refund_units=res['refund_units_sku'].get(s, 0))
            for s in all_skus], batch_size=1000)

    ln = res['lines']
    return {
        'status': 'ok',
        'source': 'finances_api',
        'month': month_start.isoformat(),
        'net_sales': round(ln.get('gross_sales', {}).get('amount', 0)
                            - ln.get('returns', {}).get('amount', 0)
                            - ln.get('promo', {}).get('amount', 0), 2),
        'cogs': ln.get('cogs', {}).get('amount', 0),
        'event_counts': res['event_counts'],
        'missing_cogs': len(res['missing_cogs']),
        'message': (f'Synced {month_start:%Y-%m} from Amazon Finances API '
                    f'(provisional — may differ ~1% from books until the '
                    f'month-end Transaction report is uploaded).'),
    }
