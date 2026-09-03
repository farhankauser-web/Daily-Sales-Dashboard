"""
apps/dashboard/unified_txn_importer.py — parse the Seller Central
"Unified Transaction" / Date-Range Transaction report (the single authoritative
posted-date source that ties to the client's books) into monthly P&L line
actuals.

One report → the whole P&L. Each row carries type + description + per-column
amounts. We classify into pnl_lines feed keys and store into
SettlementLineActual (source_note='unified'), which the P&L engine reads.

COGS is computed here the client's way: (order units − refund units) per SKU ×
uploaded COGS, stored as line_key='cogs'.

Excluded entirely: the Transfer row (bank disbursement, not a P&L item) and all
tax columns (collected = remitted, pass-through).
"""
from __future__ import annotations

import csv
import logging
import re
import io
from collections import defaultdict
from datetime import date as date_cls, datetime

_MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}


def _parse_txn_date(s: str) -> date_cls | None:
    """'May 1, 2026 12:02:12 AM PDT' → date(2026,5,1). Tolerant of variants."""
    s = (s or '').strip()
    if not s:
        return None
    parts = s.replace(',', '').split()
    if len(parts) >= 3 and parts[0][:3] in _MONTHS:
        try:
            return date_cls(int(parts[2]), _MONTHS[parts[0][:3]], int(parts[1]))
        except (ValueError, IndexError):
            return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except ValueError:
        return None

# Income keys keep natural sign; everything else stored as magnitude.
# Keys stored with their raw sign (everything else is stored as magnitude).
# The sales-tax keys carry the report's itemized VAT (UK splits it out;
# AE/SA prices are tax-inclusive and these stay 0) — the P&L engine uses
# them to gross-up revenue lines and deduct the ACTUAL VAT exactly once.
logger = logging.getLogger(__name__)

_INCOME_KEYS = {'gross_sales', 'other_income',
                'sales_tax', 'sales_tax_refunds', 'promo_tax'}


def _num(s) -> float:
    s = (s or '').replace(',', '').replace('$', '').strip()
    if not s:
        return 0.0
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return 0.0


def _find_header(rows: list[list[str]]) -> int:
    """Unified report has a definitions preamble; the real header row contains
    'date/time' AND 'product sales' as separate columns."""
    for i, r in enumerate(rows[:25]):
        low = [c.strip().lower() for c in r]
        if 'date/time' in low and 'product sales' in low:
            return i
    return 0


def _classify_other(desc: str, ttype: str,
                    unmatched: list | None = None) -> str | None:
    """Map a non-Order/Refund fee row (by description/type) to a P&L line key.
    Returns None to skip (e.g. bank Transfer).

    `unmatched` — when a list is passed, every row that reaches the catch-all
    is appended to it. The catch-all is not a safe default: it silently routed
    'Others - Seller Rewards' (an Amazon Gulf INCOME credit) into
    other_logistics for months. Callers log what lands there so a row type
    Amazon introduces surfaces on the first month, not the twelfth.
    """
    d = (desc or '').lower()
    t = (ttype or '').lower()
    if t == 'transfer':
        return None                                   # bank disbursement

    # Other Income (client definition): these transaction types + Grade-and-
    # Resell are booked as income (shipping/giftwrap credits handled separately).
    # 'Others' is Amazon's own Income-section bucket. In AE/SA it carries
    # 'Seller Rewards' — the Gulf seller-incentive credit, AED 13,287 in one
    # month. It matched no rule, fell to the catch-all, and was booked as an
    # other_logistics COST. Income lost it and expenses gained it, so AE July
    # contribution came out 24% under Amazon's own statement. USA and UK never
    # ship this row type, which is why four months of it went unseen.
    if t in ('adjustment', 'amazon charges', 'fee adjustment', 'liquidations',
             'others') \
            or 'grade and resell' in d or 'reimburs' in d or 'reimbursement' in t:
        return 'other_income'

    if 'advertis' in d or 'deal participation' in d or 'deal performance' in d:
        return 'ppc'                                  # incl. 'Refund for Advertiser' credit
    if 'premium services' in d:
        return 'account_management'                   # SAS — separate, NOT commission
    if 'subscription' in d:
        return 'subscription'
    if 'awd transportation' in d:
        return 'awd_transportation'
    if 'awd processing' in d:
        return 'awd_processing'
    if 'awd storage' in d:
        return 'awd_storage'
    if 'long-term' in d or 'storage' in d:
        return 'storage_fee'
    if 'inbound' in d:
        return 'inbound_transportation'
    if 'removal' in d or 'disposal' in d:
        return 'other_logistics'
    # Type-only rules, for rows Amazon ships with a BLANK description.
    # 'FBA Inventory Fee' with no description is the charge Amazon's own
    # summary calls "FBA inventory and inbound services fees" — $6,170.88 in
    # USA July 2026. With no description text it fell through every rule
    # above and landed in the catch-all as other_logistics. Verified against
    # the July Summary PDF.
    if t == 'fba inventory fee':
        return 'inbound_transportation'

    if 'customer returns' in d:
        return 'fba_fee'
    if 'mcf' in d or 'multi-channel' in d or 'multichannel' in d:
        return 'fba_fee'
    if unmatched is not None:
        unmatched.append(f'{ttype or "?"} - {desc or "(no description)"}')
    return 'other_logistics'                          # catch-all (small misc fees)


def parse_unified_csv(file_bytes: bytes, marketplace: str = 'usa',
                       month: date_cls | None = None) -> dict:
    """
    Returns:
      {
        'lines':        {line_key: {'amount': float, 'units': int}},
        'order_units':  int, 'refund_units': int,
        'missing_cogs': [sku, ...],   # SKUs sold with no uploaded COGS
        'rows_parsed':  int,
      }
    Amounts: income keys signed (+), cost keys stored as magnitude.
    """
    text = file_bytes.decode('utf-8-sig', errors='replace')
    rows = list(csv.reader(io.StringIO(text)))
    h = _find_header(rows)

    # ── refuse to parse a file we do not understand ─────────────────────────
    # _find_header looks for the literal English column names 'date/time' and
    # 'product sales'. A localised report (Germany ships 'Datum/Uhrzeit' and
    # 'Umsätze') matches nothing, _find_header falls back to row 0, and every
    # subsequent lookup returns '' — so the parser produced a complete set of
    # zero-valued lines and reported success. Written to storage that silently
    # replaces a real P&L with zeros.
    #
    # Measured: DE July 2026, 903,598 bytes of valid report, 0 rows parsed,
    # "DONE: 1 imported, 0 failed".
    #
    # A file with content that yields no header is a FORMAT failure, not an
    # empty month. Fail loudly; a missing month is recoverable, a month
    # silently zeroed is not.
    _hdr = [c.strip().lower() for c in (rows[h] if h < len(rows) else [])]
    if 'date/time' not in _hdr or 'product sales' not in _hdr:
        raise ValueError(
            f'Unrecognised report format for {marketplace!r}: no English '
            f'header row found in {len(file_bytes):,} bytes. This is expected '
            f'for localised marketplaces (DE ships "Datum/Uhrzeit"/"Umsätze") '
            f'and needs the header-mapping layer. Refusing to parse rather '
            f'than write a month of zeros.')
    hdr = rows[h]
    I = {c.strip().lower(): i for i, c in enumerate(hdr)}

    def cell(r, name):
        i = I.get(name)
        return r[i] if i is not None and len(r) > i else ''

    # Per-key signed accumulation (+ per-head composition for drill-down)
    signed = defaultdict(float)
    heads: dict = defaultdict(lambda: defaultdict(float))

    def _head(line_key, label, amt):
        if amt:
            heads[line_key][label] += amt

    def _norm_desc(d: str) -> str:
        # group per-ASIN / per-period rows under one head
        d = re.sub(r'\s+for ASIN:.*$', '', d or '')
        d = re.sub(r'\s*\(\d{4}-\d{2}-\d{2}[^)]*\)', '', d)
        return d.strip()[:80] or '(no description)'
    units  = defaultdict(int)
    order_units_sku  = defaultdict(int)
    refund_units_sku = defaultdict(int)

    # Cash-flow capture
    payouts: list[dict] = []          # Transfer rows → bank disbursements
    cash_total = 0.0                  # sum of 'total' col incl transfers
    cash_deferred = 0.0               # 'total' of rows with status Deferred
    cash_released = 0.0

    unmatched: list[str] = []
    n = 0
    for r in rows[h + 1:]:
        if I.get('total') is not None and len(r) <= I['total']:
            continue
        t   = cell(r, 'type').strip()
        if not t:
            continue
        n += 1
        desc = cell(r, 'description').strip()
        sku  = cell(r, 'sku').strip().upper()
        try:
            qty = int(float(cell(r, 'quantity') or 0))
        except (TypeError, ValueError):
            qty = 0

        ps   = _num(cell(r, 'product sales'))
        promo = _num(cell(r, 'promotional rebates'))
        sf   = _num(cell(r, 'selling fees'))
        fb   = _num(cell(r, 'fba fees'))
        otf  = _num(cell(r, 'other transaction fees'))
        ot   = _num(cell(r, 'other'))
        # Buyer-paid shipping credit: US report calls it 'shipping credits',
        # UK/EU report calls the same column 'postage credits'.
        ship_credits = (_num(cell(r, 'shipping credits'))
                        + _num(cell(r, 'postage credits')))
        gw_credits   = _num(cell(r, 'gift wrap credits'))

        # ── cash flow ──
        row_total = _num(cell(r, 'total'))
        status = cell(r, 'transaction status').strip().lower()
        if t == 'Transfer':
            d_dt = _parse_txn_date(cell(r, 'date/time'))
            if row_total:
                payouts.append({'date': d_dt, 'amount': abs(row_total),
                                 'description': desc[:120]})
            continue                                   # not a P&L row
        cash_total += row_total
        if status == 'deferred':
            cash_deferred += row_total
        elif status == 'released':
            cash_released += row_total

        # Itemized output VAT (UK splits every credit's VAT into its own
        # column; AE/SA are tax-inclusive so these are all 0). Captured in
        # full so nothing drops — grossed up onto revenue then deducted in the
        # VAT line, net-zero to profit. Covers ALL row types incl. retrocharge
        # (cross-border VAT corrections) and liquidations.
        #   NOTE: 'marketplace withheld tax' is deliberately EXCLUDED — it is
        #   the VAT Amazon (as marketplace facilitator) already remitted on our
        #   behalf; it is a subset of 'product sales tax' we've counted, so
        #   counting it again would double-deduct. It affects cash only (the
        #   'total' column, read by the Cash Flow page), never P&L.
        stax = (_num(cell(r, 'product sales tax'))
                + _num(cell(r, 'sales tax collected'))
                + _num(cell(r, 'shipping credits tax'))
                + _num(cell(r, 'gift wrap credits tax'))
                + _num(cell(r, 'giftwrap credits tax')))
        if stax:
            if 'refund' in t.lower():
                signed['sales_tax_refunds'] += stax
            else:
                signed['sales_tax'] += stax
        ptax = _num(cell(r, 'promotional rebates tax'))
        if ptax:
            signed['promo_tax'] += ptax

        # Promotions (any type) → promo
        if promo:
            signed['promo'] += promo
            _head('promo', 'Promotional rebates — orders'
                  if t == 'Order' else 'Promotional rebates — refunds/other',
                  promo)

        # Buyer-paid shipping & gift-wrap credits → Other Income (per client).
        # Signed: refund-side credits are negative and net it down.
        if ship_credits or gw_credits:
            signed['other_income'] += (ship_credits + gw_credits)
            _head('other_income', 'Shipping credits', ship_credits)
            _head('other_income', 'Gift wrap credits', gw_credits)

        if t == 'Order':
            signed['gross_sales'] += ps
            _head('gross_sales', 'Orders — product sales', ps)
            if sku and qty:
                order_units_sku[sku] += qty
            if sf:
                signed['commission'] += sf
                _head('commission', 'Orders — selling fees (referral)', sf)
            if fb:
                signed['fba_fee'] += fb
                _head('fba_fee', 'Orders — FBA fulfilment fees', fb)
            if otf or ot:
                signed['other_logistics'] += (otf + ot)   # shipping chargebacks etc.
                _head('other_logistics', 'Orders — other transaction fees', otf + ot)
        elif t == 'Refund':
            signed['returns'] += ps                        # negative
            _head('returns', 'Refunds — product sales', ps)
            if sku and qty:
                refund_units_sku[sku] += abs(qty)
            if sf:
                signed['commission'] += sf             # refund credit nets it down
                _head('commission', 'Refunds — selling fee credits', sf)
            if fb:
                signed['fba_fee'] += fb
                _head('fba_fee', 'Refunds — FBA fee credits', fb)
            if otf or ot:
                signed['other_logistics'] += (otf + ot)
                _head('other_logistics', 'Refunds — other transaction fees', otf + ot)
        else:
            # Non-order fee/charge rows — classify by description, use the
            # row's net fee amount across the fee columns.
            amt = sf + fb + otf + ot
            if t.startswith('Liquidations'):
                # Liquidation revenue sits in 'product sales', not in any fee
                # column, so it must be added explicitly. 'Liquidations
                # Adjustments' is a SEPARATE row type that carries value the
                # same way — an exact-match on 'Liquidations' silently dropped
                # it. Measured: UK 2026-07, two rows, -1.20 and -0.25, the
                # whole of the GBP 1.45 residual against Amazon's own Summary.
                # Small here; unbounded in a month with real liquidation
                # activity, and silent either way.
                amt += ps
            if amt:
                key = _classify_other(desc, t, unmatched)
                if key:
                    signed[key] += amt
                    _head(key, f'{t} — {_norm_desc(desc)}', amt)

    if unmatched:
        from collections import Counter
        top = Counter(unmatched).most_common(15)
        logger.warning(
            'unified_txn %s: %d row(s) matched no classification rule and were '
            'booked to other_logistics. Add explicit rules for these: %s',
            marketplace, len(unmatched), top)

    # ── COGS (client method): net units × uploaded COGS per SKU ──────────
    # Month-aware: use the COGSEntry effective for the REPORT month (latest
    # entry with month <= report month), not simply the newest entry.
    from .cogs_recalc import month_cogs_unit_map
    target_month = (month or date_cls.today()).replace(day=1)
    cogs_unit = month_cogs_unit_map(marketplace, target_month)

    def uc(s):
        return cogs_unit.get(s, 0.0)

    cogs_gross = sum(order_units_sku[s] * uc(s) for s in order_units_sku)
    cogs_ret   = sum(refund_units_sku[s] * uc(s) for s in refund_units_sku)
    missing = sorted(s for s in order_units_sku if s not in cogs_unit)

    order_units  = sum(order_units_sku.values())
    refund_units = sum(refund_units_sku.values())

    # ── Assemble final line dict (income signed, costs magnitude) ────────
    lines = {}
    for key, val in signed.items():
        # Cost keys are stored as a magnitude. Negate rather than abs():
        # for a genuine cost `val` is negative and -val == abs(val), so this
        # is bit-identical wherever the classification is right. Where a cost
        # key nets to a CREDIT, abs() flipped it back into a cost — silently,
        # and on top of the income it had already been taken from, so the
        # error landed twice. -val reports the credit as a credit.
        amt = val if key in _INCOME_KEYS else -val
        if key not in _INCOME_KEYS and amt < 0:
            logger.warning(
                'unified_txn %s: cost line %r nets to a CREDIT of %.2f. '
                'Usually a misclassified income row, not a real refund '
                'balance. Heads: %s',
                marketplace, key, -amt, dict(heads.get(key, {})))
        bd = {lbl: round(v, 2) for lbl, v in
              sorted(heads.get(key, {}).items(), key=lambda x: -abs(x[1]))
              if abs(v) >= 0.005}
        lines[key] = {'amount': round(amt, 2), 'units': 0, 'breakdown': bd}
    lines.setdefault('gross_sales', {'amount': 0.0, 'units': 0})['units'] = order_units
    lines.setdefault('returns', {'amount': 0.0, 'units': 0})['units'] = refund_units
    lines['cogs'] = {'amount': round(cogs_gross - cogs_ret, 2), 'units': 0}

    return {
        'lines':        lines,
        'order_units':  order_units,
        'refund_units': refund_units,
        'cogs_gross':   round(cogs_gross, 2),
        'cogs_returns': round(cogs_ret, 2),
        'missing_cogs': missing,
        'rows_parsed':  n,
        # per-SKU units — persisted so COGS can be recalculated later
        'order_units_sku':  dict(order_units_sku),
        'refund_units_sku': dict(refund_units_sku),
        # ── cash flow ──
        'payouts':       payouts,
        'cash_net_proceeds': round(cash_total, 2),   # earned into Amazon balance
        'cash_payouts':      round(sum(p['amount'] for p in payouts), 2),
        'cash_deferred':     round(cash_deferred, 2),
        'cash_released':     round(cash_released, 2),
    }


def import_unified_csv_bytes(*, file_bytes: bytes, original_filename: str,
                             marketplace: str, month: date_cls, user=None) -> dict:
    """Parse + store the unified report for one region+month. Replaces any
    prior actuals for that month (idempotent)."""
    from django.db import transaction
    from django.conf import settings
    from .models import (SettlementLineActual, ManualPnLUpload, AmazonPayout,
                          UnifiedSkuUnits)

    res = parse_unified_csv(file_bytes, marketplace=marketplace, month=month)
    native_ccy = (getattr(settings, 'AMAZON_MARKETPLACES', {})
                  .get(marketplace, {}).get('currency', 'USD'))
    month_start = month.replace(day=1)

    with transaction.atomic():
        # Clear prior actuals for this region+month, then write fresh.
        SettlementLineActual.objects.filter(
            marketplace=marketplace, month=month_start).delete()
        for line_key, v in res['lines'].items():
            SettlementLineActual.objects.create(
                marketplace=marketplace, month=month_start, line_key=line_key,
                amount=v['amount'], units=v.get('units', 0),
                breakdown=v.get('breakdown', {}),
                currency=native_ccy, source_note='unified')
        # Cash-flow aggregates (line keys reserved with cash_ prefix — the
        # P&L engine ignores them; the Cash Flow page reads them).
        for ck in ('cash_net_proceeds', 'cash_payouts',
                   'cash_deferred', 'cash_released'):
            SettlementLineActual.objects.create(
                marketplace=marketplace, month=month_start, line_key=ck,
                amount=res[ck], units=0,
                currency=native_ccy, source_note='unified')
        # Individual payout events
        AmazonPayout.objects.filter(
            marketplace=marketplace, month=month_start).delete()
        for p in res['payouts']:
            AmazonPayout.objects.create(
                marketplace=marketplace, month=month_start,
                payout_date=p['date'] or month_start,
                amount=p['amount'], description=p['description'])
        # Per-SKU units — enables later COGS recalculation for this month
        UnifiedSkuUnits.objects.filter(
            marketplace=marketplace, month=month_start).delete()
        all_skus = set(res['order_units_sku']) | set(res['refund_units_sku'])
        UnifiedSkuUnits.objects.bulk_create([
            UnifiedSkuUnits(
                marketplace=marketplace, month=month_start, sku=s,
                order_units=res['order_units_sku'].get(s, 0),
                refund_units=res['refund_units_sku'].get(s, 0))
            for s in all_skus], batch_size=1000)
        audit = ManualPnLUpload.objects.create(
            marketplace=marketplace, month=month_start,
            original_filename=original_filename[:256],
            rows_imported=res['rows_parsed'],
            lines_matched=len(res['lines']),
            lines_unmatched=len(res['missing_cogs']),
            status='ok', uploaded_by=user)

    return {
        'status': 'ok',
        'message': (f'Imported {res["rows_parsed"]} rows for {month_start:%Y-%m}. '
                    f'Net Sales basis loaded. '
                    f'{len(res["missing_cogs"])} SKU(s) sold without COGS.'),
        'lines': res['lines'],
        'order_units': res['order_units'],
        'refund_units': res['refund_units'],
        'missing_cogs': res['missing_cogs'][:50],
        'audit_id': audit.id,
    }


def sync_unified_from_api(marketplace: str, month: date_cls, user=None) -> dict:
    """
    One-click authoritative sync: generate the Payments Date-Range
    Transaction report via SP-API — byte-identical to the manual Seller
    Central monthly unified download (deferred transactions included, so it
    ties to the books) — and run it through the normal importer.
    Blocking: report generation typically takes 1–5 minutes.
    """
    from calendar import monthrange
    from apps.amazon_api.models import AmazonAPIConfig
    from apps.amazon_api.services import SPAPIClient

    cfg = AmazonAPIConfig.objects.filter(
        marketplace=marketplace, is_active=True).first()
    if not cfg:
        raise RuntimeError(f'no active SP-API config for {marketplace}')
    client = SPAPIClient(cfg)

    start = month.replace(day=1)
    end = start.replace(day=monthrange(start.year, start.month)[1])
    raw = client.fetch_date_range_transaction_report(start, end)

    # Sanity-parse BEFORE persisting so a malformed/empty report can never
    # overwrite good actuals with zeros.
    probe = parse_unified_csv(raw, marketplace=marketplace, month=start)
    if not probe['rows_parsed']:
        raise RuntimeError(
            'date-range transaction report downloaded but parsed 0 rows — '
            'header layout not recognised (check marketplace report language)')

    res = import_unified_csv_bytes(
        file_bytes=raw,
        original_filename=f'api-sync-{marketplace}-{start:%Y-%m}-transaction.csv',
        marketplace=marketplace, month=start, user=user)

    ln = res['lines']
    return {
        'status': 'ok',
        'source': 'date_range_report',
        'month': start.isoformat(),
        'net_sales': round(ln.get('gross_sales', {}).get('amount', 0)
                           - ln.get('returns', {}).get('amount', 0)
                           - ln.get('promo', {}).get('amount', 0), 2),
        'cogs': ln.get('cogs', {}).get('amount', 0),
        'missing_cogs': len(res['missing_cogs']),
        'message': (f'Synced {start:%Y-%m} from the Amazon Date-Range '
                    f'Transaction report — same source as the manual unified '
                    f'upload, ties to the books (deferred transactions '
                    f'included). {res["message"]}'),
    }
