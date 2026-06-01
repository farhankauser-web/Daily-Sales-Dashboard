"""
apps/dashboard/views.py — All dashboard views
"""
import csv
import io
import json
import logging
from datetime import date, timedelta
from decimal import Decimal
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import StreamingHttpResponse, JsonResponse, HttpResponse
from django.conf import settings
from django.db.models import Sum, Avg

from apps.core.decorators import permission_required
from apps.users.models import AuditLog
from apps.amazon_api.models import AmazonAPIConfig, AnthropicConfig
from .models import Product, COGSEntry, MonthlyTarget, DailyMetric, ProductTypePackMonthlyTarget, FBAFeeRate
from .forms import COGSBulkUploadForm, COGSEntryForm, MonthlyTargetForm, ProductForm, FBARateBulkUploadForm

logger = logging.getLogger(__name__)


@login_required
@permission_required('can_view_dashboard')
def index(request):
    configs = AmazonAPIConfig.objects.filter(is_active=True).values(
        'marketplace', 'label', 'last_test_status', 'last_tested_at'
    )
    allowed = request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys())
    ctx = {
        'configs': {c['marketplace']: c for c in configs},
        'allowed_marketplaces': allowed,
        'show_financials': request.user.has_perm_flag('can_view_financials'),
        'show_ppc':        request.user.has_perm_flag('can_view_ppc'),
        'show_inventory':  request.user.has_perm_flag('can_view_inventory'),
        'can_ai_summary':  request.user.has_perm_flag('can_generate_ai_summary'),
        'today':           date.today(),
    }
    return render(request, 'dashboard/index.html', ctx)


@login_required
@permission_required('can_view_historical')
def historical(request):
    from .sync import sync_window, apply_ppc_from_snapshots, days_missing_ppc

    marketplace   = request.GET.get('mp', 'usa')
    period        = request.GET.get('period', '30d')
    backfill_days = request.GET.get('backfill_days')

    if not request.user.can_access_marketplace(marketplace):
        marketplace = (request.user.allowed_marketplaces or ['usa'])[0]

    today     = date.today()
    yesterday = today - timedelta(days=1)   # historical view ends at yesterday
    end       = yesterday                   # never show today's incomplete row

    days_map = {
        '7d':  7,
        '30d': 30,
        '90d': 90,
        'ytd': (end - end.replace(month=1, day=1)).days + 1,
    }
    days  = days_map.get(period, 30)
    start = end - timedelta(days=days - 1) if period != 'ytd' else end.replace(month=1, day=1)

    cfg = AmazonAPIConfig.objects.filter(marketplace=marketplace, is_active=True).first()
    has_config = bool(cfg and cfg.has_sp_api_credentials())

    sync_status = None

    # UI-triggered backfill. Synchronous, capped at 90s. If Amazon's report
    # isn't ready by then, the in-flight reportId persists in memory — clicking
    # the button again will re-poll the SAME report and download as soon as it's
    # built.
    if backfill_days and has_config:
        try:
            n = max(1, min(int(backfill_days), 90))
            bf_end   = end
            bf_start = bf_end - timedelta(days=n - 1)

            # Step 1: sync order data (revenue / units / COGS)
            res = sync_window(marketplace, bf_start, bf_end, max_wait_seconds=90)

            # Step 2: apply PPC from any snapshots already in DB (instant)
            ppc_updated = apply_ppc_from_snapshots(marketplace, bf_start, bf_end)

            # Step 3: if some days still have no PPC, launch backfill_ppc in the
            # background so Amazon's campaign reports are fetched asynchronously.
            missing = days_missing_ppc(marketplace, bf_start, bf_end)
            ppc_bg_launched = False
            if missing:
                try:
                    import subprocess, sys, os
                    subprocess.Popen(
                        [
                            sys.executable,
                            'manage.py', 'backfill_ppc',
                            '--marketplace', marketplace,
                            '--start', str(bf_start),
                            '--end',   str(bf_end),
                        ],
                        cwd=settings.BASE_DIR,
                        env=os.environ.copy(),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    ppc_bg_launched = True
                except Exception as bg_err:
                    logger.warning('Failed to launch background backfill_ppc: %s', bg_err)

            sync_status = (
                f"Backfill {bf_start:%Y-%m-%d} → {bf_end:%Y-%m-%d}  "
                f"status={res['status']}  rows={res['rows']}  "
                f"days_written={res.get('days_written', 0)}  "
                f"ppc_days_from_cache={ppc_updated}"
                + (f"  days_with_orders={res['days_with_orders']}" if 'days_with_orders' in res else '')
            )
            if ppc_bg_launched:
                sync_status += (
                    f"  · PPC backfill started in background for {len(missing)} day(s) "
                    f"— refresh this page in 1-3 minutes to see PPC data."
                )
            if res['status'] not in ('OK', 'CACHED', 'FRESH'):
                sync_status += (
                    f"  · Amazon hasn't finished building the orders report yet "
                    f"(reportId={res.get('report_id')}). Click the backfill button again in "
                    f"1-2 minutes — the same reportId will be polled and downloaded when ready."
                )
        except Exception as e:
            logger.error('UI backfill failed: %s', e, exc_info=True)
            sync_status = f'backfill error: {e}'

    # Auto-sync yesterday if it has no row yet (fills the gap at start of each new day).
    # Also apply PPC from existing snapshots so yesterday's PPC shows immediately
    # even before the 6am cron runs.
    if has_config and not backfill_days:
        yest_exists = DailyMetric.objects.filter(
            marketplace=marketplace, date=yesterday
        ).exclude(revenue=0).exists()
        if not yest_exists:
            try:
                sync_window(marketplace, yesterday, yesterday, max_wait_seconds=30)
            except Exception:
                pass  # silent — user can trigger manual backfill if needed
        # Always apply PPC snapshots for yesterday (handles midnight→6am gap)
        try:
            apply_ppc_from_snapshots(marketplace, yesterday, yesterday)
        except Exception:
            pass

    # Read from DailyMetric — ends at yesterday (today is always incomplete)
    import calendar
    rows = list(DailyMetric.objects.filter(
        marketplace=marketplace, date__gte=start, date__lte=end,
    ).order_by('date'))
    by_date = {r.date: r for r in rows}

    # Pre-load monthly targets for any month in the range so we can compute
    # the per-day TAR GM/Day = monthly_revenue_target / days_in_month
    # Source priority:
    #   1. MonthlyTarget.revenue_target  (marketplace-level, if user set it)
    #   2. SUM(ProductTypePackMonthlyTarget.revenue_target)  (per-product targets)
    month_starts = set()
    _m = start.replace(day=1)
    while _m <= today:
        month_starts.add(_m)
        _m = (_m + timedelta(days=32)).replace(day=1)

    # Layer 1 — marketplace-level targets
    monthly_target_revenue = {}     # date(month) → revenue target (float)
    for t in MonthlyTarget.objects.filter(marketplace=marketplace, month__in=month_starts):
        if t.revenue_target:
            monthly_target_revenue[t.month] = float(t.revenue_target)

    # Layer 2 — fall back to summing per-product targets for any month not in layer 1
    missing_months = [m for m in month_starts if m not in monthly_target_revenue]
    if missing_months:
        per_product_sums = (
            ProductTypePackMonthlyTarget.objects
            .filter(marketplace=marketplace, month__in=missing_months)
            .values('month')
            .annotate(total=Sum('revenue_target'))
        )
        for row in per_product_sums:
            if row['total']:
                monthly_target_revenue[row['month']] = float(row['total'])

    chart_dates, chart_rev, chart_units, chart_ppc, chart_tacos, chart_gm_pct = [], [], [], [], [], []
    table_rows = []
    tot_rev = tot_units = tot_orders = 0
    tot_cgs = tot_amz = tot_fba = tot_cm = tot_gm = tot_ppc = tot_tar = 0.0

    cursor = start
    while cursor <= end:
        m = by_date.get(cursor)
        rev    = float(m.revenue)             if m else 0.0
        units  = int(m.units)                 if m else 0
        orders = int(m.orders)                if m else 0
        cgs    = float(m.cgs or 0)            if m else 0.0
        amz    = float(m.amazon_fee or 0)     if m else 0.0
        fba    = float(m.fba_fee or 0)        if m else 0.0
        cm_amt = float(m.contribution_margin) if m else 0.0
        cm_pct = float(m.cm_pct) * 100        if m else 0.0
        ppc    = float(m.ppc_spend)           if m else 0.0  # 0 until Ads API
        # GM = CM − PPC (compute on-the-fly so it's always correct)
        gm_amt = cm_amt - ppc
        gm_pct = (gm_amt / rev * 100) if rev else 0.0
        tacos  = float(m.tacos) * 100         if m else 0.0

        # CPA: spend per order (0 when PPC not yet connected)
        cpa = (ppc / orders) if orders else 0.0

        # TAR GM/Day = monthly revenue target ÷ days_in_month
        # (uses MonthlyTarget if set, else summed ProductTypePackMonthlyTarget)
        target_rev = monthly_target_revenue.get(cursor.replace(day=1), 0.0)
        if target_rev:
            days_in_mo = calendar.monthrange(cursor.year, cursor.month)[1]
            tar_day    = target_rev / days_in_mo
        else:
            tar_day = 0.0
        gm_minus_tar = gm_amt - tar_day if tar_day else 0.0

        chart_dates.append(cursor.isoformat())
        chart_rev.append(round(rev, 2))
        chart_units.append(units)
        chart_ppc.append(round(ppc, 2))
        chart_tacos.append(round(tacos, 2))
        chart_gm_pct.append(round(gm_pct, 2))

        table_rows.append({
            'date':         cursor,
            'revenue':      rev,
            'units':        units,
            'orders':       orders,
            'cgs':          cgs,
            'amazon_fee':   amz,
            'fba_fee':      fba,
            'cm':           cm_amt,
            'cm_pct':       cm_pct,
            'ppc_spend':    ppc,
            'gm':           gm_amt,
            'gm_pct':       gm_pct,
            'cpa':          cpa,
            'tacos':        tacos,
            'tar_gm_day':   tar_day,
            'gm_minus_tar': gm_minus_tar,
        })

        tot_rev    += rev
        tot_units  += units
        tot_orders += orders
        tot_cgs    += cgs
        tot_amz    += amz
        tot_fba    += fba
        tot_cm     += cm_amt
        tot_gm     += gm_amt
        tot_ppc    += ppc
        tot_tar    += tar_day
        cursor     += timedelta(days=1)

    totals = {
        'total_revenue': round(tot_rev, 2),
        'total_units':   int(tot_units),
        'total_orders':  int(tot_orders),
        'total_cgs':     round(tot_cgs, 2),
        'total_amz':     round(tot_amz, 2),
        'total_fba':     round(tot_fba, 2),
        'total_cm':      round(tot_cm, 2),
        'total_gm':      round(tot_gm, 2),
        'total_ppc':     round(tot_ppc, 2),
        'total_tar':     round(tot_tar, 2),
        'avg_cm_pct':    round((tot_cm / tot_rev * 100) if tot_rev else 0, 2),
        'avg_gm_pct':    round((tot_gm / tot_rev * 100) if tot_rev else 0, 2),
        'avg_tacos':     round((tot_ppc / tot_rev * 100) if tot_rev else 0, 2),
        'avg_acos':      0,
        'cpa':           round((tot_ppc / tot_orders) if tot_orders else 0, 2),
        'gm_minus_tar':  round(tot_gm - tot_tar, 2) if tot_tar else 0,
    }
    has_data = bool(rows)
    chart_data = json.dumps({
        'dates': chart_dates, 'revenue': chart_rev, 'units': chart_units,
        'ppc':   chart_ppc,   'tacos':   chart_tacos, 'gm_pct': chart_gm_pct,
    })

    last_sync = (
        DailyMetric.objects
        .filter(marketplace=marketplace, date=yesterday)
        .values_list('synced_at', flat=True)
        .first()
    )

    target = MonthlyTarget.objects.filter(
        marketplace=marketplace, month=end.replace(day=1)
    ).first()

    ctx = {
        'metrics':     table_rows,
        'totals':      totals,
        'chart_data':  chart_data,
        'has_data':    has_data,
        'has_config':  has_config,
        'sync_status': sync_status,
        'last_sync_yest': last_sync,
        'marketplace': marketplace,
        'period':      period,
        'start':       start,
        'end':         end,        # yesterday
        'today':       today,
        'target':      target,
        'allowed_marketplaces': request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys()),
        'show_financials': request.user.has_perm_flag('can_view_financials'),
        'show_ppc':        request.user.has_perm_flag('can_view_ppc'),
    }
    return render(request, 'dashboard/historical.html', ctx)


# ── AJAX: Product-line cumulative analysis ────────────────────────────────────
@login_required
def product_line_analysis(request):
    """
    Returns per-product-group P&L for a historical date range.
    Uses:
      • FlatFileAllOrdersReport  → revenue / qty / cogs / fees per SKU
      • PPCProductSnapshot (DB)  → PPC spend per ASIN
      • ProductTypePackMonthlyTarget → TAR GM/Day
    """
    from collections import defaultdict
    from apps.amazon_api.services import SPAPIClient, AdsAPIClient
    from apps.amazon_api.models import AmazonAPIConfig
    from apps.dashboard.models import (
        Product, COGSEntry, PPCProductSnapshot, ProductTypePackMonthlyTarget
    )
    import calendar as _cal
    import re

    marketplace = request.GET.get('mp', 'usa')
    start_str   = request.GET.get('start')
    end_str     = request.GET.get('end')

    if not request.user.can_access_marketplace(marketplace):
        return JsonResponse({'error': 'Access denied'}, status=403)

    try:
        s_d = date.fromisoformat(start_str)
        e_d = date.fromisoformat(end_str)
    except Exception:
        yesterday = date.today() - timedelta(days=1)
        s_d = yesterday - timedelta(days=29)
        e_d = yesterday

    # Never let end bleed into today
    yesterday = date.today() - timedelta(days=1)
    e_d = min(e_d, yesterday)

    cfg = AmazonAPIConfig.objects.filter(marketplace=marketplace, is_active=True).first()
    if not cfg or not cfg.has_sp_api_credentials():
        return JsonResponse({'error': 'No SP-API config'}, status=400)

    period_days   = (e_d - s_d).days + 1
    days_in_month = _cal.monthrange(e_d.year, e_d.month)[1]

    # ── 1. Fetch order report ────────────────────────────────────────────────
    client = SPAPIClient(cfg)
    tz_name = settings.AMAZON_MARKETPLACES.get(marketplace, {}).get('timezone', settings.TIME_ZONE)
    local_zone = ZoneInfo(tz_name)
    report_result = client.fetch_orders_report_sync(
        'custom', start_date=str(s_d), end_date=str(e_d), max_wait_seconds=90,
    )
    rows = report_result.get('rows') or []

    # ── 2. Build catalog lookups ─────────────────────────────────────────────
    prods_by_sku  = {}
    prods_by_asin = {}
    for p in Product.objects.filter(marketplace=marketplace):
        if p.sku:  prods_by_sku[p.sku.upper()]  = p
        if p.asin: prods_by_asin[p.asin.upper()] = p

    month_start = e_d.replace(day=1)
    cogs_by_sku  = {}
    cogs_by_asin = {}
    for c in COGSEntry.objects.filter(
        product__marketplace=marketplace, month=month_start,
    ).select_related('product'):
        if c.product.sku:  cogs_by_sku[c.product.sku.upper()]  = c
        if c.product.asin: cogs_by_asin[c.product.asin.upper()] = c

    # ── 3. Aggregate order rows per SKU ─────────────────────────────────────
    agg = {}
    for row in rows:
        if (row.get('order-status') or '').strip().lower() == 'cancelled': continue
        if (row.get('item-status')  or '').strip().lower() == 'cancelled': continue
        ch = (row.get('sales-channel') or '').strip().lower()
        if ch and ch != 'amazon.com': continue

        sku  = (row.get('sku')  or '').strip()
        asin = (row.get('asin') or '').strip()
        key  = sku or asin
        if not key: continue
        try:   qty   = int(float(row.get('quantity') or 0))
        except: qty  = 0
        try:   price = float(row.get('item-price') or 0)
        except: price = 0.0
        try:   promo = float(row.get('item-promotion-discount') or 0)
        except: promo = 0.0
        rev = max(0.0, price - promo)

        a = agg.setdefault(key, {'qty': 0, 'revenue': 0.0, 'asin': asin, 'sku': sku,
                                  'title': (row.get('product-name') or '')})
        a['qty']     += qty
        a['revenue'] += rev

    # ── 4. Build per-group breakdown ─────────────────────────────────────────
    def split_title(title):
        parts = [p.strip() for p in (title or '').split(' - ') if p.strip()]
        pt   = parts[0] if parts else 'Other'
        pack = parts[1] if len(parts) > 1 else '—'
        var  = parts[2] if len(parts) > 2 else ''
        return pt, pack, var

    grouped = {}
    for key, m in agg.items():
        sku  = m['sku']
        asin = m['asin']
        qty  = m['qty']
        rev  = m['revenue']
        product = prods_by_sku.get(sku.upper())  or prods_by_asin.get(asin.upper())
        cogs    = cogs_by_sku.get(sku.upper())   or cogs_by_asin.get(asin.upper())

        if rev == 0 and product:
            rev = float(product.sale_price or product.list_price or 0) * qty

        if cogs:
            cgs_u = (float(cogs.unit_cost or 0) + float(cogs.duties_cost or 0)
                     + float(cogs.prep_cost or 0) + float(cogs.other_cost or 0))
            fba_u = float(cogs.shipping_cost or 0)
        else:
            cgs_u = fba_u = 0.0

        total_cgs = cgs_u * qty
        fulfill   = fba_u * qty
        amz_fee   = rev * 0.15
        cm        = rev - total_cgs - amz_fee - fulfill

        if product and product.title:
            pt, pack, var = split_title(product.title)
        else:
            pt, pack, var = (m.get('title') or '')[:30] or sku, '—', ''

        gk = (pt, pack)
        if gk not in grouped:
            grouped[gk] = {
                'pt': pt, 'pack': pack,
                'qty': 0, 'revenue': 0.0, 'cgs': 0.0,
                'amz_fee': 0.0, 'fulfill': 0.0, 'cm': 0.0,
                'ppc': 0.0, '_sku_set': set(), '_asin_set': set(),
            }
        g = grouped[gk]
        g['qty']     += qty
        g['revenue'] += rev
        g['cgs']     += total_cgs
        g['amz_fee'] += amz_fee
        g['fulfill'] += fulfill
        g['cm']      += cm
        if sku:  g['_sku_set'].add(sku.upper())
        if asin: g['_asin_set'].add(asin.upper())

    # ── 5. Merge PPC from DB snapshots ───────────────────────────────────────
    # PPCProductSnapshot only captures ~10% of real spend (product attribution).
    # We use it for proportional allocation, then scale to the campaign total.
    from apps.dashboard.models import PPCCampaignSnapshot
    asin_ppc = defaultdict(float)
    sku_ppc  = defaultdict(float)
    for snap in PPCProductSnapshot.objects.filter(
        marketplace=marketplace, date__gte=s_d, date__lte=e_d, campaign_type='sp',
    ).values('asin', 'sku', 'spend'):
        if snap['asin']: asin_ppc[snap['asin'].upper()] += float(snap['spend'] or 0)
        if snap['sku']:  sku_ppc[snap['sku'].upper()]   += float(snap['spend'] or 0)

    # True total from campaign snapshots
    camp_total_ppc = sum(
        float(v or 0) for v in
        PPCCampaignSnapshot.objects.filter(
            marketplace=marketplace, date__gte=s_d, date__lte=e_d,
        ).values_list('spend', flat=True)
    )
    # Scale proportions up to match campaign total
    prod_total_ppc = sum(asin_ppc.values()) or 0
    ppc_scale = (camp_total_ppc / prod_total_ppc) if prod_total_ppc and camp_total_ppc > prod_total_ppc else 1.0
    if ppc_scale > 1.0:
        asin_ppc = defaultdict(float, {k: v * ppc_scale for k, v in asin_ppc.items()})
        sku_ppc  = defaultdict(float, {k: v * ppc_scale for k, v in sku_ppc.items()})

    for g in grouped.values():
        sp = 0.0
        for sku_ in g['_sku_set']:
            sp += sku_ppc.get(sku_, 0)
        if not sp:
            for asin_ in g['_asin_set']:
                sp += asin_ppc.get(asin_, 0)
        g['ppc'] = sp

    # ── 6. Load targets ───────────────────────────────────────────────────────
    def _norm_key(pt, pack):
        digits = re.search(r'\d+', str(pack or ''))
        pn = digits.group(0) if digits else str(pack or '').strip().lower()
        return f"{(pt or '').strip().lower()}::{pn}"

    tar_rows = ProductTypePackMonthlyTarget.objects.filter(
        marketplace=marketplace, month=month_start,
    )
    tar_by_key = {}
    for t in tar_rows:
        monthly = float(t.revenue_target or 0)
        day_tar = (monthly / days_in_month) * period_days if days_in_month else 0
        k = _norm_key(t.product_type, t.pack_size)
        tar_by_key[k] = tar_by_key.get(k, 0) + day_tar

    # ── 7. Finalise rows ──────────────────────────────────────────────────────
    out = []
    for (pt, pack), g in sorted(grouped.items(), key=lambda x: -x[1]['revenue']):
        rev  = g['revenue']
        cm   = g['cm']
        ppc  = g['ppc']
        qty  = g['qty']
        gm   = cm - ppc
        tar  = tar_by_key.get(_norm_key(pt, pack), 0)
        out.append({
            'group':     (pt[:6].upper().replace(' ', '') + pack[:4].upper().replace(' ', ''))[:8],
            'groupName': f'{pt} · {pack}' if pack != '—' else pt,
            'qty':        qty,
            'revenue':    round(rev, 2),
            'cgs':        round(g['cgs'], 2),
            'amzFee':     round(g['amz_fee'], 2),
            'fulfill':    round(g['fulfill'], 2),
            'cm':         round(cm, 2),
            'cmPct':      round((cm / rev * 100) if rev else 0, 2),
            'arpu':       round((rev / qty) if qty else 0, 2),
            'ppcSpend':   round(ppc, 2),
            'grossMargin': round(gm, 2),
            'gmPerUnit':  round((gm / qty) if qty else 0, 2),
            'gmPct':      round((gm / rev * 100) if rev else 0, 2),
            'cpa':        round((ppc / qty) if qty else 0, 2),
            'tacos':      round((ppc / rev * 100) if rev else 0, 2),
            'tarGmDay':   round(tar, 2),
            'gmMinusTar': round(gm - tar, 2),
        })

    return JsonResponse({
        'groups':      out,
        'period':      f'{s_d} → {e_d}',
        'period_days': period_days,
        'report_used': report_result.get('status'),
    })


@login_required
@permission_required('can_manage_cogs')
def cogs(request):
    upload_form    = COGSBulkUploadForm()
    manual_form    = COGSEntryForm()
    fba_form       = FBARateBulkUploadForm()
    upload_result  = None
    fba_result     = None

    if request.method == 'POST':
        action = request.POST.get('action')

        # ── FBA rate upload ─────────────────────────────────────────────────
        if action == 'upload_fba_rates':
            fba_form = FBARateBulkUploadForm(request.POST, request.FILES)
            if fba_form.is_valid():
                fba_result = _process_fba_rates_file(
                    request.FILES['file'],
                    overwrite=fba_form.cleaned_data['overwrite'],
                    user=request.user,
                )
                if fba_result['errors']:
                    messages.warning(request, f"FBA rates: {len(fba_result['errors'])} row errors. "
                                              f"{fba_result['created']} created, {fba_result['updated']} updated.")
                else:
                    messages.success(request, f"✓ FBA rates: {fba_result['created']} created, "
                                              f"{fba_result['updated']} updated.")
                AuditLog.objects.create(user=request.user, action='create',
                    resource='fba_rates:csv', ip_address=request.META.get('REMOTE_ADDR'))

                # Auto-resync each affected (marketplace, window) — re-aggregates
                # only the days where the new rate applies.
                if fba_result['affected_windows']:
                    from .sync import sync_window
                    today = date.today()
                    for mp, win_start, win_end in sorted(fba_result['affected_windows']):
                        end = min(win_end, today)
                        if end < win_start:
                            continue
                        try:
                            res = sync_window(mp, win_start, end, max_wait_seconds=60)
                            if res.get('status') in ('OK', 'CACHED', 'FRESH'):
                                messages.info(request,
                                    f"↻ Recomputed {res.get('days_written', 0)} days "
                                    f"({mp.upper()} {win_start} → {end}) with the new FBA rate.")
                            else:
                                messages.warning(request,
                                    f"FBA rate saved for {mp.upper()} {win_start} → {end}, but resync "
                                    f"returned {res.get('status')}. Re-run "
                                    f"`python manage.py backfill_history --start {win_start} --end {end} "
                                    f"--marketplace {mp}` when Amazon's report is ready.")
                        except Exception as exc:
                            logger.warning('FBA resync failed for %s %s→%s: %s', mp, win_start, end, exc)

        elif action == 'upload_csv':
            upload_form = COGSBulkUploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                upload_result = _process_cogs_csv(
                    request.FILES['csv_file'],
                    overwrite=upload_form.cleaned_data['overwrite'],
                    user=request.user,
                )
                if upload_result['errors']:
                    messages.warning(request, f"{len(upload_result['errors'])} row errors.")
                else:
                    messages.success(request, f"✓ {upload_result['created']} created, {upload_result['updated']} updated.")
                AuditLog.objects.create(user=request.user, action='create',
                    resource='cogs:csv', ip_address=request.META.get('REMOTE_ADDR'))

                # Re-aggregate ONLY the months touched by this upload.
                # Other months (e.g. April) stay untouched.
                if upload_result['affected']:
                    summary = _resync_months_after_cogs(upload_result['affected'], user=request.user)
                    for mp, m, days, status in summary:
                        if status in ('OK', 'CACHED', 'FRESH'):
                            messages.info(request,
                                f"↻ Recomputed {days} days for {mp.upper()} {m:%b %Y} with the new COGS.")
                        else:
                            messages.warning(request,
                                f"COGS saved for {mp.upper()} {m:%b %Y}, but resync returned {status}. "
                                "Run `python manage.py backfill_history --start {0} --end today --marketplace {1}` "
                                "to recompute when Amazon's report is ready.".format(m, mp))

        elif action == 'manual_entry':
            manual_form = COGSEntryForm(request.POST)
            if manual_form.is_valid():
                e = manual_form.save(commit=False)
                e.uploaded_by = request.user
                e.save()
                # Re-aggregate just this (marketplace, month)
                summary = _resync_months_after_cogs(
                    {(e.product.marketplace, e.month)}, user=request.user
                )
                msg = 'COGS entry saved.'
                for mp, m, days, status in summary:
                    if status in ('OK', 'CACHED', 'FRESH'):
                        msg += f' ↻ Recomputed {days} days for {mp.upper()} {m:%b %Y}.'
                messages.success(request, msg)
                return redirect('dashboard:cogs')

    recent     = COGSEntry.objects.select_related('product').order_by('-month', 'product__asin')[:100]
    recent_fba = FBAFeeRate.objects.select_related('product').order_by('-effective_from', 'product__asin')[:50]
    ctx = {
        'upload_form':   upload_form,
        'manual_form':   manual_form,
        'upload_result': upload_result,
        'recent':        recent,
        'fba_form':      fba_form,
        'fba_result':    fba_result,
        'recent_fba':    recent_fba,
    }
    return render(request, 'dashboard/cogs.html', ctx)


def _process_cogs_csv(f, overwrite=False, user=None):
    result = {'created': 0, 'updated': 0, 'errors': [], 'affected': set()}
    content = f.read().decode('utf-8-sig')
    reader  = csv.DictReader(io.StringIO(content))
    raw_headers = reader.fieldnames or []
    normalized = {h.strip().lower(): h for h in raw_headers if h}

    # Accept both legacy and unified business format:
    # SKU, ASIN, Region, Month, Cogs, FBA, ProductType, PackSize, Variant
    has_new_format = {'sku', 'asin', 'region', 'month', 'cogs', 'fba', 'producttype', 'packsize', 'variant'}.issubset(set(normalized.keys()))
    has_legacy_format = {'asin', 'marketplace', 'month', 'unit_cost'}.issubset(set(normalized.keys()))
    if not has_new_format and not has_legacy_format:
        result['errors'].append(
            'Missing columns. Required either '
            '[SKU, ASIN, Region, Month, Cogs, FBA, ProductType, PackSize, Variant] or '
            '[asin, marketplace, month, unit_cost].'
        )
        return result

    def cell(row, key, default=''):
        src = normalized.get(key.lower())
        return (row.get(src, default) if src else default)

    def normalize_marketplace(value):
        v = (value or '').strip().lower()
        aliases = {
            'us': 'usa', 'usa': 'usa', 'united states': 'usa',
            'ca': 'ca', 'canada': 'ca',
            'uk': 'uk', 'gb': 'uk', 'united kingdom': 'uk',
            'de': 'de', 'germany': 'de',
            'ae': 'ae', 'uae': 'ae', 'united arab emirates': 'ae',
            'sa': 'sa', 'ksa': 'sa', 'saudi arabia': 'sa',
        }
        return aliases.get(v, v)

    for i, row in enumerate(reader, start=2):
        try:
            asin = cell(row, 'asin').strip().upper()
            if not asin:
                raise ValueError('ASIN is required')
            mp_raw = cell(row, 'region') if has_new_format else cell(row, 'marketplace')
            mp = normalize_marketplace(mp_raw)
            ms = cell(row, 'month').strip()
            if len(ms) == 7:
                ms += '-01'
            month = datetime.strptime(ms, '%Y-%m-%d').date().replace(day=1)
            sku = cell(row, 'sku').strip()
            product_type = cell(row, 'producttype').strip() or cell(row, 'product_type').strip()
            pack_size = cell(row, 'packsize').strip() or cell(row, 'pack_size').strip()
            variant = cell(row, 'variant').strip()
            if has_new_format:
                missing = []
                if not sku:
                    missing.append('SKU')
                if not product_type:
                    missing.append('ProductType')
                if not pack_size:
                    missing.append('PackSize')
                if not variant:
                    missing.append('Variant')
                if missing:
                    raise ValueError(f"Missing required fields: {', '.join(missing)}")
            generated_title = ' - '.join([part for part in [product_type, pack_size, variant] if part]).strip()
            product, _ = Product.objects.get_or_create(
                asin=asin, marketplace=mp,
                defaults={'title': generated_title or asin, 'brand': 'Infinitee Xclusives'}
            )
            if sku and product.sku != sku:
                product.sku = sku
            if generated_title and (not product.title or product.title == product.asin):
                product.title = generated_title
            if product_type and not product.category:
                product.category = product_type
            if sku or generated_title or product_type:
                product.save(update_fields=['sku', 'title', 'category', 'updated_at'])
            defaults = {
                'unit_cost':     Decimal(str(cell(row, 'cogs', cell(row, 'unit_cost', 0)) or 0)),
                'shipping_cost': Decimal(str(cell(row, 'fba', cell(row, 'shipping_cost', 0)) or 0)),
                'duties_cost':   Decimal(str(row.get('duties_cost', 0) or 0)),
                'prep_cost':     Decimal(str(row.get('prep_cost', 0) or 0)),
                'other_cost':    Decimal(str(row.get('other_cost', 0) or 0)),
                'uploaded_by':   user,
            }
            if overwrite:
                _, created = COGSEntry.objects.update_or_create(
                    product=product, month=month, defaults=defaults)
            else:
                _, created = COGSEntry.objects.get_or_create(
                    product=product, month=month, defaults=defaults)
            if created:
                result['created'] += 1
            else:
                result['updated'] += 1
            result['affected'].add((mp, month))
        except Exception as e:
            result['errors'].append(f'Row {i}: {e}')
    return result


def _process_fba_rates_file(f, overwrite=True, user=None):
    """
    Parse a CSV or .xlsx upload of per-SKU FBA fees with effective dates.
    Required columns (case-insensitive): SKU, EffectiveFrom, FBAFee.
    Optional: ASIN, Region (defaults to product's marketplace).
    Returns {'created', 'updated', 'errors', 'affected_windows'}.
    `affected_windows` is a set of (marketplace, window_start, window_end) tuples
    so the caller can resync exactly the affected days.
    """
    result = {'created': 0, 'updated': 0, 'errors': [], 'affected_windows': set()}
    name = (getattr(f, 'name', '') or '').lower()

    rows = []
    try:
        if name.endswith('.xlsx'):
            from openpyxl import load_workbook
            wb = load_workbook(f, data_only=True)
            ws = wb.active
            sheet_rows = list(ws.iter_rows(values_only=True))
            if not sheet_rows:
                result['errors'].append('Sheet is empty.')
                return result
            headers = [str(h).strip() if h is not None else '' for h in sheet_rows[0]]
            for r in sheet_rows[1:]:
                rows.append({headers[i]: r[i] for i in range(len(headers)) if headers[i]})
        else:
            content = f.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
    except Exception as exc:
        result['errors'].append(f'Could not read file: {exc}')
        return result

    if not rows:
        result['errors'].append('No data rows found.')
        return result

    # Build flexible header lookup
    sample_headers = {(k or '').strip().lower(): k for k in rows[0].keys() if k}

    def col(row, *aliases):
        for a in aliases:
            src = sample_headers.get(a.lower())
            if src is not None:
                v = row.get(src)
                if v is not None and str(v).strip() != '':
                    return v
        return None

    mp_aliases = {
        'us': 'usa', 'usa': 'usa', 'united states': 'usa',
        'ca': 'ca', 'canada': 'ca',
        'uk': 'uk', 'gb': 'uk', 'united kingdom': 'uk',
        'de': 'de', 'germany': 'de',
        'ae': 'ae', 'uae': 'ae',
        'sa': 'sa', 'ksa': 'sa',
    }

    # Track per-(marketplace, product) the set of effective_from dates so we
    # can compute the resync windows after we've inserted everything.
    from collections import defaultdict
    new_dates = defaultdict(set)  # (mp, product_id) → {effective_from, ...}

    for i, row in enumerate(rows, start=2):
        try:
            sku  = (str(col(row, 'sku')  or '')).strip()
            asin = (str(col(row, 'asin') or '')).strip().upper()
            mp_raw = col(row, 'region', 'marketplace', 'mp')
            mp_in  = (str(mp_raw or '')).strip().lower()
            mp_in  = mp_aliases.get(mp_in, mp_in)
            eff_raw = col(row, 'effectivefrom', 'effective_from', 'effective from', 'date')
            fee_raw = col(row, 'fbafee', 'fba_fee', 'fba fee', 'fee')

            if not sku and not asin:
                raise ValueError('Need SKU or ASIN')
            if not eff_raw or fee_raw in (None, ''):
                raise ValueError('Missing EffectiveFrom or FBAFee')

            # Parse effective_from (accept date or datetime or ISO string)
            if hasattr(eff_raw, 'date'):
                eff_dt = eff_raw.date() if hasattr(eff_raw, 'hour') else eff_raw
            else:
                eff_dt = datetime.strptime(str(eff_raw).strip()[:10], '%Y-%m-%d').date()

            try:
                fee = Decimal(str(fee_raw).replace(',', '').replace('$', ''))
            except Exception:
                raise ValueError(f'Invalid FBAFee: {fee_raw!r}')

            # Find product
            qs = Product.objects
            if mp_in:
                qs = qs.filter(marketplace=mp_in)
            product = (qs.filter(sku=sku).first() if sku
                       else qs.filter(asin=asin).first())
            if not product and asin:
                product = qs.filter(asin=asin).first()
            if not product:
                raise ValueError(f'Product not found (sku={sku} asin={asin} region={mp_in})')

            defaults = {'fba_fee_per_unit': fee, 'uploaded_by': user}
            if overwrite:
                _, created = FBAFeeRate.objects.update_or_create(
                    product=product, effective_from=eff_dt, defaults=defaults,
                )
            else:
                _, created = FBAFeeRate.objects.get_or_create(
                    product=product, effective_from=eff_dt, defaults=defaults,
                )
            if created:
                result['created'] += 1
            else:
                result['updated'] += 1
            new_dates[(product.marketplace, product.id)].add(eff_dt)
        except Exception as exc:
            result['errors'].append(f'Row {i}: {exc}')

    # Compute resync windows: for each (mp, product), the affected window for
    # a new effective_from = [that date, day_before_next_rate_for_same_product].
    # We collapse into per-marketplace windows so the resync is one report each.
    today_d = date.today()
    per_mp_window = {}   # mp → (min_start, max_end)
    for (mp, product_id), dates in new_dates.items():
        all_dates = sorted({
            d for d in FBAFeeRate.objects
                          .filter(product_id=product_id)
                          .values_list('effective_from', flat=True)
        })
        for d in dates:
            # Window for this effective_from: [d, day before next, or today]
            try:
                next_d = next(x for x in all_dates if x > d)
                end = next_d - timedelta(days=1)
            except StopIteration:
                end = today_d
            cur = per_mp_window.get(mp)
            if cur is None:
                per_mp_window[mp] = (d, end)
            else:
                per_mp_window[mp] = (min(cur[0], d), max(cur[1], end))
    result['affected_windows'] = {(mp, s, e) for mp, (s, e) in per_mp_window.items()}
    return result


def _resync_months_after_cogs(affected, user=None):
    """
    After COGS rows are uploaded for one or more (marketplace, month) pairs,
    rebuild only those months' DailyMetric rows. Other months stay untouched
    — uploading May COGS does NOT change April's report.
    Returns a list of (mp, month, days_written) tuples for messaging.
    """
    from .sync import sync_window
    today = date.today()
    summary = []
    for mp, month_first in sorted(affected):
        # Last day of that month, capped at today (don't fetch future days)
        if month_first.month == 12:
            month_last = date(month_first.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_last = date(month_first.year, month_first.month + 1, 1) - timedelta(days=1)
        end = min(month_last, today)
        if end < month_first:
            continue
        try:
            res = sync_window(mp, month_first, end, max_wait_seconds=60)
            summary.append((mp, month_first, res.get('days_written', 0), res.get('status', '?')))
        except Exception as exc:
            logger.warning('COGS resync failed for %s %s: %s', mp, month_first, exc)
            summary.append((mp, month_first, 0, f'ERROR: {exc}'))
    return summary


@login_required
@permission_required('can_manage_targets')
def targets(request):
    today = date.today()
    start_month = date(today.year, 1, 1)
    planning_months = [date(today.year, m, 1) for m in range(1, 13)]

    def make_row_key(product_type: str, pack_size: str) -> str:
        key = f'{product_type}__{pack_size}'.lower()
        return re.sub(r'[^a-z0-9_]+', '_', key)

    def split_title_parts(title: str):
        parts = [p.strip() for p in (title or '').split('-') if p.strip()]
        product_type = parts[0] if parts else (title or '').strip() or 'Unknown'
        if len(parts) > 1:
            pack_size = parts[1]
        else:
            m = re.search(r'(\d+\s*-\s*pack|\d+\s*pack)', (title or '').lower())
            pack_size = m.group(1).replace(' ', '') if m else '-'
        return product_type, (pack_size or '-')

    # ── Handle POST ─────────────────────────────────────────────────────────
    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        pk     = request.POST.get('pk')

        if action == 'delete' and pk:
            MonthlyTarget.objects.filter(pk=pk).delete()
            messages.success(request, 'Target deleted.')
            return redirect('dashboard:targets')

        # ── Bulk save: 12 months for one marketplace at once ─────────────────
        if action == 'bulk_save':
            mp = request.POST.get('bulk_marketplace', 'usa')
            saved = 0
            for month_date in planning_months:
                key = month_date.strftime('%Y-%m')
                rev  = request.POST.get(f'rev_{key}')
                ppc  = request.POST.get(f'ppc_{key}')
                tacos = request.POST.get(f'tacos_{key}')
                gm   = request.POST.get(f'gm_{key}')
                units = request.POST.get(f'units_{key}')
                if not rev:
                    continue
                MonthlyTarget.objects.update_or_create(
                    marketplace=mp, month=month_date,
                    defaults={
                        'revenue_target': rev,
                        'ppc_budget':     ppc or 0,
                        'tacos_target':   tacos or 15,
                        'gm_target':      gm or 25,
                        'units_target':   units or 0,
                        'created_by':     request.user,
                    }
                )
                saved += 1
            messages.success(request, f'✓ {saved} monthly targets saved for {mp.upper()}.')
            AuditLog.objects.create(user=request.user, action='update',
                resource=f'targets:bulk:{mp}:{today.year}',
                ip_address=request.META.get('REMOTE_ADDR'))
            return redirect(f'/dashboard/targets/?view=annual&mp={mp}')

        if action == 'bulk_save_products':
            mp = request.POST.get('bulk_marketplace', 'usa')
            products = Product.objects.filter(marketplace=mp).order_by('title', 'asin')
            group_pairs = {}
            for p in products:
                pt, ps = split_title_parts(p.title or p.asin)
                group_pairs[make_row_key(pt, ps)] = (pt, ps)
            saved = 0
            for row_key, (product_type, pack_size) in group_pairs.items():
                for month_date in planning_months:
                    key = month_date.strftime('%Y-%m')
                    raw_val = request.POST.get(f'rev_{row_key}_{key}', '').strip()
                    if raw_val == '':
                        continue
                    ProductTypePackMonthlyTarget.objects.update_or_create(
                        marketplace=mp,
                        product_type=product_type,
                        pack_size=pack_size,
                        month=month_date,
                        defaults={'revenue_target': raw_val, 'created_by': request.user},
                    )
                    saved += 1

            messages.success(request, f'✓ {saved} product targets saved for {mp.upper()} ({today:%b}–Dec).')
            return redirect(f'/dashboard/targets/?view=annual&mp={mp}')

        # ── Excel upload: annual product-level targets (12 months × N products) ──
        if action == 'upload_targets_xlsx' and request.FILES.get('targets_xlsx'):
            try:
                from openpyxl import load_workbook
            except ImportError:
                messages.error(request, 'openpyxl is not installed on the server. Run: pip install openpyxl')
                return redirect(f'/dashboard/targets/?view=annual&mp={request.POST.get("bulk_marketplace", "usa")}')

            mp_default = request.POST.get('bulk_marketplace', 'usa')
            f = request.FILES['targets_xlsx']
            try:
                wb = load_workbook(f, data_only=True)
            except Exception as e:
                messages.error(request, f'Could not read Excel file: {e}')
                return redirect(f'/dashboard/targets/?view=annual&mp={mp_default}')

            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if len(rows) < 2:
                messages.error(request, 'Sheet has no data rows.')
                return redirect(f'/dashboard/targets/?view=annual&mp={mp_default}')

            # Header parsing (case-insensitive). Required: ProductType, PackSize.
            # Optional: Marketplace. Month columns: YYYY-MM, "Jan", "January", "Jan 2026", etc.
            raw_headers = [str(h).strip() if h is not None else '' for h in rows[0]]
            header_idx = {h.lower(): i for i, h in enumerate(raw_headers) if h}

            def col(*aliases):
                for a in aliases:
                    if a.lower() in header_idx:
                        return header_idx[a.lower()]
                return None

            i_pt   = col('producttype', 'product type', 'product_type')
            i_ps   = col('packsize',    'pack size',    'pack_size')
            i_mp   = col('marketplace', 'region', 'mp')
            if i_pt is None or i_ps is None:
                messages.error(request, 'Missing required columns: ProductType and PackSize.')
                return redirect(f'/dashboard/targets/?view=annual&mp={mp_default}')

            # Map remaining columns to months. Accept 2026-01, "Jan", "January", "Jan-2026", "Jan 2026"
            month_aliases = {
                'jan':1,'january':1,'feb':2,'february':2,'mar':3,'march':3,
                'apr':4,'april':4,'may':5,'jun':6,'june':6,'jul':7,'july':7,
                'aug':8,'august':8,'sep':9,'sept':9,'september':9,
                'oct':10,'october':10,'nov':11,'november':11,'dec':12,'december':12,
            }
            month_columns = {}  # column index → date(year, month, 1)
            for idx, h in enumerate(raw_headers):
                if not h or idx in (i_pt, i_ps, i_mp):
                    continue
                hl = h.strip().lower().replace(' ', '-').replace('_', '-')
                # YYYY-MM
                m = re.match(r'^(\d{4})-(\d{1,2})(?:-\d{1,2})?$', hl)
                if m:
                    y, mo = int(m.group(1)), int(m.group(2))
                    if 1 <= mo <= 12:
                        month_columns[idx] = date(y, mo, 1)
                        continue
                # Month name (with optional year)
                parts = re.split(r'[-/]', hl)
                month_name = parts[0]
                year_part  = parts[1] if len(parts) > 1 else None
                if month_name in month_aliases:
                    mo = month_aliases[month_name]
                    try:
                        y = int(year_part) if year_part and len(year_part) == 4 else today.year
                    except ValueError:
                        y = today.year
                    month_columns[idx] = date(y, mo, 1)

            if not month_columns:
                messages.error(request, 'No month columns detected. Use headers like "2026-01" or "Jan".')
                return redirect(f'/dashboard/targets/?view=annual&mp={mp_default}')

            mp_aliases = {
                'us':'usa','usa':'usa','united states':'usa',
                'ca':'ca','canada':'ca',
                'uk':'uk','gb':'uk','united kingdom':'uk',
                'de':'de','germany':'de',
                'ae':'ae','uae':'ae','united arab emirates':'ae',
                'sa':'sa','ksa':'sa','saudi arabia':'sa',
            }

            saved = 0
            row_errors = []
            for r_idx, row in enumerate(rows[1:], start=2):
                product_type = (str(row[i_pt]).strip() if row[i_pt] is not None else '')
                pack_size    = (str(row[i_ps]).strip() if row[i_ps] is not None else '')
                if not product_type or not pack_size:
                    continue
                if i_mp is not None and row[i_mp] not in (None, ''):
                    raw_mp = str(row[i_mp]).strip().lower()
                    mp = mp_aliases.get(raw_mp, raw_mp)
                else:
                    mp = mp_default

                for c_idx, month_date in month_columns.items():
                    val = row[c_idx]
                    if val is None or val == '':
                        continue
                    try:
                        amount = Decimal(str(val).replace(',', '').replace('$', ''))
                    except Exception:
                        row_errors.append(f'Row {r_idx} col {raw_headers[c_idx]}: invalid value "{val}"')
                        continue
                    ProductTypePackMonthlyTarget.objects.update_or_create(
                        marketplace=mp,
                        product_type=product_type,
                        pack_size=pack_size,
                        month=month_date,
                        defaults={'revenue_target': amount, 'created_by': request.user},
                    )
                    saved += 1

            if row_errors:
                messages.warning(request, f'Saved {saved} cells. {len(row_errors)} errors: ' + '; '.join(row_errors[:5]))
            else:
                messages.success(request, f'✓ Uploaded {saved} target cells from spreadsheet.')
            AuditLog.objects.create(user=request.user, action='upload',
                resource=f'targets:xlsx:{mp_default}',
                ip_address=request.META.get('REMOTE_ADDR'))
            return redirect(f'/dashboard/targets/?view=annual&mp={mp_default}')

        # ── Single save ───────────────────────────────────────────────────────
        instance = MonthlyTarget.objects.filter(pk=pk).first() if pk else None
        form = MonthlyTargetForm(request.POST, instance=instance)
        if form.is_valid():
            t = form.save(commit=False)
            if not instance:
                t.created_by = request.user
            t.save()
            messages.success(request, f'Target saved for {t.get_marketplace_display()} — {t.month:%B %Y}.')
            AuditLog.objects.create(user=request.user, action='update',
                resource=f'target:{t.marketplace}:{t.month}',
                ip_address=request.META.get('REMOTE_ADDR'))
            return redirect('dashboard:targets')
        # form invalid — fall through to render with errors
    else:
        form = MonthlyTargetForm()

    # ── View mode ────────────────────────────────────────────────────────────
    view_mode     = request.GET.get('view', 'annual')   # 'monthly' | 'annual'
    active_mp     = request.GET.get('mp', 'usa')

    # ── All existing targets ─────────────────────────────────────────────────
    all_targets = MonthlyTarget.objects.order_by('-month', 'marketplace')
    grouped = {}
    for t in all_targets:
        key = str(t.month)[:7]
        grouped.setdefault(key, []).append(t)

    # ── Annual planning grid (12 months × this marketplace) ──────────────────
    year = today.year
    annual_months = planning_months
    annual_targets_map = {}  # key: 'YYYY-MM' → MonthlyTarget or None

    for month_date in annual_months:
        existing = MonthlyTarget.objects.filter(
            marketplace=active_mp, month=month_date
        ).first()
        annual_targets_map[month_date.strftime('%Y-%m')] = existing

    products = Product.objects.filter(marketplace=active_mp).order_by('title', 'asin')
    p_targets = ProductTypePackMonthlyTarget.objects.filter(
        marketplace=active_mp,
        month__gte=start_month,
        month__year=today.year,
    )
    p_target_map = {}
    for t in p_targets:
        p_target_map.setdefault((t.product_type, t.pack_size), {})[t.month.strftime('%Y-%m')] = t

    def infer_pack_size(title: str) -> str:
        m = re.search(r'(\d+\s*-\s*pack|\d+\s*pack)', title.lower())
        if not m:
            return '-'
        return m.group(1).replace(' ', '')

    grouped_pairs = {}
    for p in products:
        pt, ps = split_title_parts(p.title or p.asin)
        grouped_pairs[(pt, ps)] = True
    for t in p_targets:
        grouped_pairs[(t.product_type, t.pack_size)] = True

    product_rows = []
    for product_type, pack_size in sorted(grouped_pairs.keys()):
        per_month_targets = p_target_map.get((product_type, pack_size), {})
        yearly_total = sum(
            float(t.revenue_target) for t in per_month_targets.values()
            if t and t.revenue_target is not None
        )
        row_key = make_row_key(product_type, pack_size)
        product_rows.append({
            'row_key': row_key,
            'product_type': product_type,
            'pack_size': pack_size,
            'targets_by_month': per_month_targets,
            'yearly_total': yearly_total,
        })

    month_totals = {}
    for month_date in annual_months:
        key = month_date.strftime('%Y-%m')
        month_totals[key] = float(
            ProductTypePackMonthlyTarget.objects.filter(
                marketplace=active_mp,
                month=month_date,
            ).aggregate(total=Sum('revenue_target'))['total'] or 0
        )
    grand_total = sum(month_totals.values())

    next_month = date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
    this_month_total = month_totals.get(today.strftime('%Y-%m'), 0)
    next_month_total = month_totals.get(next_month.strftime('%Y-%m'), 0)
    tracking = {
        'this_month': today.replace(day=1),
        'next_month': next_month,
        'this_month_total': this_month_total,
        'next_month_total': next_month_total,
        'delta_to_next': next_month_total - this_month_total,
        'grand_total': grand_total,
    }

    marketplace_list = [
        ('usa', 'United States', '🇺🇸'),
        ('ca',  'Canada',        '🇨🇦'),
        ('uk',  'United Kingdom','🇬🇧'),
        ('de',  'Germany',       '🇩🇪'),
        ('ae',  'UAE',           '🇦🇪'),
        ('sa',  'Saudi Arabia',  '🇸🇦'),
    ]

    ctx = {
        'form':              form,
        'grouped':           grouped,
        'marketplace_list':  marketplace_list,
        'view_mode':         view_mode,
        'active_mp':         active_mp,
        'annual_months':     annual_months,
        'annual_targets_map': annual_targets_map,
        'product_rows':      product_rows,
        'month_totals':      month_totals,
        'tracking':          tracking,
        'year':              year,
        'today':             today,
    }
    return render(request, 'dashboard/targets.html', ctx)


@login_required
@permission_required('can_manage_catalog')
def catalog(request):
    mp = request.GET.get('mp', 'all')
    qs = Product.objects.order_by('marketplace', 'asin')
    if mp != 'all':
        qs = qs.filter(marketplace=mp)
    if request.method == 'POST' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        data  = json.loads(request.body)
        pk    = data.get('pk')
        field = data.get('field')
        val   = data.get('value')
        allowed = ['status','sku','category','title']
        if field in allowed and pk:
            Product.objects.filter(pk=pk).update(**{field: val})
            return JsonResponse({'ok': True})
        return JsonResponse({'error': 'Invalid'}, status=400)

    grouped_catalog = {}
    for p in qs:
        title = p.title or ''
        parts = [s.strip() for s in title.split('-') if s.strip()]
        product_type = parts[0] if parts else (p.category or 'Misc')
        pack_size = parts[1] if len(parts) > 1 else 'Unspecified'
        variant = parts[2] if len(parts) > 2 else ''
        grouped_catalog.setdefault(product_type, {}).setdefault(pack_size, []).append({
            'sku': p.sku or p.asin,
            'asin': p.asin,
            'variant': variant or title or p.asin,
            'pk': p.pk,
        })

    grouped_rows = []
    for product_type in sorted(grouped_catalog.keys()):
        packs = grouped_catalog[product_type]
        pack_rows = []
        sku_count = 0
        for pack in sorted(packs.keys()):
            items = packs[pack]
            sku_count += len(items)
            pack_rows.append({'pack': pack, 'items': items})
        grouped_rows.append({
            'product_type': product_type,
            'pack_count': len(pack_rows),
            'sku_count': sku_count,
            'packs': pack_rows,
        })

    ctx = {
        'products': qs, 'mp': mp,
        'grouped_rows': grouped_rows,
        'allowed_marketplaces': request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys()),
    }
    return render(request, 'dashboard/catalog.html', ctx)


@login_required
@permission_required('can_manage_catalog')
def product_form(request, pk=None):
    instance = get_object_or_404(Product, pk=pk) if pk else None
    form = ProductForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        p = form.save(commit=False)
        p.updated_by = request.user
        p.save()
        messages.success(request, f'Product {p.asin} saved.')
        return redirect('dashboard:catalog')
    return render(request, 'dashboard/product_form.html', {'form': form, 'instance': instance})


@login_required
@permission_required('can_generate_ai_summary')
def executive_summary(request):
    return render(request, 'dashboard/summary.html')


@login_required
@permission_required('can_generate_ai_summary')
def summary_stream(request):
    import requests as http

    mp     = request.GET.get('marketplace', 'usa')
    rev    = request.GET.get('revenue', 'N/A')
    units  = request.GET.get('units', 'N/A')
    ppc    = request.GET.get('ppc_spend', 'N/A')
    tacos  = request.GET.get('tacos', 'N/A')
    gm_pct = request.GET.get('gm_pct', 'N/A')
    cm_pct = request.GET.get('cm_pct', 'N/A')
    vs_tgt = request.GET.get('vs_target', 'N/A')
    acos   = request.GET.get('acos_avg', 'N/A')

    today  = date.today()
    target = MonthlyTarget.objects.filter(marketplace=mp, month=today.replace(day=1)).first()
    target_info = (
        f"Monthly revenue target ${target.revenue_target:,.0f}, TACoS target {target.tacos_target}%, PPC budget ${target.ppc_budget:,.0f}."
        if target else "No monthly targets set for this marketplace."
    )

    metrics_7d = DailyMetric.objects.filter(marketplace=mp, date__gte=today-timedelta(days=7)).order_by('date')
    trend_info = ""
    if metrics_7d.exists():
        rev_7d = [f"${float(m.revenue):,.0f}" for m in metrics_7d]
        trend_info = f"7-day revenue: {', '.join(rev_7d)}"

    # Priority: new AIProviderConfig(anthropic) → legacy AnthropicConfig → settings fallback
    from apps.amazon_api.models import AIProviderConfig as _AIProv
    ai_prov_cfg   = _AIProv.get_for('anthropic')
    anthropic_cfg = AnthropicConfig.get_active()

    if ai_prov_cfg:
        api_key = ai_prov_cfg.api_key
        model   = ai_prov_cfg.get_model() or settings.ANTHROPIC_MODEL
    elif anthropic_cfg:
        api_key = anthropic_cfg.api_key or settings.ANTHROPIC_API_KEY
        model   = anthropic_cfg.model or settings.ANTHROPIC_MODEL
    else:
        api_key = settings.ANTHROPIC_API_KEY
        model   = settings.ANTHROPIC_MODEL

    if not api_key:
        def _err():
            yield 'data: {"error": "Anthropic API key not configured. Go to API Config → Anthropic to add your key."}\n\n'
        return StreamingHttpResponse(_err(), content_type='text/event-stream')

    system = """You are a Senior Amazon E-Commerce Analyst for Infinitee Xclusives, a private-label Home & Kitchen brand (towels, bedsheets) across 6 Amazon marketplaces. Manufacturing: Pakistan/India, 45-day lead time.

Provide CEO-level analysis. No filler. Use exact numbers.

Structure response with EXACTLY these markdown sections:
## 🔑 Key Insight
## 📊 Performance Interpretation
## ✅ Recommended Actions
## ⚠️ Risks & Watch Items"""

    prompt = f"""Executive summary for {mp.upper()} marketplace — {today}:

**KPIs:** Revenue: {rev} | Units: {units} | PPC: {ppc} | TACoS: {tacos} | GM%: {gm_pct} | CM%: {cm_pct} | vs Target: {vs_tgt} | ACoS: {acos}
**Targets:** {target_info}
**Trend:** {trend_info or 'No historical data yet.'}"""

    AuditLog.objects.create(user=request.user, action='ai_summary',
        resource=f'summary:{mp}:{today}', ip_address=request.META.get('REMOTE_ADDR'))

    def generate():
        try:
            resp = http.post(
                'https://api.anthropic.com/v1/messages',
                headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'},
                json={'model': model, 'max_tokens': 1024, 'stream': True, 'system': system,
                      'messages': [{'role': 'user', 'content': prompt}]},
                stream=True, timeout=60,
            )
            # Surface HTTP-level errors immediately (400 = bad key/model/credits,
            # 401 = invalid key, 429 = rate limit, 529 = overloaded).
            # Without this check the error body is never emitted because it
            # doesn't contain SSE `data:` lines, leaving the UI stuck forever.
            if not resp.ok:
                try:
                    err_body = resp.json()
                    err_msg = err_body.get('error', {}).get('message') or resp.text[:300]
                except Exception:
                    err_msg = resp.text[:300] or f'HTTP {resp.status_code}'
                logger.error('Anthropic API error %s: %s', resp.status_code, err_msg)
                yield f'data: {json.dumps({"error": f"[{resp.status_code}] {err_msg}"})}\n\n'
                return

            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
                if line.startswith('data:'):
                    payload = line[5:].strip()
                    if payload == '[DONE]':
                        yield 'data: [DONE]\n\n'
                        break
                    try:
                        evt = json.loads(payload)
                        if evt.get('type') == 'content_block_delta':
                            delta = evt.get('delta', {}).get('text', '')
                            if delta:
                                yield f'data: {json.dumps({"text": delta})}\n\n'
                        elif evt.get('type') == 'error':
                            # Streaming error event from Anthropic
                            err_msg = evt.get('error', {}).get('message', str(evt))
                            yield f'data: {json.dumps({"error": err_msg})}\n\n'
                            return
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.error(f'Summary stream error: {e}')
            yield f'data: {json.dumps({"error": str(e)})}\n\n'

    r = StreamingHttpResponse(generate(), content_type='text/event-stream')
    r['Cache-Control'] = 'no-cache'
    r['X-Accel-Buffering'] = 'no'
    return r


@login_required
def export_csv(request):
    mp    = request.GET.get('mp', 'usa')
    start = request.GET.get('start', str(date.today() - timedelta(days=30)))
    end   = request.GET.get('end',   str(date.today()))
    if not request.user.can_access_marketplace(mp):
        return HttpResponse('Access denied', status=403)
    AuditLog.objects.create(user=request.user, action='export',
        resource=f'historical:{mp}', ip_address=request.META.get('REMOTE_ADDR'))
    qs = DailyMetric.objects.filter(marketplace=mp, date__gte=start, date__lte=end).order_by('date')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="ix_{mp}_{start}_{end}.csv"'
    w = csv.writer(response)
    w.writerow(['Date','Marketplace','Revenue','Units','Orders','Sessions','CVR%',
                'PPC Spend','TACoS%','ACoS%','Gross Margin','GM%','CM','CM%'])
    for m in qs:
        rev_f  = float(m.revenue or 0)
        cm_f   = float(m.contribution_margin or 0)
        ppc_f  = float(m.ppc_spend or 0)
        gm_f   = cm_f - ppc_f        # GM = CM − PPC
        gm_pct_f = (gm_f / rev_f * 100) if rev_f else 0.0
        w.writerow([m.date,m.marketplace,m.revenue,m.units,m.orders,m.sessions,
                    f'{float(m.conversion_rate)*100:.2f}',m.ppc_spend,
                    f'{float(m.tacos)*100:.2f}',f'{float(m.acos)*100:.2f}',
                    f'{gm_f:.2f}',f'{gm_pct_f:.2f}',
                    m.contribution_margin,f'{float(m.cm_pct)*100:.2f}'])
    return response


@login_required
@permission_required('can_manage_cogs')
def fba_rates_template_xlsx(request):
    """Download an Excel template pre-filled with the user's products
    and example peak/off-peak effective dates. If FBAFeeRate rows already
    exist for a product, they're included so the user can edit + re-upload.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
    except ImportError:
        return HttpResponse('openpyxl is not installed. Run: pip install openpyxl', status=500)

    mp = request.GET.get('mp', 'usa')
    today = date.today()

    # Typical Amazon US peak surcharge cycle: Oct 15 → Jan 14 (US/CA/MX).
    # Pick the next peak start at/after today, and the matching peak end.
    peak_start_year = today.year if today < date(today.year, 10, 15) else today.year
    peak_start = date(peak_start_year, 10, 15)
    peak_end_next = date(peak_start_year + 1, 1, 15)

    # Existing rates pre-fill (keyed by product so we don't duplicate rows)
    existing = {}
    for r in FBAFeeRate.objects.filter(product__marketplace=mp).select_related('product'):
        existing.setdefault(r.product_id, []).append(
            (r.effective_from, float(r.fba_fee_per_unit))
        )

    products = list(Product.objects.filter(marketplace=mp).order_by('sku', 'asin'))

    wb = Workbook()
    ws = wb.active
    ws.title = f'FBA Rates {mp.upper()}'

    headers = ['SKU', 'ASIN', 'Region', 'EffectiveFrom', 'FBAFee']
    ws.append(headers)
    bold = Font(bold=True)
    fill = PatternFill('solid', fgColor='F3F4F5')
    for c_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c_idx)
        cell.font = bold
        cell.fill = fill
        cell.alignment = Alignment(horizontal='center')

    for p in products:
        existing_for_p = sorted(existing.get(p.id, []))
        if existing_for_p:
            # Use real history — user edits / re-uploads
            for eff, fee in existing_for_p:
                ws.append([p.sku or '', p.asin, mp, eff.isoformat(), fee])
        else:
            # Two example rows so the user sees the peak / off-peak pattern
            ws.append([p.sku or '', p.asin, mp, peak_start.isoformat(),    ''])  # peak start
            ws.append([p.sku or '', p.asin, mp, peak_end_next.isoformat(), ''])  # peak end

    # Column widths
    widths = [22, 14, 8, 14, 10]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    # A small instructions sheet
    info = wb.create_sheet('How to fill')
    info.append(['How to use this template'])
    info.append([])
    info.append(['1. Each row = one FBA fulfilment rate that takes effect on EffectiveFrom.'])
    info.append(['2. The rate stays in effect until the next EffectiveFrom for the same SKU.'])
    info.append(['3. Two rows per SKU per year is typical:'])
    info.append(['     - Oct 15 (peak surcharge begins)'])
    info.append(['     - Jan 15 (peak ends, off-peak rate resumes)'])
    info.append([])
    info.append(['Columns:'])
    info.append(['  SKU            - your seller SKU'])
    info.append(['  ASIN           - Amazon ASIN (used if SKU is blank)'])
    info.append(['  Region         - usa / ca / uk / de / ae / sa'])
    info.append(['  EffectiveFrom  - YYYY-MM-DD (date the rate begins)'])
    info.append(['  FBAFee         - USD per unit (Amazon’s published rate)'])
    info.append([])
    info.append(['Tip: if you don’t upload any FBA rates, the dashboard falls back to'])
    info.append(['     the FBA column from your COGS upload (one rate per month).'])
    info.column_dimensions['A'].width = 75
    info['A1'].font = Font(bold=True, size=13)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = (
        f'attachment; filename="fba_rates_template_{mp}.xlsx"'
    )
    wb.save(response)
    return response


@login_required
@permission_required('can_manage_targets')
def targets_template_xlsx(request):
    """Download an Excel template pre-filled with the user's product groups
    for the active marketplace. Header row: ProductType, PackSize, Marketplace,
    then the 12 months of the current year (YYYY-MM)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
    except ImportError:
        return HttpResponse('openpyxl is not installed. Run: pip install openpyxl', status=500)

    mp = request.GET.get('mp', 'usa')
    year = date.today().year

    months = [date(year, m, 1) for m in range(1, 13)]
    months_iso = [m.strftime('%Y-%m') for m in months]

    # Discover existing (product_type, pack_size) pairs from the catalog
    def split_title_parts(title: str):
        parts = [p.strip() for p in (title or '').split('-') if p.strip()]
        product_type = parts[0] if parts else (title or '').strip() or 'Unknown'
        pack_size    = parts[1] if len(parts) > 1 else '-'
        return product_type, pack_size

    pairs = set()
    for p in Product.objects.filter(marketplace=mp):
        pairs.add(split_title_parts(p.title or p.asin))
    for t in ProductTypePackMonthlyTarget.objects.filter(marketplace=mp):
        pairs.add((t.product_type, t.pack_size))

    # Load existing target values to pre-fill the cells
    existing = {}
    for t in ProductTypePackMonthlyTarget.objects.filter(
        marketplace=mp, month__year=year,
    ):
        existing[(t.product_type, t.pack_size, t.month.strftime('%Y-%m'))] = float(t.revenue_target or 0)

    wb = Workbook()
    ws = wb.active
    ws.title = f'Targets {mp.upper()} {year}'

    headers = ['ProductType', 'PackSize', 'Marketplace'] + months_iso
    ws.append(headers)
    bold = Font(bold=True)
    fill = PatternFill('solid', fgColor='F3F4F5')
    for c_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c_idx)
        cell.font = bold
        cell.fill = fill
        cell.alignment = Alignment(horizontal='center')

    for product_type, pack_size in sorted(pairs):
        row = [product_type, pack_size, mp]
        for mi in months_iso:
            row.append(existing.get((product_type, pack_size, mi), ''))
        ws.append(row)

    # Column widths
    widths = [22, 14, 12] + [11] * 12
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else 'A' + chr(64 + i - 26)].width = w

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = (
        f'attachment; filename="annual_targets_template_{mp}_{year}.xlsx"'
    )
    wb.save(response)
    return response
