"""
apps/dashboard/cogs_recalc.py — recalculate stored COGS after a COGS upload.

COGS is frozen into several storage layers at write time. When the user
uploads (or corrects) COGS for a month, this module refreshes every layer for
that (marketplace, month) without re-fetching anything from Amazon:

  A. SettlementLineActual line_key='cogs'      → Management P&L
     (from UnifiedSkuUnits persisted at unified-report import)
  B. DailySkuSnapshot.cgs / .cm                → SKU Profitability, drills
  C. DailyMetric.cgs + derived margins         → Daily Dashboard, Historical,
                                                  Daily P&L, Morning Report
  D. HourlySkuSnapshot / HourlyMetricSnapshot  → Hourly Patterns (≤30d window)
  E. CampaignProfitDaily                       → Campaign P&L
     (re-runs the existing compute_campaign_profit command for the month)

Formulas mirror the write path in sync.py exactly:
  sku cm             = revenue − cgs − amz_fee − fulfill
  DailyMetric:
    contribution_margin = Σ sku cm            (pre-PPC)
    cm_pct              = cm / revenue
    gross_margin        = cm − ppc_spend
    gm_pct              = gross_margin / revenue

finalized_at is intentionally IGNORED — this is a cost-basis correction on
already-locked order data, not a rewrite of order data.
"""
from __future__ import annotations

from datetime import date as date_cls, timedelta
from decimal import Decimal


def month_cogs_unit_map(marketplace: str, month: date_cls) -> dict[str, float]:
    """
    {SKU_UPPER: cogs_per_unit} effective for `month`, using the same
    month-with-fallback convention as sync.py (_lookup_cogs_with_fallback):
    the COGSEntry with the latest month <= target month wins.
    """
    from .models import COGSEntry
    month_start = month.replace(day=1)
    out: dict[str, tuple[date_cls, float]] = {}
    for c in (COGSEntry.objects
              .filter(product__marketplace=marketplace,
                      month__lte=month_start)
              .select_related('product')):
        sku = (c.product.sku or '').upper() if c.product else ''
        if not sku:
            continue
        cu = (float(c.unit_cost or 0) + float(c.duties_cost or 0)
              + float(c.prep_cost or 0) + float(c.other_cost or 0))
        m = c.month or date_cls(2000, 1, 1)
        if sku not in out or m > out[sku][0]:
            out[sku] = (m, cu)
    return {k: v[1] for k, v in out.items()}


def _month_bounds(month: date_cls) -> tuple[date_cls, date_cls]:
    start = month.replace(day=1)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, nxt - timedelta(days=1)


def recalc_cogs(marketplace: str, month: date_cls,
                run_campaign_profit: bool = True) -> dict:
    """Refresh COGS in every storage layer for one (marketplace, month)."""
    from django.db import transaction
    from .models import (
        SettlementLineActual, UnifiedSkuUnits,
        DailySkuSnapshot, DailyMetric,
        HourlySkuSnapshot, HourlyMetricSnapshot,
    )

    month_start, month_end = _month_bounds(month)
    cost = month_cogs_unit_map(marketplace, month_start)
    summary: dict = {'marketplace': marketplace,
                     'month': month_start.isoformat(),
                     'cogs_skus_mapped': len(cost)}

    def unit_cost(sku: str) -> float:
        return cost.get((sku or '').upper(), 0.0)

    with transaction.atomic():
        # ── A. Management P&L (SettlementLineActual.cogs) ────────────────
        units = list(UnifiedSkuUnits.objects.filter(
            marketplace=marketplace, month=month_start))
        if units:
            gross = sum(u.order_units * unit_cost(u.sku) for u in units)
            ret   = sum(u.refund_units * unit_cost(u.sku) for u in units)
            new_cogs = round(gross - ret, 2)
            row = SettlementLineActual.objects.filter(
                marketplace=marketplace, month=month_start,
                line_key='cogs').first()
            old = float(row.amount) if row else None
            if row:
                row.amount = new_cogs
                row.save(update_fields=['amount', 'updated_at'])
            else:
                SettlementLineActual.objects.create(
                    marketplace=marketplace, month=month_start,
                    line_key='cogs', amount=new_cogs,
                    source_note='unified')
            summary['pnl_cogs'] = {'old': old, 'new': new_cogs,
                                    'skus': len(units)}
        else:
            summary['pnl_cogs'] = ('skipped — no unified upload stored for '
                                    'this month (upload the Unified Transaction '
                                    'report once to enable P&L recalc)')

        # ── B. DailySkuSnapshot ──────────────────────────────────────────
        sku_rows = list(DailySkuSnapshot.objects.filter(
            marketplace=marketplace,
            date__gte=month_start, date__lte=month_end))
        old_sku_cgs = sum(float(r.cgs or 0) for r in sku_rows)
        for r in sku_rows:
            cgs = round(r.qty * unit_cost(r.sku), 2)
            cm = (float(r.revenue or 0) - cgs
                  - float(r.amz_fee or 0) - float(r.fulfill or 0))
            r.cgs = Decimal(f'{cgs:.2f}')
            r.cm  = Decimal(f'{cm:.2f}')
        DailySkuSnapshot.objects.bulk_update(sku_rows, ['cgs', 'cm'],
                                              batch_size=2000)
        new_sku_cgs = sum(float(r.cgs or 0) for r in sku_rows)
        summary['daily_sku'] = {'rows': len(sku_rows),
                                 'old_cgs': round(old_sku_cgs, 2),
                                 'new_cgs': round(new_sku_cgs, 2)}

        # ── C. DailyMetric (roll up B, re-derive margins per sync.py) ────
        by_day: dict = {}
        for r in sku_rows:
            by_day[r.date] = by_day.get(r.date, 0.0) + float(r.cgs or 0)
        dm_rows = list(DailyMetric.objects.filter(
            marketplace=marketplace,
            date__gte=month_start, date__lte=month_end))
        dm_updated = 0
        for dm in dm_rows:
            if dm.date not in by_day:
                continue   # no per-SKU rows that day — leave untouched
            rev = float(dm.revenue or 0)
            ppc = float(dm.ppc_spend or 0)
            cgs = by_day[dm.date]
            cm  = (rev - cgs - float(dm.amazon_fee or 0)
                   - float(dm.fba_fee or 0))
            gm  = cm - ppc
            dm.cgs                 = Decimal(f'{cgs:.2f}')
            dm.contribution_margin = Decimal(f'{cm:.2f}')
            dm.cm_pct              = Decimal(f'{(cm / rev) if rev else 0:.4f}')
            dm.gross_margin        = Decimal(f'{gm:.2f}')
            dm.gm_pct              = Decimal(f'{(gm / rev) if rev else 0:.4f}')
            dm_updated += 1
        DailyMetric.objects.bulk_update(
            dm_rows, ['cgs', 'contribution_margin', 'cm_pct',
                       'gross_margin', 'gm_pct'], batch_size=500)
        summary['daily_metric'] = {'rows_updated': dm_updated,
                                    'days_without_sku_rows':
                                        len(dm_rows) - dm_updated}

        # ── D. Hourly snapshots (only recent months survive pruning) ────
        h_sku = list(HourlySkuSnapshot.objects.filter(
            marketplace=marketplace,
            date__gte=month_start, date__lte=month_end))
        for r in h_sku:
            cgs = round(r.qty * unit_cost(r.sku), 2)
            cm = (float(r.revenue or 0) - cgs
                  - float(r.amazon_fee or 0) - float(r.fba_fee or 0))
            r.cgs                 = Decimal(f'{cgs:.2f}')
            r.contribution_margin = Decimal(f'{cm:.2f}')
        HourlySkuSnapshot.objects.bulk_update(
            h_sku, ['cgs', 'contribution_margin'], batch_size=2000)

        by_dh: dict = {}
        for r in h_sku:
            by_dh[(r.date, r.hour)] = (by_dh.get((r.date, r.hour), 0.0)
                                        + float(r.cgs or 0))
        h_met = list(HourlyMetricSnapshot.objects.filter(
            marketplace=marketplace,
            date__gte=month_start, date__lte=month_end))
        hm_updated = 0
        for hm in h_met:
            key = (hm.date, hm.hour)
            if key not in by_dh:
                continue
            rev = float(hm.revenue or 0)
            ppc = float(hm.ppc_spend or 0)
            cgs = by_dh[key]
            cm  = (rev - cgs - float(hm.amazon_fee or 0)
                   - float(hm.fba_fee or 0))
            gm  = cm - ppc
            hm.cgs                 = Decimal(f'{cgs:.2f}')
            hm.contribution_margin = Decimal(f'{cm:.2f}')
            hm.cm_pct              = Decimal(f'{(cm / rev) if rev else 0:.4f}')
            hm.gross_margin        = Decimal(f'{gm:.2f}')
            hm.gm_pct              = Decimal(f'{(gm / rev) if rev else 0:.4f}')
            hm_updated += 1
        HourlyMetricSnapshot.objects.bulk_update(
            h_met, ['cgs', 'contribution_margin', 'cm_pct',
                     'gross_margin', 'gm_pct'], batch_size=2000)
        summary['hourly'] = {'sku_rows': len(h_sku),
                              'metric_rows_updated': hm_updated}

    # ── E. Campaign P&L (outside the atomic block — its own transactions) ─
    if run_campaign_profit:
        try:
            from django.core.management import call_command
            rewind = (month_end - month_start).days
            call_command('compute_campaign_profit',
                         marketplace=marketplace,
                         date=month_end.isoformat(),
                         rewind=rewind, skip_search_terms=True)
            summary['campaign_profit'] = f'recomputed {month_start} → {month_end}'
        except Exception as exc:
            summary['campaign_profit'] = f'FAILED: {exc}'
    else:
        summary['campaign_profit'] = 'skipped (run_campaign_profit=False)'

    return summary
