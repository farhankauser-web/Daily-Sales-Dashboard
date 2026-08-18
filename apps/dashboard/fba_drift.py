"""
apps/dashboard/fba_drift.py — compare uploaded FBA fees vs. settlement actuals.

For each product in a marketplace, this helper joins:
  • FBAFeeRate (the uploaded value — most-recent effective_from ≤ today)
  • SkuFeeActual (per-day actuals from settlement reports, last N days)

and emits a drift row per SKU. The view layer renders these as a table;
the alerts engine reads them too to flag 🔴 SKUs as actionable alerts.

No external services touched — this is pure DB read + arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from decimal import Decimal


# ── thresholds (tunable) ─────────────────────────────────────────────────────
WINDOW_DAYS              = 14    # rolling window for "actual" avg
VOLUME_FLOOR_UNITS       = 10    # min units in window — otherwise hide as noise
WARN_PCT                 = 2.0   # |pct| > this AND impact > WARN_IMPACT  → 🟡
WARN_IMPACT              = 50.0  # |delta × units| in USD → 🟡
CRITICAL_PCT             = 8.0   # |pct| > this OR impact > CRITICAL_IMPACT → 🔴
CRITICAL_IMPACT          = 500.0


@dataclass
class DriftRow:
    sku:                str
    asin:               str
    product_name:       str
    brand:              str
    product_family:     str
    uploaded_fee:       float
    actual_fee_avg:     float      # weighted avg over WINDOW_DAYS
    actual_fee_latest:  float      # most recent posted-date fee_per_unit
    actual_units:       int        # total units in window
    actual_latest_date: date | None
    delta:              float      # actual_fee_avg - uploaded_fee (signed)
    pct:                float      # delta / uploaded_fee × 100 (signed)
    dollar_impact:      float      # |delta| × actual_units
    status:             str        # 'ok' | 'warn' | 'critical' | 'no_upload'

    def as_dict(self) -> dict:
        d = asdict(self)
        d['actual_latest_date'] = (
            self.actual_latest_date.isoformat()
            if self.actual_latest_date else None
        )
        return d


def _classify(pct: float, impact: float, uploaded_fee: float) -> str:
    """Map (pct drift, $ impact, uploaded fee presence) → status flag."""
    if uploaded_fee <= 0:
        return 'no_upload'
    abs_pct = abs(pct)
    if abs_pct >= CRITICAL_PCT or impact >= CRITICAL_IMPACT:
        return 'critical'
    if abs_pct >= WARN_PCT and impact >= WARN_IMPACT:
        return 'warn'
    return 'ok'


def compute_drift(
    marketplace:    str,
    *,
    window_days:    int = WINDOW_DAYS,
    today:          date | None = None,
    include_zero_volume: bool = False,
) -> list[DriftRow]:
    """
    Build a list of DriftRow for every SKU in `marketplace` that has either
    (a) any FBAFeeRate uploaded OR (b) any SkuFeeActual row in the window.

    Args:
        window_days:        How many days of settlement data to roll up.
        today:              Override today's date (useful for tests).
        include_zero_volume: If True, keep SKUs with units below the floor.

    Returns:
        list[DriftRow], sorted descending by dollar_impact.
    """
    from .models import COGSEntry, FBAFeeRate, Product, SkuFeeActual

    today = today or date.today()

    # The "actual" window is anchored to the most recent settlement data we
    # HAVE, not to the calendar. Settlement reports arrive weeks in arrears, so
    # a window of `today - 14 days` is routinely empty and the page then shows
    # nothing at all — which reads as "no drift" when it actually means "no
    # data yet". Anchoring to the latest actual keeps the comparison populated
    # and honest; `window_end` is reported so the UI can state the real period.
    window_end = (SkuFeeActual.objects
                  .filter(marketplace=marketplace, date__lte=today)
                  .order_by('-date')
                  .values_list('date', flat=True).first()) or today
    window_start = window_end - timedelta(days=window_days)
    month_start  = today.replace(day=1)

    # 1) Load most-recent uploaded FBA rate per product. Sync.py uses the
    #    same fallback chain so the drift compare reads what the dashboard
    #    actually charges against:
    #       a) FBAFeeRate (date-effective) — preferred
    #       b) COGSEntry.shipping_cost — fallback when no FBAFeeRate exists
    uploaded_by_pid: dict[int, float] = {}

    rates_qs = (FBAFeeRate.objects
                 .filter(product__marketplace=marketplace,
                          effective_from__lte=today)
                 .order_by('product_id', '-effective_from')
                 .values_list('product_id', 'effective_from', 'fba_fee_per_unit'))
    for pid, _eff, fee in rates_qs:
        if pid not in uploaded_by_pid:
            uploaded_by_pid[pid] = float(fee or 0)

    # COGS fallback — most-recent month with shipping_cost > 0, only for
    # products not already covered by FBAFeeRate
    cogs_qs = (COGSEntry.objects
                .filter(product__marketplace=marketplace,
                         month__lte=month_start)
                .order_by('product_id', '-month')
                .values_list('product_id', 'month', 'shipping_cost'))
    for pid, _m, sc in cogs_qs:
        if pid in uploaded_by_pid:
            continue
        if not sc:
            continue
        uploaded_by_pid[pid] = float(sc)

    # 2) Load product dim (sku/asin/title/brand/category) keyed by sku
    products_qs = (Product.objects
                    .filter(marketplace=marketplace)
                    .values('id', 'sku', 'asin', 'title', 'brand', 'category'))
    product_by_sku: dict[str, dict] = {p['sku']: p for p in products_qs if p['sku']}

    # 3) Load all actual fee rows in window, aggregate per SKU
    actuals_qs = (SkuFeeActual.objects
                   .filter(marketplace=marketplace, date__gte=window_start, date__lte=window_end)
                   .values('sku', 'date', 'units', 'fba_fee_total', 'fee_per_unit'))
    agg_by_sku: dict[str, dict] = {}
    for r in actuals_qs:
        sku = r['sku']
        a = agg_by_sku.setdefault(sku, {
            'units_total':       0,
            'fee_total':         0.0,
            'latest_date':       None,
            'latest_fee':        0.0,
        })
        a['units_total']  += int(r['units'])
        a['fee_total']    += float(r['fba_fee_total'])
        d = r['date']
        if a['latest_date'] is None or d > a['latest_date']:
            a['latest_date'] = d
            a['latest_fee']  = float(r['fee_per_unit'])

    # 4) Build drift rows for every SKU that appears in (uploaded ∪ actuals)
    skus_with_uploaded: set[str] = {
        p['sku'] for p in product_by_sku.values()
        if p['id'] in uploaded_by_pid and p['sku']
    }
    skus_with_actuals: set[str] = set(agg_by_sku.keys())
    all_skus = skus_with_uploaded | skus_with_actuals

    rows: list[DriftRow] = []
    for sku in all_skus:
        prod = product_by_sku.get(sku) or {}
        pid  = prod.get('id')
        uploaded_fee = uploaded_by_pid.get(pid, 0.0) if pid else 0.0
        agg = agg_by_sku.get(sku) or {}
        units = agg.get('units_total', 0)

        if not include_zero_volume and units < VOLUME_FLOOR_UNITS:
            # No statistical signal — skip noisy low-volume SKUs by default
            continue

        if units > 0:
            actual_avg = agg['fee_total'] / units
        else:
            actual_avg = 0.0
        latest_fee  = agg.get('latest_fee', 0.0)
        latest_date = agg.get('latest_date')

        if uploaded_fee > 0:
            delta = actual_avg - uploaded_fee
            pct   = (delta / uploaded_fee) * 100.0
        else:
            delta = 0.0
            pct   = 0.0

        impact = abs(delta) * units
        status = _classify(pct, impact, uploaded_fee)

        rows.append(DriftRow(
            sku                = sku,
            asin               = prod.get('asin', '') or '',
            product_name       = prod.get('title', '') or '',
            brand              = prod.get('brand', '') or '',
            product_family     = prod.get('category', '') or '',
            uploaded_fee       = round(uploaded_fee, 4),
            actual_fee_avg     = round(actual_avg, 4),
            actual_fee_latest  = round(latest_fee, 4),
            actual_units       = units,
            actual_latest_date = latest_date,
            delta              = round(delta, 4),
            pct                = round(pct, 2),
            dollar_impact      = round(impact, 2),
            status             = status,
        ))

    # Sort by absolute $ impact descending — the row that costs you most up top
    rows.sort(key=lambda r: r.dollar_impact, reverse=True)
    return rows


def summarize(rows: list[DriftRow]) -> dict:
    """
    Top-of-page summary cards. Reads the rows produced by compute_drift.

    Returns:
        {
          'skus_total':        int,
          'skus_drifting':     int,   # warn + critical
          'skus_critical':     int,
          'skus_no_upload':    int,
          'monthly_at_risk':   float, # signed sum: dollar_impact × (30/WINDOW_DAYS),
                                      #   over drifting SKUs only. Positive = under-
                                      #   reporting cost (overstating margin).
        }
    """
    drifting = [r for r in rows if r.status in ('warn', 'critical')]
    critical = [r for r in rows if r.status == 'critical']
    no_upload = [r for r in rows if r.status == 'no_upload']

    # Project the 14-day signed delta-spend forward to a monthly figure.
    # delta > 0  ⇒ actual fee is higher than uploaded ⇒ we are over-reporting margin.
    # delta < 0  ⇒ actual is lower than uploaded ⇒ we're under-reporting margin.
    factor = 30.0 / max(WINDOW_DAYS, 1)
    monthly_at_risk = sum(r.delta * r.actual_units for r in drifting) * factor

    return {
        'skus_total':        len(rows),
        'skus_drifting':     len(drifting),
        'skus_critical':     len(critical),
        'skus_no_upload':    len(no_upload),
        'monthly_at_risk':   round(monthly_at_risk, 2),
        'window_days':       WINDOW_DAYS,
        'thresholds':        {
            'warn_pct':         WARN_PCT,
            'warn_impact':      WARN_IMPACT,
            'critical_pct':     CRITICAL_PCT,
            'critical_impact':  CRITICAL_IMPACT,
            'volume_floor':     VOLUME_FLOOR_UNITS,
        },
    }
