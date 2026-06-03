"""
apps/dashboard/sync.py — Pure helpers for FlatFileAllOrdersReport → DailyMetric.

Used by:
  - management commands (sync_daily_metrics, backfill_history)
  - the historical view's on-demand "refresh today" path
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as date_cls, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings

from apps.amazon_api.models import AmazonAPIConfig
from apps.amazon_api.services import SPAPIClient
from .models import COGSEntry, DailyMetric, FBAFeeRate, Product

logger = logging.getLogger(__name__)


def _local_zone(marketplace: str) -> ZoneInfo:
    tz_name = settings.AMAZON_MARKETPLACES.get(marketplace, {}).get('timezone', settings.TIME_ZONE)
    return ZoneInfo(tz_name)


def _iter_clean_rows(rows, local_zone):
    """Yield (purchase_local_date, sku, asin, qty, revenue) for valid rows.
    Skips Cancelled and Non-Amazon channels — matches Sales Snapshot."""
    for row in rows:
        if (row.get('order-status') or '').strip().lower() == 'cancelled':
            continue
        if (row.get('item-status') or '').strip().lower() == 'cancelled':
            continue
        channel = (row.get('sales-channel') or '').strip().lower()
        if channel and channel != 'amazon.com':
            continue

        pd_str = row.get('purchase-date') or ''
        try:
            pd_dt = datetime.fromisoformat(pd_str.replace('Z', '+00:00'))
            d = pd_dt.astimezone(local_zone).date()
        except Exception:
            continue

        try:
            qty = int(float(row.get('quantity') or 0))
        except ValueError:
            qty = 0
        try:
            price = float(row.get('item-price') or 0)
        except ValueError:
            price = 0.0
        try:
            promo = float(row.get('item-promotion-discount') or 0)
        except ValueError:
            promo = 0.0
        rev = max(0.0, price - promo)

        sku  = (row.get('sku')  or '').strip().upper()
        asin = (row.get('asin') or '').strip().upper()
        order_id = (row.get('amazon-order-id') or '').strip()
        yield d, sku, asin, qty, rev, order_id


COGS_FALLBACK_MONTHS = 3  # walk back this many months if current month's COGS isn't uploaded


def _load_cogs_index(marketplace: str, start_month: date_cls, end_month: date_cls):
    """
    Returns (cogs_by_month_sku, cogs_by_month_asin) keyed by 'YYYY-MM-01'.
    Loads COGS_FALLBACK_MONTHS extra months PRIOR to start_month so that
    the per-row lookup can fall back when the current month isn't uploaded yet.
    """
    # Widen lookup window to include fallback months
    fb_start = start_month
    for _ in range(COGS_FALLBACK_MONTHS):
        fb_start = (fb_start - timedelta(days=1)).replace(day=1)

    cogs_by_month_sku  = defaultdict(dict)
    cogs_by_month_asin = defaultdict(dict)
    for c in COGSEntry.objects.filter(
        product__marketplace=marketplace,
        month__gte=fb_start,
        month__lte=end_month,
    ).select_related('product'):
        mk = c.month.isoformat()
        if c.product.sku:
            cogs_by_month_sku[mk][c.product.sku.upper()] = c
        cogs_by_month_asin[mk][c.product.asin.upper()] = c
    return cogs_by_month_sku, cogs_by_month_asin


def _load_fba_rate_index(marketplace: str, day_end: date_cls):
    """
    Returns dict {product_id: [(effective_from, fee), ...]} sorted DESC by date.
    Loads only rates whose effective_from <= day_end (anything later can't apply
    to orders in the window).
    """
    rates: dict = defaultdict(list)
    qs = (FBAFeeRate.objects
          .filter(product__marketplace=marketplace, effective_from__lte=day_end)
          .order_by('product_id', '-effective_from')
          .values_list('product_id', 'effective_from', 'fba_fee_per_unit'))
    for pid, eff, fee in qs:
        rates[pid].append((eff, float(fee)))
    return rates


def _lookup_fba_rate(rates_for_product, order_date: date_cls):
    """rates_for_product is a list of (effective_from, fee) sorted DESC by date."""
    for eff, fee in rates_for_product:
        if eff <= order_date:
            return fee
    return None


def _lookup_cogs_with_fallback(cogs_by_month_sku, cogs_by_month_asin,
                                purchase_month: date_cls, sku: str, asin: str):
    """
    Try to find COGS for the order's purchase month. If not present,
    walk back month-by-month up to COGS_FALLBACK_MONTHS times.
    Example: order on 2026-05-07, May COGS not yet uploaded → use April's COGS.
    """
    cur = purchase_month
    for _ in range(COGS_FALLBACK_MONTHS + 1):
        mk = cur.isoformat()
        c = cogs_by_month_sku.get(mk, {}).get(sku) or cogs_by_month_asin.get(mk, {}).get(asin)
        if c:
            return c
        cur = (cur - timedelta(days=1)).replace(day=1)
    return None


def aggregate_rows_by_day(rows, marketplace: str, day_start: date_cls, day_end: date_cls):
    """
    Bucket cleaned report rows into per-day metrics with COGS-based GM.
    Returns dict: { date(YYYY-MM-DD): {revenue, units, orders, cgs, amz_fee, fulfill, gm, cm} }
    Days with zero orders are NOT included; caller fills gaps as needed.

    Per-row cost lookup priority:
      cgs_unit  = COGSEntry.unit_cost (+duties+prep+other)  with monthly fallback
      fba_unit  = FBAFeeRate (most-recent effective_from <= order_date)
                  ↳ falls back to COGSEntry.shipping_cost if no rate exists
      amz_fee   = revenue × 15%
    """
    local_zone = _local_zone(marketplace)
    cogs_by_month_sku, cogs_by_month_asin = _load_cogs_index(
        marketplace, day_start.replace(day=1), day_end.replace(day=1)
    )
    fba_rates = _load_fba_rate_index(marketplace, day_end)

    # Pre-load Product to map sku/asin → product_id (needed for FBA rate lookup)
    prod_id_by_sku, prod_id_by_asin = {}, {}
    for pid, sku_db, asin_db in (
        Product.objects.filter(marketplace=marketplace)
        .values_list('id', 'sku', 'asin')
    ):
        if sku_db:
            prod_id_by_sku[sku_db.upper()] = pid
        if asin_db:
            prod_id_by_asin[asin_db.upper()] = pid

    buckets = defaultdict(lambda: {
        'revenue': 0.0, 'units': 0, 'orders': set(),
        'cgs': 0.0, 'amz_fee': 0.0, 'fulfill': 0.0,
    })
    for d, sku, asin, qty, rev, oid in _iter_clean_rows(rows, local_zone):
        if d < day_start or d > day_end:
            continue
        purchase_month = d.replace(day=1)
        cogs = _lookup_cogs_with_fallback(
            cogs_by_month_sku, cogs_by_month_asin, purchase_month, sku, asin
        )
        if cogs:
            cgs_unit = (float(cogs.unit_cost or 0) + float(cogs.duties_cost or 0)
                        + float(cogs.prep_cost or 0) + float(cogs.other_cost or 0))
        else:
            cgs_unit = 0.0

        # FBA fee — prefer FBAFeeRate keyed by effective_from
        pid = prod_id_by_sku.get(sku) or prod_id_by_asin.get(asin)
        fba_unit = None
        if pid is not None:
            fba_unit = _lookup_fba_rate(fba_rates.get(pid, []), d)
        if fba_unit is None:                               # fall back to COGS shipping_cost
            fba_unit = float(cogs.shipping_cost or 0) if cogs else 0.0

        b = buckets[d]
        b['revenue'] += rev
        b['units']   += qty
        b['cgs']     += cgs_unit * qty
        b['amz_fee'] += rev * 0.15
        b['fulfill'] += fba_unit * qty
        if oid:
            b['orders'].add(oid)

    # Materialise gm/cm
    for b in buckets.values():
        gm = b['revenue'] - b['cgs'] - b['amz_fee'] - b['fulfill']
        b['gm']     = gm
        b['cm']     = gm     # No PPC yet — CM == GM until Ads API connects
        b['orders'] = len(b['orders'])
    return dict(buckets)


def upsert_daily_metrics(marketplace: str, by_day: dict, full_window: tuple[date_cls, date_cls] = None):
    """
    Persist aggregated per-day metrics into DailyMetric.
    If full_window=(start, end) is given, ALSO writes zero rows for any day in
    that window that didn't appear in by_day (so the chart line stays continuous).
    """
    written = 0
    target_dates = set(by_day.keys())
    if full_window:
        s, e = full_window
        d = s
        while d <= e:
            target_dates.add(d)
            d += timedelta(days=1)

    for d in sorted(target_dates):
        b = by_day.get(d) or {
            'revenue': 0.0, 'units': 0, 'orders': 0,
            'cgs': 0.0, 'amz_fee': 0.0, 'fulfill': 0.0, 'gm': 0.0, 'cm': 0.0,
        }
        rev = b['revenue']
        gm  = b['gm']   # = CM before PPC known
        cm  = b['cm']
        obj, _ = DailyMetric.objects.update_or_create(
            marketplace=marketplace, date=d,
            defaults={
                'revenue':             Decimal(f'{rev:.2f}'),
                'units':               int(b['units']),
                'orders':              int(b['orders']),
                'cgs':                 Decimal(f'{b["cgs"]:.2f}'),
                'amazon_fee':          Decimal(f'{b["amz_fee"]:.2f}'),
                'fba_fee':             Decimal(f'{b["fulfill"]:.2f}'),
                'contribution_margin': Decimal(f'{cm:.2f}'),
                'cm_pct':              Decimal(f'{(cm / rev) if rev else 0:.4f}'),
                # gross_margin and PPC fields updated after, preserving existing PPC data
            },
        )
        # Re-compute GM = CM − PPC and TACoS using the freshly updated revenue.
        # This preserves any PPC data already written by backfill_ppc.
        ppc_val  = float(obj.ppc_spend or 0)
        gm_final = cm - ppc_val
        rev_f    = rev or float(obj.revenue or 0)
        DailyMetric.objects.filter(pk=obj.pk).update(
            gross_margin = Decimal(f'{gm_final:.2f}'),
            gm_pct       = Decimal(f'{(gm_final / rev_f) if rev_f else 0:.4f}'),
            tacos        = Decimal(f'{(ppc_val / rev_f) if (ppc_val and rev_f) else 0:.4f}'),
        )
        written += 1
    return written


def aggregate_rows_by_sku(rows, marketplace: str, day_start: date_cls, day_end: date_cls):
    """
    Bucket order-report rows into per-SKU metrics with COGS for [day_start, day_end].
    Returns dict: { sku_upper: {asin, qty, revenue, cgs, amz_fee, fulfill, cm} }
    """
    local_zone = _local_zone(marketplace)
    cogs_by_month_sku, cogs_by_month_asin = _load_cogs_index(
        marketplace, day_start.replace(day=1), day_end.replace(day=1)
    )
    fba_rates = _load_fba_rate_index(marketplace, day_end)

    prod_id_by_sku, prod_id_by_asin = {}, {}
    for pid, sku_db, asin_db in (
        Product.objects.filter(marketplace=marketplace)
        .values_list('id', 'sku', 'asin')
    ):
        if sku_db: prod_id_by_sku[sku_db.upper()]  = pid
        if asin_db: prod_id_by_asin[asin_db.upper()] = pid

    buckets: dict = {}
    for d, sku, asin, qty, rev, oid in _iter_clean_rows(rows, local_zone):
        if d < day_start or d > day_end:
            continue
        purchase_month = d.replace(day=1)
        cogs = _lookup_cogs_with_fallback(
            cogs_by_month_sku, cogs_by_month_asin, purchase_month, sku, asin
        )
        cgs_unit = (float(cogs.unit_cost or 0) + float(cogs.duties_cost or 0)
                    + float(cogs.prep_cost or 0) + float(cogs.other_cost or 0)) if cogs else 0.0
        pid = prod_id_by_sku.get(sku) or prod_id_by_asin.get(asin)
        fba_unit = _lookup_fba_rate(fba_rates.get(pid, []), d) if pid else None
        if fba_unit is None:
            fba_unit = float(cogs.shipping_cost or 0) if cogs else 0.0

        key = sku or asin
        if not key:
            continue
        if key not in buckets:
            buckets[key] = {'asin': asin, 'qty': 0, 'revenue': 0.0,
                            'cgs': 0.0, 'amz_fee': 0.0, 'fulfill': 0.0}
        b = buckets[key]
        b['asin']    = b['asin'] or asin
        b['qty']     += qty
        b['revenue'] += rev
        b['cgs']     += cgs_unit * qty
        b['amz_fee'] += rev * 0.15
        b['fulfill'] += fba_unit * qty

    for b in buckets.values():
        b['cm'] = b['revenue'] - b['cgs'] - b['amz_fee'] - b['fulfill']
    return buckets


def upsert_sku_snapshots(marketplace: str, by_sku: dict, date: date_cls) -> int:
    """
    Persist per-SKU metrics into DailySkuSnapshot for a single date.
    Returns number of rows written.
    """
    from .models import DailySkuSnapshot
    objs = []
    for sku, b in by_sku.items():
        objs.append(DailySkuSnapshot(
            marketplace = marketplace,
            date        = date,
            sku         = sku,
            asin        = b.get('asin', ''),
            qty         = int(b['qty']),
            revenue     = Decimal(f'{b["revenue"]:.2f}'),
            cgs         = Decimal(f'{b["cgs"]:.2f}'),
            amz_fee     = Decimal(f'{b["amz_fee"]:.2f}'),
            fulfill     = Decimal(f'{b["fulfill"]:.2f}'),
            cm          = Decimal(f'{b["cm"]:.2f}'),
        ))
    if objs:
        DailySkuSnapshot.objects.bulk_create(
            objs,
            update_conflicts=True,
            update_fields=['asin', 'qty', 'revenue', 'cgs', 'amz_fee', 'fulfill', 'cm', 'synced_at'],
            unique_fields=['marketplace', 'date', 'sku'],
        )
    return len(objs)


def sync_window(marketplace: str, start: date_cls, end: date_cls,
                max_wait_seconds: int = 90, progress_cb=None) -> dict:
    """
    Fetch ONE FlatFileAllOrdersReport for [start, end] inclusive,
    aggregate per day, persist into DailyMetric.
    Returns {status, rows, days_written, report_id?}.
    """
    cfg = AmazonAPIConfig.objects.filter(marketplace=marketplace, is_active=True).first()
    if not cfg or not cfg.has_sp_api_credentials():
        return {'status': 'NO_CONFIG', 'rows': 0, 'days_written': 0}

    client = SPAPIClient(cfg)
    result = client.fetch_orders_report_sync(
        'custom',
        start_date=str(start),
        end_date=str(end),
        max_wait_seconds=max_wait_seconds,
        progress_cb=progress_cb,
    )
    rows = result.get('rows')
    if not rows:
        return {
            'status':       result.get('status', 'PENDING'),
            'rows':         0,
            'days_written': 0,
            'report_id':    result.get('report_id'),
        }

    by_day = aggregate_rows_by_day(rows, marketplace, start, end)
    written = upsert_daily_metrics(marketplace, by_day, full_window=(start, end))

    # Also cache per-SKU breakdown for every day in the window so the
    # "Today" dashboard can show Product Performance from cache.
    for day in by_day:
        by_sku = aggregate_rows_by_sku(rows, marketplace, day, day)
        upsert_sku_snapshots(marketplace, by_sku, day)

    return {
        'status':       result.get('status', 'OK'),
        'rows':         len(rows),
        'days_written': written,
        'days_with_orders': len(by_day),
    }


def apply_ppc_from_snapshots(marketplace: str, start: date_cls, end: date_cls) -> int:
    """
    Read PPCCampaignSnapshot rows already in the DB and patch DailyMetric.ppc_spend.

    Called after sync_window() so the PPC column is populated immediately from
    whatever snapshots were saved (either by the live dashboard during the day, or
    by a previous backfill_ppc run).  This fills the midnight-to-6am gap where the
    cron hasn't run yet.

    Only updates a DailyMetric row when the snapshot total is HIGHER than what is
    already stored (avoids accidentally overwriting a more-accurate cron value).

    Returns the number of DailyMetric rows updated.
    """
    from django.db.models import Sum
    from apps.dashboard.models import PPCCampaignSnapshot

    rows = (
        PPCCampaignSnapshot.objects
        .filter(marketplace=marketplace, date__gte=start, date__lte=end)
        .values('date')
        .annotate(total_spend=Sum('spend'))
    )
    ppc_by_day = {r['date']: float(r['total_spend'] or 0) for r in rows}

    if not ppc_by_day:
        return 0

    updated = 0
    for d, ppc in ppc_by_day.items():
        if ppc <= 0:
            continue
        dm = DailyMetric.objects.filter(marketplace=marketplace, date=d).first()
        if dm is None:
            continue
        existing_ppc = float(dm.ppc_spend or 0)
        if ppc > existing_ppc:
            rev = float(dm.revenue or 0)
            cm  = float(dm.contribution_margin or 0)
            gm  = cm - ppc
            DailyMetric.objects.filter(pk=dm.pk).update(
                ppc_spend    = Decimal(f'{ppc:.2f}'),
                gross_margin = Decimal(f'{gm:.2f}'),
                gm_pct       = Decimal(f'{(gm / rev) if rev else 0:.4f}'),
                tacos        = Decimal(f'{(ppc / rev) if (ppc and rev) else 0:.4f}'),
            )
            updated += 1
    return updated


def days_missing_ppc(marketplace: str, start: date_cls, end: date_cls) -> list:
    """
    Return a list of dates in [start, end] whose DailyMetric row exists but has
    ppc_spend == 0.  Used to decide whether to spawn a background backfill_ppc.
    """
    missing = []
    d = start
    while d <= end:
        dm = DailyMetric.objects.filter(marketplace=marketplace, date=d).first()
        if dm is not None and float(dm.ppc_spend or 0) == 0:
            missing.append(d)
        d += timedelta(days=1)
    return missing


def configured_marketplaces() -> list[str]:
    """Marketplaces that currently have active SP-API credentials."""
    return list(
        AmazonAPIConfig.objects
        .filter(is_active=True)
        .values_list('marketplace', flat=True)
    )
