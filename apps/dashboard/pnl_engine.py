"""
apps/dashboard/pnl_engine.py — assemble a Management P&L statement.

build_statement(marketplace, month) returns a fully-resolved statement for
one region+month in that region's NATIVE currency:

    auto lines     ← SettlementLineActual (settled actuals)
                     with a fallback to operational DailyMetric/COGS when no
                     settlement has landed yet for the month (so recent months
                     aren't blank during the ~2-week settlement lag).
    manual lines   ← MonthlyPnLEntry (regional currency)
    computed lines ← formulas below
    metrics        ← units, ARPU, per-unit fees

build_consolidated(month) sums all regions into USD via MonthlyFXRate.

The statement is a dict keyed by line_key → {amount, source, ...} plus an
ordered `rows` list ready for the renderer.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings

from .pnl_lines import PNL_LINES, LINE_BY_KEY, SECTION_LABELS


def _month_bounds(month: date) -> tuple[date, date]:
    """First and last day of the month containing `month`."""
    start = month.replace(day=1)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    end = nxt - timedelta(days=1)
    return start, end


def currency_for(marketplace: str) -> str:
    return (getattr(settings, 'AMAZON_MARKETPLACES', {})
            .get(marketplace, {}).get('currency', 'USD'))


# ── auto-feed: pull the 🟢 lines for a region+month ──────────────────────────
def _auto_feed(marketplace: str, month_start: date) -> dict[str, dict]:
    """
    Returns {line_key: {'amount': float, 'units': int, 'src': str}} for the
    auto (settlement-fed) lines. Falls back to operational data per-line when
    settlement hasn't landed.
    """
    from .models import (
        SettlementLineActual, DailyMetric, DailySkuSnapshot, SkuPpcAllocation,
    )
    from django.db.models import Sum

    month_a, month_b = _month_bounds(month_start)
    out: dict[str, dict] = {}

    # 1) Settlement actuals (preferred)
    settle = {r.line_key: r for r in SettlementLineActual.objects.filter(
        marketplace=marketplace, month=month_start)}
    breakdowns = {k: (getattr(r, 'breakdown', None) or {})
                  for k, r in settle.items()}
    for key, row in settle.items():
        out[key] = {'amount': float(row.amount or 0),
                    'units':  int(row.units or 0),
                    'src':    'settlement'}

    has_settlement = bool(settle)

    # 2) Operational fallback for lines settlement didn't provide.
    #    Uses the same numbers the daily dashboard already shows.
    def _need(k):  # missing or zero from settlement
        return k not in out

    if _need('gross_sales') or _need('cogs') or _need('commission') or _need('fba_fee'):
        dm = DailyMetric.objects.filter(
            marketplace=marketplace, date__gte=month_a, date__lte=month_b,
        ).aggregate(
            rev=Sum('revenue'), cgs=Sum('cgs'),
            amz=Sum('amazon_fee'), fba=Sum('fba_fee'), ppc=Sum('ppc_spend'),
        )
        sku = DailySkuSnapshot.objects.filter(
            marketplace=marketplace, date__gte=month_a, date__lte=month_b,
        ).aggregate(units=Sum('qty'))

        if _need('gross_sales'):
            out['gross_sales'] = {'amount': float(dm['rev'] or 0),
                                   'units': int(sku['units'] or 0),
                                   'src': 'operational'}
        if _need('cogs'):
            out['cogs'] = {'amount': float(dm['cgs'] or 0), 'units': 0,
                            'src': 'operational'}
        if _need('commission'):
            out['commission'] = {'amount': float(dm['amz'] or 0), 'units': 0,
                                  'src': 'operational'}
        if _need('fba_fee'):
            out['fba_fee'] = {'amount': float(dm['fba'] or 0), 'units': 0,
                               'src': 'operational'}
        if _need('ppc'):
            out['ppc'] = {'amount': float(dm['ppc'] or 0), 'units': 0,
                           'src': 'operational'}

    # 3) PPC — prefer SkuPpcAllocation sum if present, else operational above
    if _need('ppc'):
        ppc = SkuPpcAllocation.objects.filter(
            marketplace=marketplace, date__gte=month_a, date__lte=month_b,
        ).aggregate(s=Sum('sku_ppc_spend'))['s']
        if ppc is not None:
            out['ppc'] = {'amount': float(ppc), 'units': 0, 'src': 'allocation'}

    # Ensure every auto line exists (zero if truly absent)
    for ln in PNL_LINES:
        if ln['source'] == 'auto' and ln['feed'] and ln['feed'] not in out:
            out[ln['feed']] = {'amount': 0.0, 'units': 0, 'src': 'none'}

    out['_has_settlement'] = has_settlement
    out['_breakdowns'] = breakdowns
    return out


# ── manual lines for a region+month ──────────────────────────────────────────
def _manual_feed(marketplace: str, month_start: date) -> dict[str, dict]:
    """{line_key: {'amazon': amt, 'retail': amt}} from MonthlyPnLEntry."""
    from .models import MonthlyPnLEntry
    out: dict[str, dict] = {}
    for e in MonthlyPnLEntry.objects.filter(marketplace=marketplace, month=month_start):
        slot = out.setdefault(e.line_key, {'amazon': 0.0, 'retail': 0.0})
        slot[e.channel] = float(e.amount or 0)
    return out


def build_statement(marketplace: str, month: date) -> dict:
    """
    Full P&L for one region+month, native currency. Returns:
      {
        'marketplace', 'month', 'currency',
        'has_settlement': bool,
        'rows': [ {key,label,section,indent,source,amazon,retail,total}, ... ],
        'values': {key: total, ...},      # convenience for callers
      }
    """
    month_start = month.replace(day=1)
    auto   = _auto_feed(marketplace, month_start)
    manual = _manual_feed(marketplace, month_start)
    has_settlement = auto.pop('_has_settlement', False)
    line_breakdowns = auto.pop('_breakdowns', {})

    # Amazon-column amount per auto line (feed → value)
    def auto_amount(feed_key):
        return auto.get(feed_key, {}).get('amount', 0.0)

    # Resolve each line into amazon/retail amounts
    amazon: dict[str, float] = {}
    retail: dict[str, float] = {}

    for ln in PNL_LINES:
        k = ln['key']
        if ln['source'] == 'auto':
            amazon[k] = auto_amount(ln['feed'])
            retail[k] = 0.0
        elif ln['source'] == 'manual':
            m = manual.get(k, {})
            amazon[k] = m.get('amazon', 0.0)
            retail[k] = m.get('retail', 0.0)
        else:
            amazon[k] = 0.0
            retail[k] = 0.0

    # VAT on sales — two cases, decided by the data so it can never deduct
    # twice:
    #   1. The report itemizes VAT in separate tax columns (UK): product
    #      sales are EX-VAT → gross-up Sales/Returns/Promo with their tax
    #      and deduct the ACTUAL collected VAT once.
    #   2. No tax columns (AE/SA — prices tax-inclusive): compute
    #      vat = gross × r/(1+r) as before.
    _vr = float(getattr(settings, 'AMAZON_MARKETPLACES', {})
                .get(marketplace, {}).get('vat', 0) or 0)
    _tax_o = auto_amount('sales_tax')            # orders VAT (+)
    _tax_r = auto_amount('sales_tax_refunds')    # refunded VAT (−)
    _tax_p = auto_amount('promo_tax')            # VAT on promo rebates (−)
    if _vr and (_tax_o or _tax_r):
        amazon['sales_amazon'] = amazon.get('sales_amazon', 0.0) + _tax_o
        amazon['sales_return'] = amazon.get('sales_return', 0.0) + abs(_tax_r)
        amazon['promotional_discounts'] = (
            amazon.get('promotional_discounts', 0.0) + abs(_tax_p))
        amazon['vat'] = _tax_o + _tax_r + _tax_p
        line_breakdowns.setdefault('gross_sales', {})[
            'Orders — VAT collected'] = round(_tax_o, 2)
        line_breakdowns.setdefault('returns', {})[
            'Refunds — VAT refunded'] = round(_tax_r, 2)
        if _tax_p:
            line_breakdowns.setdefault('promo', {})[
                'Promotional rebates — VAT'] = round(_tax_p, 2)
        line_breakdowns['vat'] = {
            k: v for k, v in (('VAT collected on orders', round(-_tax_o, 2)),
                              ('VAT refunded on returns', round(-_tax_r, 2)),
                              ('VAT on promotional rebates', round(-_tax_p, 2)))
            if v}
    else:
        amazon['vat'] = (amazon.get('sales_amazon', 0.0) * _vr / (1 + _vr)) \
            if _vr else 0.0
    retail['vat'] = 0.0

    # Input VAT: Amazon charges VAT on its fees in VAT marketplaces, and the
    # report amounts are VAT-inclusive. Recoverable input VAT is not an
    # expense, so every Amazon fee line (and its heads) is booked net of VAT.
    _AMZ_FEE_LINES = ('amazon_commission', 'amazon_fulfilment_fee',
                      'amazon_ppc', 'awd_transportation', 'awd_processing',
                      'awd_storage', 'fba_inventory_storage_fee',
                      'inbound_transportation', 'other_logistics_fees',
                      'amazon_subscription_fee', 'amazon_account_management')
    _AMZ_FEE_FEEDS = ('commission', 'fba_fee', 'ppc', 'awd_transportation',
                      'awd_processing', 'awd_storage', 'storage_fee',
                      'inbound_transportation', 'other_logistics',
                      'subscription', 'account_management')
    if _vr:
        for k in _AMZ_FEE_LINES:
            if amazon.get(k):
                amazon[k] = amazon[k] / (1 + _vr)
        for f in _AMZ_FEE_FEEDS:
            if line_breakdowns.get(f):
                line_breakdowns[f] = {lbl: round(v / (1 + _vr), 2)
                                      for lbl, v in line_breakdowns[f].items()}

    # Units (from auto feed)
    units_sold     = auto.get('gross_sales', {}).get('units', 0)
    units_returned = auto.get('returns', {}).get('units', 0)

    # ── computed lines (per column) ──────────────────────────────────────
    def col(d, k):  # safe getter
        return d.get(k, 0.0)

    def compute_column(d: dict):
        net_sales = (col(d, 'sales_amazon') - col(d, 'vat')
                     - col(d, 'sales_return') - col(d, 'promotional_discounts'))
        d['net_sales'] = net_sales
        d['sales_return_pct'] = (col(d, 'sales_return') / col(d, 'sales_amazon')
                                  if col(d, 'sales_amazon') else 0.0)
        d['cost_of_sales_pct'] = (col(d, 'cost_of_sales') / net_sales
                                   if net_sales else 0.0)

        total_marketing = (col(d, 'amazon_ppc') + col(d, 'promo_other_platforms')
                            + col(d, 'sampling_cost') + col(d, 'other_marketing'))
        gross_margin = (net_sales
                        - col(d, 'cost_of_sales')
                        - col(d, 'amazon_commission')
                        - col(d, 'amazon_fulfilment_fee')
                        - col(d, 'walmart_retail_commission')
                        - total_marketing
                        + col(d, 'other_income'))
        d['gross_margin'] = gross_margin
        d['gross_margin_pct'] = gross_margin / net_sales if net_sales else 0.0

        storage = sum(col(d, k) for k in (
            'warehouse_rent', 'awd_transportation', 'awd_processing',
            'awd_storage', 'fba_inventory_storage_fee',
            'inbound_transportation', 'other_logistics_fees'))

        opex = sum(col(d, k) for k in (
            'amazon_subscription_fee', 'entertainment', 'travel_accommodation',
            'corporate_giveaways', 'employees_training', 'trademark_legal_fee',
            'inspection_charges_3p', 'courier_charges', 'mobile_laptops',
            'it_expense', 'software_system_charges', 'virtual_office_rent',
            'tax_consultancy_charges', 'audit_charges', 'product_photography',
            'bank_charges', 'trucking_cost', 'amazon_account_management',
            'other_costs'))
        d['total_operating_expenses'] = opex

        hr = sum(col(d, k) for k in (
            'hr_pakistan_dedicated', 'hr_pakistan_new_hiring', 'hr_pakistan_shared',
            'hr_uae', 'hr_shared_staff_uae', 'consultancy_bpo_usa', 'rushmore'))
        d['total_hr_cost'] = hr

        npbt = gross_margin - storage - opex - hr
        d['net_profit_before_tax'] = npbt
        d['net_margin_before_tax_pct'] = npbt / net_sales if net_sales else 0.0
        npat = npbt - col(d, 'tax_expense')
        d['net_profit_after_tax'] = npat
        d['net_margin_after_tax_pct'] = npat / net_sales if net_sales else 0.0
        return d

    amazon = compute_column(amazon)
    retail = compute_column(retail)

    # Metrics (Amazon column drives the unit metrics)
    net_units = units_sold - units_returned
    amazon['total_units_sold']   = units_sold
    amazon['inventory_returned'] = units_returned
    amazon['net_units']          = net_units
    amazon['arpu']               = (amazon['net_sales'] / net_units) if net_units else 0.0
    amazon['per_unit_cogs']      = (amazon['cost_of_sales'] / net_units) if net_units else 0.0
    amazon['per_unit_commission'] = (amazon['amazon_commission'] / net_units) if net_units else 0.0
    amazon['per_unit_fulfilment'] = (amazon['amazon_fulfilment_fee'] / net_units) if net_units else 0.0
    for mk in ('total_units_sold', 'inventory_returned', 'net_units', 'arpu',
               'per_unit_cogs', 'per_unit_commission', 'per_unit_fulfilment'):
        retail.setdefault(mk, 0.0)

    # ── build ordered rows ───────────────────────────────────────────────
    PCT_KEYS = {'sales_return_pct', 'cost_of_sales_pct', 'gross_margin_pct',
                'net_margin_before_tax_pct', 'net_margin_after_tax_pct'}
    UNIT_KEYS = {'total_units_sold', 'inventory_returned', 'net_units'}

    rows = []
    for ln in PNL_LINES:
        k = ln['key']
        a = amazon.get(k, 0.0)
        r = retail.get(k, 0.0)
        is_pct  = k in PCT_KEYS
        is_unit = k in UNIT_KEYS
        total = round(a + r, 4 if is_pct else 2) if not is_pct else round(a, 4)
        # Debit/Credit accounting view: '-' lines are debits (costs),
        # '+' lines and computed results are credits.
        debit = credit = 0.0
        if not is_pct and not is_unit and ln['source'] != 'header':
            if ln.get('sign') == '-':
                debit = abs(total)
            else:
                credit = total
        bd = (line_breakdowns.get(ln.get('feed'))
              if ln['source'] == 'auto' else None) or {}
        rows.append({
            'key':     k,
            'label':   ln['label'],
            'section': ln['section'],
            'source':  ln['source'],
            'sign':    ln.get('sign', ''),
            'indent':  ln['indent'],
            'amazon':  round(a, 4 if is_pct else 2),
            'retail':  round(r, 4 if is_pct else 2),
            'total':   total,
            'debit':   round(debit, 2),
            'credit':  round(credit, 4 if is_pct else 2),
            'breakdown': bd,
            'is_pct':  is_pct,
            'is_unit': is_unit,
            'is_header': ln['source'] == 'header',
        })

    return {
        'marketplace':    marketplace,
        'month':          month_start.isoformat(),
        'currency':       currency_for(marketplace),
        'has_settlement': has_settlement,
        'rows':           rows,
        'amazon':         amazon,
        'retail':         retail,
    }


def build_consolidated(month: date, marketplaces: list[str]) -> dict:
    """
    Sum multiple regions into a USD consolidated statement using MonthlyFXRate.
    Lines are summed in USD; % and per-unit lines are recomputed on the USD
    totals (not summed).
    """
    from .models import MonthlyFXRate
    month_start = month.replace(day=1)

    # FX lookup: currency → rate_to_usd (USD = 1.0)
    fx = {'USD': Decimal('1')}
    for r in MonthlyFXRate.objects.filter(month=month_start):
        fx[r.currency] = r.rate_to_usd

    missing_fx = set()
    summed_amazon: dict[str, float] = {}
    summed_retail: dict[str, float] = {}
    # Sum only the additive (non-pct, non-per-unit) lines; recompute the rest.
    ADDITIVE = {ln['key'] for ln in PNL_LINES
                if ln['source'] in ('auto', 'manual')
                or ln['key'] in ('total_units_sold', 'inventory_returned')}

    per_region = []
    for mp in marketplaces:
        st = build_statement(mp, month_start)
        ccy = st['currency']
        rate = fx.get(ccy)
        if rate is None:
            missing_fx.add(ccy)
            rate = Decimal('1')   # fall back 1:1 so the row still shows
        rate = float(rate)
        for k in ADDITIVE:
            summed_amazon[k] = summed_amazon.get(k, 0.0) + st['amazon'].get(k, 0.0) * rate
            summed_retail[k] = summed_retail.get(k, 0.0) + st['retail'].get(k, 0.0) * rate
        per_region.append({'marketplace': mp, 'currency': ccy, 'rate': rate,
                            'net_profit_after_tax': st['amazon'].get('net_profit_after_tax', 0.0)
                                                    + st['retail'].get('net_profit_after_tax', 0.0)})

    # Recompute derived lines on the USD totals by reusing build_statement's math
    # via a lightweight re-run: stuff summed values into the compute function.
    # Simplest: call the same column computation used above.
    # (Re-import to avoid duplicating the formula.)
    result = _recompute_consolidated(summed_amazon, summed_retail)
    result.update({
        'month':        month_start.isoformat(),
        'currency':     'USD',
        'marketplaces': marketplaces,
        'missing_fx':   sorted(missing_fx),
        'per_region':   per_region,
    })
    return result


def _recompute_consolidated(amazon: dict, retail: dict) -> dict:
    """Recompute % + per-unit lines on summed USD figures, build rows."""
    # Reuse the same per-column compute by importing the inner logic:
    # we inline it here to avoid refactoring build_statement.
    def col(d, k): return d.get(k, 0.0)

    def recompute(d):
        net_sales = (col(d, 'sales_amazon') - col(d, 'vat')
                     - col(d, 'sales_return') - col(d, 'promotional_discounts'))
        d['net_sales'] = net_sales
        d['sales_return_pct']  = col(d, 'sales_return') / col(d, 'sales_amazon') if col(d, 'sales_amazon') else 0.0
        d['cost_of_sales_pct'] = col(d, 'cost_of_sales') / net_sales if net_sales else 0.0
        total_marketing = (col(d, 'amazon_ppc') + col(d, 'promo_other_platforms')
                           + col(d, 'sampling_cost') + col(d, 'other_marketing'))
        gm = (net_sales - col(d, 'cost_of_sales') - col(d, 'amazon_commission')
              - col(d, 'amazon_fulfilment_fee') - col(d, 'walmart_retail_commission')
              - total_marketing + col(d, 'other_income'))
        d['gross_margin'] = gm
        d['gross_margin_pct'] = gm / net_sales if net_sales else 0.0
        storage = sum(col(d, k) for k in (
            'warehouse_rent', 'awd_transportation', 'awd_processing',
            'awd_storage', 'fba_inventory_storage_fee',
            'inbound_transportation', 'other_logistics_fees'))
        opex = sum(col(d, k) for k in (
            'amazon_subscription_fee', 'entertainment', 'travel_accommodation',
            'corporate_giveaways', 'employees_training', 'trademark_legal_fee',
            'inspection_charges_3p', 'courier_charges', 'mobile_laptops',
            'it_expense', 'software_system_charges', 'virtual_office_rent',
            'tax_consultancy_charges', 'audit_charges', 'product_photography',
            'bank_charges', 'trucking_cost', 'amazon_account_management', 'other_costs'))
        d['total_operating_expenses'] = opex
        hr = sum(col(d, k) for k in (
            'hr_pakistan_dedicated', 'hr_pakistan_new_hiring', 'hr_pakistan_shared',
            'hr_uae', 'hr_shared_staff_uae', 'consultancy_bpo_usa', 'rushmore'))
        d['total_hr_cost'] = hr
        npbt = gm - storage - opex - hr
        d['net_profit_before_tax'] = npbt
        d['net_margin_before_tax_pct'] = npbt / net_sales if net_sales else 0.0
        npat = npbt - col(d, 'tax_expense')
        d['net_profit_after_tax'] = npat
        d['net_margin_after_tax_pct'] = npat / net_sales if net_sales else 0.0
        return d

    amazon = recompute(amazon)
    retail = recompute(retail)

    units_sold = amazon.get('total_units_sold', 0.0)
    units_ret  = amazon.get('inventory_returned', 0.0)
    net_units  = units_sold - units_ret
    amazon['net_units'] = net_units
    amazon['arpu'] = amazon['net_sales'] / net_units if net_units else 0.0
    amazon['per_unit_cogs'] = amazon['cost_of_sales'] / net_units if net_units else 0.0
    amazon['per_unit_commission'] = amazon['amazon_commission'] / net_units if net_units else 0.0
    amazon['per_unit_fulfilment'] = amazon['amazon_fulfilment_fee'] / net_units if net_units else 0.0

    PCT_KEYS = {'sales_return_pct', 'cost_of_sales_pct', 'gross_margin_pct',
                'net_margin_before_tax_pct', 'net_margin_after_tax_pct'}
    UNIT_KEYS = {'total_units_sold', 'inventory_returned', 'net_units'}
    rows = []
    for ln in PNL_LINES:
        k = ln['key']
        a = amazon.get(k, 0.0); r = retail.get(k, 0.0)
        is_pct = k in PCT_KEYS
        rows.append({
            'key': k, 'label': ln['label'], 'section': ln['section'],
            'source': ln['source'], 'indent': ln['indent'],
            'amazon': round(a, 4 if is_pct else 2),
            'retail': round(r, 4 if is_pct else 2),
            'total':  round(a, 4) if is_pct else round(a + r, 2),
            'is_pct': is_pct, 'is_unit': k in UNIT_KEYS,
            'is_header': ln['source'] == 'header',
        })
    return {'rows': rows, 'amazon': amazon, 'retail': retail}
