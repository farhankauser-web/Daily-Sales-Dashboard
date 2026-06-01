"""
apps/amazon_api/views.py — API Configuration Management + SP-API service
"""
import json
import time
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import calendar
from zoneinfo import ZoneInfo
import random

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.conf import settings

from .models import AmazonAPIConfig, AnthropicConfig, AIProviderConfig, APISyncLog
from .forms import AmazonAPIConfigForm, AnthropicConfigForm, AIProviderConfigForm
from .services import SPAPIClient, AdsAPIClient
from apps.core.decorators import permission_required
from apps.users.models import AuditLog
from apps.dashboard.models import ProductTypePackMonthlyTarget

logger = logging.getLogger(__name__)


# ── API CONFIG LIST ───────────────────────────────────────────────────────────
@login_required
@permission_required('can_configure_api')
def config_list(request):
    configs = AmazonAPIConfig.objects.all().order_by('marketplace')
    # Build per-provider dict for the AI section (falls back to legacy AnthropicConfig row)
    ai_providers = {obj.provider: obj for obj in AIProviderConfig.objects.all()}
    # If no AIProviderConfig for anthropic yet, show legacy AnthropicConfig status
    legacy_anthropic = AnthropicConfig.objects.first()
    return render(request, 'amazon_api/config_list.html', {
        'configs':          configs,
        'anthropic':        legacy_anthropic,        # backward compat for old template section
        'ai_providers':     ai_providers,            # new unified AI section
        'legacy_anthropic': legacy_anthropic,
    })


# ── MARKETPLACE CONFIG FORM ───────────────────────────────────────────────────
@login_required
@permission_required('can_configure_api')
def config_form(request, pk=None):
    instance = get_object_or_404(AmazonAPIConfig, pk=pk) if pk else None
    form = AmazonAPIConfigForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        cfg = form.save(commit=False)
        if pk:
            cfg.updated_by = request.user
        else:
            cfg.created_by = request.user
            cfg.updated_by = request.user
        cfg.save()
        AuditLog.objects.create(
            user=request.user,
            action='update' if pk else 'create',
            resource=f'api_config:{cfg.marketplace}',
            ip_address=request.META.get('REMOTE_ADDR'),
        )
        messages.success(request, f'API config for {cfg.get_marketplace_display()} saved.')
        return redirect('amazon_api:list')
    return render(request, 'amazon_api/config_form.html', {
        'form': form, 'instance': instance
    })


# ── TEST CONNECTION ───────────────────────────────────────────────────────────
@login_required
@permission_required('can_configure_api')
@require_POST
def test_connection(request, pk):
    cfg = get_object_or_404(AmazonAPIConfig, pk=pk)
    from django.utils import timezone

    try:
        client = SPAPIClient(cfg)
        result = client.test_connection()
        cfg.last_test_status = 'ok'
        cfg.last_test_detail = json.dumps(result)
    except Exception as e:
        cfg.last_test_status = 'error'
        cfg.last_test_detail = str(e)

    cfg.last_tested_at = timezone.now()
    cfg.save(update_fields=['last_test_status', 'last_test_detail', 'last_tested_at'])
    return JsonResponse({
        'status': cfg.last_test_status,
        'detail': cfg.last_test_detail,
    })


# ── ANTHROPIC CONFIG (legacy route kept for backward compat) ──────────────────
@login_required
@permission_required('can_configure_api')
def anthropic_config(request):
    instance = AnthropicConfig.objects.first()
    form = AnthropicConfigForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Anthropic API key saved.')
        return redirect('amazon_api:list')
    return render(request, 'amazon_api/anthropic_form.html', {'form': form})


# ── AI PROVIDER CONFIG (Anthropic / OpenAI / Gemini) ─────────────────────────
@login_required
@permission_required('can_configure_api')
def ai_provider_config(request, provider):
    """Unified view for configuring any AI provider API key."""
    valid_providers = dict(AIProviderConfig.PROVIDER_CHOICES)
    if provider not in valid_providers:
        messages.error(request, f'Unknown AI provider: {provider}')
        return redirect('amazon_api:list')

    instance = AIProviderConfig.objects.filter(provider=provider).first()
    default_model = AIProviderConfig.DEFAULT_MODELS.get(provider, '')
    meta = AIProviderConfig.PROVIDER_META.get(provider, {})

    if request.method == 'POST':
        form = AIProviderConfigForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.provider = provider
            if not obj.model:
                obj.model = default_model
            obj.save()
            AuditLog.objects.create(
                user=request.user,
                action='update' if instance else 'create',
                resource=f'ai_provider:{provider}',
                ip_address=request.META.get('REMOTE_ADDR'),
            )
            messages.success(request, f'{valid_providers[provider]} API key saved.')
            return redirect('amazon_api:list')
    else:
        initial = {'model': default_model} if not instance else {}
        form = AIProviderConfigForm(instance=instance, initial=initial)

    return render(request, 'amazon_api/ai_provider_form.html', {
        'form':           form,
        'provider':       provider,
        'provider_label': valid_providers[provider],
        'instance':       instance,
        'meta':           meta,
        'default_model':  default_model,
    })


# ── AJAX: ADS REPORT STATUS POLLING ──────────────────────────────────────────
@login_required
def ads_report_status(request, report_id: str):
    """
    Poll a previously submitted Ads v3 report by ID.
    Returns {'status': 'pending'|'ok'|'error', 'campaigns': [...]}
    The dashboard JS should call this every 60 s when status=='pending'.
    """
    marketplace = request.GET.get('marketplace', 'usa')
    cfg = AmazonAPIConfig.objects.filter(marketplace=marketplace, is_active=True).first()
    if not cfg or not cfg.has_ads_credentials():
        return JsonResponse({'error': 'No active Ads config.'}, status=400)
    try:
        result = AdsAPIClient(cfg).check_report_status(report_id)
        return JsonResponse(result)
    except Exception as e:
        logger.error('Ads report status error: %s', e)
        return JsonResponse({'error': str(e)}, status=500)


# ── AJAX: FETCH DASHBOARD DATA ────────────────────────────────────────────────
@login_required
def fetch_dashboard_data(request):
    """
    AJAX endpoint called by the dashboard JS.
    Returns aggregated data for the requested marketplace + date range.
    """
    marketplace = request.GET.get('marketplace', 'usa')
    date_range  = request.GET.get('range', 'today')
    start_date  = request.GET.get('start')
    end_date    = request.GET.get('end')

    def _resolve_period_days():
        tz_name = settings.AMAZON_MARKETPLACES.get(marketplace, {}).get('timezone', settings.TIME_ZONE)
        today = datetime.now(tz=ZoneInfo(tz_name)).date()
        if date_range in ('today', 'yesterday'):
            return 1, today
        if date_range == 'mtd':
            return today.day, today
        if date_range == '7d':
            return 7, today
        if date_range == '30d':
            return 30, today
        if date_range == 'custom' and start_date and end_date:
            try:
                s = datetime.strptime(start_date, '%Y-%m-%d').date()
                e = datetime.strptime(end_date, '%Y-%m-%d').date()
                days = max(1, (e - s).days + 1)
                return days, e
            except Exception:
                return 1, today
        return 1, today

    def _build_targets_payload():
        import re
        period_days, anchor_day = _resolve_period_days()
        month_start = anchor_day.replace(day=1)
        days_in_month = calendar.monthrange(anchor_day.year, anchor_day.month)[1]
        rows = ProductTypePackMonthlyTarget.objects.filter(
            marketplace=marketplace,
            month=month_start,
        )

        def _norm_key(pt: str, pack: str) -> str:
            """Match key that ignores formatting differences in pack size:
            'Bath Towels' + '2'  ==  'Bath Towels' + '2-Pack' == 'Bath Towels' + 'Pack of 2'.
            """
            digits = re.search(r'\d+', str(pack or ''))
            pack_norm = digits.group(0) if digits else (str(pack or '').strip().lower())
            return f"{(pt or '').strip().lower()}::{pack_norm}"

        by_group = {}            # human-readable key  → period $ target
        by_group_match = {}      # normalized lookup key → period $ target
        for r in rows:
            monthly_target = float(r.revenue_target or 0)
            period_target = (monthly_target / days_in_month) * period_days if days_in_month else 0
            display_key = f'{r.product_type} · {r.pack_size}'
            match_key   = _norm_key(r.product_type, r.pack_size)
            by_group[display_key]      = round(by_group.get(display_key, 0) + period_target, 2)
            by_group_match[match_key]  = round(by_group_match.get(match_key, 0) + period_target, 2)
        return {
            'period_days': period_days,
            'days_in_month': days_in_month,
            'month': str(month_start),
            'by_sku': {},
            'by_group': by_group,
            'by_group_match': by_group_match,
        }

    if not request.user.can_access_marketplace(marketplace):
        return JsonResponse({'error': 'Access denied for this marketplace.'}, status=403)

    cfg = AmazonAPIConfig.objects.filter(marketplace=marketplace, is_active=True).first()
    if not cfg or not cfg.has_sp_api_credentials():
        return JsonResponse({
            'success': False,
            'error': 'No active API config for this marketplace.',
            'targets': _build_targets_payload(),
        }, status=200)

    try:
        from collections import defaultdict
        from apps.dashboard.models import Product, COGSEntry

        client = SPAPIClient(cfg)
        marketplace_tz = settings.AMAZON_MARKETPLACES.get(marketplace, {}).get('timezone', settings.TIME_ZONE)
        local_zone = ZoneInfo(marketplace_tz)

        # ── PRIMARY: FlatFileAllOrdersReport from SP-API Reports endpoint ─────
        # This is the same report you pull from Seller Central → Report Central.
        # It's the only source that returns item-price for Pending orders.
        report_result = client.fetch_orders_report_sync(
            date_range, start_date=start_date, end_date=end_date,
            max_wait_seconds=25,
        )
        report_rows   = report_result.get('rows')
        report_status = report_result.get('status', 'UNKNOWN')

        agg = {}  # sku -> {'qty', 'revenue', 'asin', 'title'}
        daily_buckets = defaultdict(lambda: {'revenue': 0.0, 'units': 0})
        unique_order_ids = set()

        report_rows_used = 0
        if report_rows:
            for row in report_rows:
                # Skip Cancelled rows at item or order level
                if (row.get('order-status') or '').strip().lower() == 'cancelled':
                    continue
                if (row.get('item-status') or '').strip().lower() == 'cancelled':
                    continue
                # Exclude Multi-Channel Fulfillment / Non-Amazon orders so the
                # totals line up with Seller Central's Sales Snapshot
                # (which counts Amazon.com sales only).
                channel = (row.get('sales-channel') or '').strip().lower()
                if channel and channel != 'amazon.com':
                    continue
                sku  = (row.get('sku')  or '').strip()
                asin = (row.get('asin') or '').strip()
                key  = sku or asin
                if not key:
                    continue
                try:
                    qty   = int(float(row.get('quantity') or 0))
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

                a = agg.setdefault(key, {'qty': 0, 'revenue': 0.0,
                                         'asin': asin, 'sku': sku,
                                         'title': (row.get('product-name') or '')})
                a['qty']     += qty
                a['revenue'] += rev

                pd_str = row.get('purchase-date') or ''
                try:
                    pd_dt  = datetime.fromisoformat(pd_str.replace('Z', '+00:00'))
                    pd_day = pd_dt.astimezone(local_zone).date().isoformat()
                except Exception:
                    pd_day = pd_str[:10]
                if pd_day:
                    daily_buckets[pd_day]['revenue'] += rev
                    daily_buckets[pd_day]['units']   += qty

                oid = (row.get('amazon-order-id') or '').strip()
                if oid:
                    unique_order_ids.add(oid)
                report_rows_used += 1

        # ── FALLBACK: live Orders API (used when report isn't ready yet) ──────
        item_fetch_success = 0
        item_fetch_errors  = 0
        all_orders = []
        if not report_rows:
            all_orders = client.get_orders_paged(
                date_range, start_date=start_date, end_date=end_date, max_pages=8
            )
            orders = [
                o for o in all_orders
                if o.get('OrderStatus') not in ('Canceled', 'Unfulfillable')
            ]
            for o in orders:
                pd_str = o.get('PurchaseDate') or ''
                try:
                    pd_dt  = datetime.fromisoformat(pd_str.replace('Z', '+00:00'))
                    pd_day = pd_dt.astimezone(local_zone).date().isoformat()
                except Exception:
                    pd_day = pd_str[:10]
                if not pd_day:
                    continue
                order_total = float((o.get('OrderTotal') or {}).get('Amount') or 0)
                shipped     = int(o.get('NumberOfItemsShipped',   0) or 0)
                unshipped   = int(o.get('NumberOfItemsUnshipped', 0) or 0)
                daily_buckets[pd_day]['revenue'] += order_total
                daily_buckets[pd_day]['units']   += shipped + unshipped
                if o.get('AmazonOrderId'):
                    unique_order_ids.add(o['AmazonOrderId'])

            def fetch_items_with_retry(order_id: str, attempts: int = 3):
                last_err = None
                for attempt in range(1, attempts + 1):
                    try:
                        return client.get_order_items(order_id)
                    except Exception as e:
                        last_err = e
                        sleep_s = (0.25 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.12)
                        time.sleep(sleep_s)
                raise last_err

            for o in orders[:120]:
                oid = o.get('AmazonOrderId')
                if not oid:
                    continue
                try:
                    items_resp = fetch_items_with_retry(oid, attempts=3)
                    items = ((items_resp or {}).get('payload', {}) or {}).get('OrderItems', [])
                    item_fetch_success += 1
                except Exception as e:
                    item_fetch_errors += 1
                    logger.warning(f'Order items fetch failed for {oid}: {e}')
                    continue
                for it in items:
                    sku  = (it.get('SellerSKU') or '').strip()
                    asin = (it.get('ASIN')      or '').strip()
                    key  = sku or asin
                    if not key:
                        continue
                    qty   = int(it.get('QuantityOrdered') or 0)
                    price = float((it.get('ItemPrice')         or {}).get('Amount') or 0)
                    promo = float((it.get('PromotionDiscount') or {}).get('Amount') or 0)
                    rev   = max(0.0, price - promo)
                    a = agg.setdefault(key, {'qty': 0, 'revenue': 0.0,
                                             'asin': asin, 'sku': sku,
                                             'title': it.get('Title', '')})
                    a['qty']     += qty
                    a['revenue'] += rev

        daily_breakdown = [
            {'date': d, 'revenue': round(v['revenue'], 2), 'units': v['units']}
            for d, v in sorted(daily_buckets.items())
        ]

        # ── Look up Product catalog + this month's COGS from DB ───────────────
        try:
            month_start = datetime.now(tz=local_zone).date().replace(day=1)
        except Exception:
            month_start = datetime.now().date().replace(day=1)

        prods_by_sku  = {}
        prods_by_asin = {}
        for p in Product.objects.filter(marketplace=marketplace):
            if p.sku:
                prods_by_sku[p.sku.upper()]  = p
            prods_by_asin[p.asin.upper()] = p

        cogs_by_sku  = {}
        cogs_by_asin = {}
        for c in COGSEntry.objects.filter(
            product__marketplace=marketplace, month=month_start,
        ).select_related('product'):
            if c.product.sku:
                cogs_by_sku[c.product.sku.upper()]  = c
            cogs_by_asin[c.product.asin.upper()] = c

        # ── Build per-SKU rows with full P&L ──────────────────────────────────
        def split_title(title: str):
            parts = [p.strip() for p in (title or '').split(' - ') if p.strip()]
            pt   = parts[0] if parts else 'Other'
            pack = parts[1] if len(parts) > 1 else '—'
            var  = parts[2] if len(parts) > 2 else ''
            return pt, pack, var

        sku_rows = []
        tot_rev = tot_units = tot_cgs = tot_amz = tot_fbf = 0.0
        for key, m in agg.items():
            sku  = m['sku']
            asin = m['asin']
            qty  = m['qty']
            rev  = m['revenue']
            product = (prods_by_sku.get(sku.upper())  or
                       prods_by_asin.get(asin.upper()))
            cogs    = (cogs_by_sku.get(sku.upper())   or
                       cogs_by_asin.get(asin.upper()))

            # Fall back to catalog price for orders where item-price was empty
            # (typical for MCF / Non-Amazon-channel orders).
            if rev == 0 and product:
                rev = float(product.sale_price or product.list_price or 0) * qty

            # CGS column = "Cogs" from the COGS upload (FOB unit cost) × qty.
            # Duties / prep / other costs (if uploaded) roll into CGS as well.
            if cogs:
                cgs_per_unit = (
                    float(cogs.unit_cost or 0)
                    + float(cogs.duties_cost or 0)
                    + float(cogs.prep_cost   or 0)
                    + float(cogs.other_cost  or 0)
                )
                fba_per_unit = float(cogs.shipping_cost or 0)   # "FBA" column
            else:
                cgs_per_unit = 0.0
                fba_per_unit = 0.0

            total_cgs = cgs_per_unit * qty
            fulfill   = fba_per_unit * qty
            amz_fee   = rev * 0.15                              # flat 15% referral
            cm        = rev - total_cgs - amz_fee - fulfill
            cm_pct       = (cm / rev * 100) if rev else 0.0
            arpu         = (rev / qty)      if qty else 0.0

            tot_rev   += rev
            tot_units += qty
            tot_cgs   += total_cgs
            tot_amz   += amz_fee
            tot_fbf   += fulfill

            if product and product.title:
                pt, pack, var = split_title(product.title)
            else:
                pt, pack, var = ((m.get('title') or '')[:30] or sku, '—', '')
            sku_rows.append({
                '_pt': pt, '_pack': pack,
                'sku': sku or asin,
                'name': var or sku or asin,
                'qty': qty,
                'revenue': round(rev, 2),
                'cgs': round(total_cgs, 2),
                'amzFee': round(amz_fee, 2),
                'fulfill': round(fulfill, 2),
                'cm': round(cm, 2),
                'cmPct': round(cm_pct, 2),
                'arpu': round(arpu, 2),
                'spSpend': 0, 'sdSpend': 0, 'sbSpend': 0, 'totalPpc': 0,
                'grossMargin': round(cm, 2),
                'gmPct': round(cm_pct, 2),
                'cpa': 0, 'tacos': 0,
            })

        # Group rows by (product_type, pack_size)
        grouped = {}
        for r in sorted(sku_rows, key=lambda x: -x['revenue']):
            gk = (r['_pt'], r['_pack'])
            if gk not in grouped:
                grouped[gk] = {
                    'group': f'{r["_pt"]}-{r["_pack"]}'.upper().replace(' ', '-')[:12].rstrip('-'),
                    'groupName': f'{r["_pt"]} · {r["_pack"]}' if r['_pack'] != '—' else r['_pt'],
                    'qty': 0, 'revenue': 0.0,
                    'cgs': 0.0, 'amzFee': 0.0, 'fulfill': 0.0, 'cm': 0.0,
                    'spSpend': 0, 'sdSpend': 0, 'sbSpend': 0, 'totalPpc': 0,
                    'cpa': 0, 'tacos': 0,
                    'variants': [],
                }
            g = grouped[gk]
            for f in ('qty', 'revenue', 'cgs', 'amzFee', 'fulfill', 'cm'):
                g[f] += r[f]
            v = {k: r[k] for k in r if not k.startswith('_')}
            g['variants'].append(v)

        skus_out = []
        for g in grouped.values():
            rev = g['revenue']
            cm  = g['cm']
            sp  = g.get('spSpend', 0)
            gm  = cm - sp
            g['cmPct']       = round((cm  / rev * 100) if rev else 0, 2)
            g['arpu']        = round((rev / g['qty']) if g['qty'] else 0, 2)
            g['grossMargin'] = round(gm, 2)
            g['gmPct']       = round((gm / rev * 100) if rev else 0, 2)
            for f in ('revenue', 'cgs', 'amzFee', 'fulfill', 'cm'):
                g[f] = round(g[f], 2)
            skus_out.append(g)
        skus_out.sort(key=lambda x: -x['revenue'])

        # If item-fetch was skipped/failed, fall back to order-level totals
        order_level_rev   = sum(v['revenue'] for v in daily_buckets.values())
        order_level_units = sum(v['units']   for v in daily_buckets.values())
        final_rev   = round(tot_rev, 2)   if tot_rev   > 0 else round(order_level_rev, 2)
        final_units = int(tot_units)      if tot_units > 0 else order_level_units
        total_cm    = round(final_rev - tot_cgs - tot_amz - tot_fbf, 2)

        data = {
            'metrics': {
                'ordered_revenue': final_rev,
                'ordered_units':   final_units,
                'total_orders':    len(unique_order_ids),
                'cgs':             round(tot_cgs, 2),
                'amz_fee':         round(tot_amz, 2),
                'fulfill':         round(tot_fbf, 2),
                'cm':              total_cm,
                'cm_pct':          round((total_cm / final_rev * 100) if final_rev else 0, 2),
                'gross_margin':    total_cm,
                'gm_pct':          round((total_cm / final_rev * 100) if final_rev else 0, 2),
                'arpu':            round((final_rev / final_units) if final_units else 0, 2),
            },
            'daily_breakdown': daily_breakdown,
            'skus':            skus_out,
            'debug': {
                'source':                   'report' if report_rows else 'orders_api',
                'report_status':            report_status,
                'report_rows_used':         report_rows_used,
                'orders_total_from_api':    len(all_orders),
                'order_items_fetch_success': item_fetch_success,
                'order_items_fetch_errors':  item_fetch_errors,
                'unique_orders':            len(unique_order_ids),
            },
        }

        # ── Ads API ───────────────────────────────────────────────────────────
        ads_payload = None
        if cfg.has_ads_credentials():
            try:
                ads_client        = AdsAPIClient(cfg)
                existing_sp_rid   = (request.GET.get('ads_sp_report_id')
                                     or request.GET.get('ads_report_id') or None)
                existing_sb_rid   = request.GET.get('ads_sb_report_id') or None
                existing_sd_rid   = request.GET.get('ads_sd_report_id') or None
                existing_prod_rid = request.GET.get('ads_product_report_id') or None

                # Submit / check SP + SB + SD campaign-level reports (combined)
                raw_all  = ads_client.get_all_campaigns_summary(
                    date_range,
                    existing_sp_id=existing_sp_rid,
                    existing_sb_id=existing_sb_rid,
                    existing_sd_id=existing_sd_rid,
                )

                # Submit / check ASIN-level report (for per-SKU spend allocation)
                raw_prod = ads_client.get_advertised_product_summary(
                    date_range, existing_report_id=existing_prod_rid)

                camp_ok = raw_all.get('status') == 'ok'
                prod_ok = raw_prod.get('status') == 'ok'

                if camp_ok:
                    total_spend = raw_all.get('total_spend', 0)
                    total_sales = round(
                        sum(float(c.get('sales7d') or c.get('sales14d')
                                  or c.get('sales') or 0)
                            for c in raw_all.get('campaigns', [])), 2)
                    acos = round((total_spend / total_sales * 100) if total_sales else 0, 2)
                    ads_payload = {
                        'status':         'ok',
                        'sp_report_id':   raw_all.get('sp_report_id'),
                        'sb_report_id':   raw_all.get('sb_report_id'),
                        'sd_report_id':   raw_all.get('sd_report_id'),
                        'report_id':      raw_all.get('sp_report_id'),   # backward compat
                        'prod_report_id': raw_prod.get('report_id'),
                        'total_spend':    total_spend,
                        'sp':             raw_all.get('sp_spend', 0),
                        'sb':             raw_all.get('sb_spend', 0),
                        'sd':             raw_all.get('sd_spend', 0),
                        'acos':           acos,
                        'campaign_count': len(raw_all.get('campaigns', [])),
                    }
                else:
                    total_spend = 0
                    ads_payload = {
                        'status':         raw_all.get('status', 'pending'),
                        'sp_report_id':   raw_all.get('sp_report_id'),
                        'sb_report_id':   raw_all.get('sb_report_id'),
                        'sd_report_id':   raw_all.get('sd_report_id'),
                        'report_id':      raw_all.get('sp_report_id'),
                        'prod_report_id': raw_prod.get('report_id'),
                    }

                # ── Merge per-ASIN spend into SKU rows ────────────────────────
                # Source priority:
                #   1. Live Ads report (prod_ok)
                #   2. DB cache (PPCProductSnapshot) — used when report still pending
                from apps.dashboard.models import PPCProductSnapshot as _PPCSnap
                asin_spend: dict = {}
                sku_spend:  dict = {}
                db_ppc_used = False
                # _camp_total: authoritative spend from campaign report (accurate,
                # includes SP + SB + SD). When live report is ready use that total;
                # when using DB cache, use stored campaign snapshots total.
                _camp_total: float = total_spend if camp_ok else 0.0

                # ── Persist live campaign data to PPCCampaignSnapshot ─────────
                if camp_ok:
                    try:
                        from apps.dashboard.models import PPCCampaignSnapshot
                        _s_d2, _e_d2, _ = SPAPIClient._resolve_local_dates(
                            date_range, marketplace=marketplace)
                        snap_date = _e_d2
                        camp_objs = []
                        for c in raw_all.get('campaigns', []):
                            cid = str(c.get('campaignId') or '')
                            if not cid:
                                continue
                            c_spend  = Decimal(str(round(float(c.get('cost') or 0), 4)))
                            c_sales  = Decimal(str(round(float(
                                c.get('sales7d') or c.get('sales14d')
                                or c.get('sales') or 0), 4)))
                            c_impr   = int(c.get('impressions') or 0)
                            c_clicks = int(c.get('clicks') or 0)
                            c_orders = int(c.get('purchases7d') or c.get('purchases14d')
                                           or c.get('purchasesClicks') or c.get('purchases') or 0)
                            c_acos   = Decimal(str(float(c_spend) / float(c_sales)
                                               if c_sales else 0))
                            c_roas   = Decimal(str(float(c_sales) / float(c_spend)
                                               if c_spend else 0))
                            c_cpc    = Decimal(str(float(c_spend) / c_clicks
                                               if c_clicks else 0))
                            c_ctr    = Decimal(str(c_clicks / c_impr if c_impr else 0))
                            camp_objs.append(PPCCampaignSnapshot(
                                marketplace   = marketplace,
                                date          = snap_date,
                                campaign_id   = cid,
                                campaign_name = (c.get('campaignName') or '')[:256],
                                campaign_type = c.get('_adType', 'sp'),
                                state         = 'enabled',
                                impressions   = c_impr,
                                clicks        = c_clicks,
                                spend         = c_spend,
                                sales_7d      = c_sales,
                                orders_7d     = c_orders,
                                units_7d      = 0,
                                acos          = c_acos,
                                roas          = c_roas,
                                cpc           = c_cpc,
                                ctr           = c_ctr,
                            ))
                        if camp_objs:
                            PPCCampaignSnapshot.objects.bulk_create(
                                camp_objs,
                                update_conflicts=True,
                                update_fields=['impressions', 'clicks', 'spend', 'sales_7d',
                                               'orders_7d', 'acos', 'roas', 'cpc', 'ctr'],
                                unique_fields=['marketplace', 'date', 'campaign_id'],
                            )
                    except Exception as snap_err:
                        logger.warning('PPCCampaignSnapshot (live) save error: %s', snap_err)

                if prod_ok:
                    for p in raw_prod.get('products', []):
                        asin = (p.get('advertisedAsin') or '').upper()
                        sku  = (p.get('advertisedSku')  or '').upper()
                        cost = float(p.get('cost') or 0)
                        if asin: asin_spend[asin] = asin_spend.get(asin, 0) + cost
                        if sku:  sku_spend[sku]   = sku_spend.get(sku,  0) + cost
                    # Scale per-ASIN proportions to match the accurate campaign total
                    prod_total_live = sum(asin_spend.values()) or 0
                    if prod_total_live and _camp_total and _camp_total > prod_total_live:
                        _scale = _camp_total / prod_total_live
                        asin_spend = {k: round(v * _scale, 2) for k, v in asin_spend.items()}
                        sku_spend  = {k: round(v * _scale, 2) for k, v in sku_spend.items()}
                else:
                    # Fall back to DB snapshots for the requested period.
                    # Two sources:
                    #   • PPCProductSnapshot  → per-ASIN proportions (but ~10% of real total)
                    #   • PPCCampaignSnapshot → accurate campaign total
                    # Strategy: use product proportions scaled to the campaign total.
                    from datetime import timedelta as _td
                    from apps.dashboard.models import PPCCampaignSnapshot as _CampSnap
                    s_d, e_d, _ = SPAPIClient._resolve_local_dates(
                        date_range, start_date=start_date, end_date=end_date,
                        marketplace=marketplace)

                    def _load_snaps(d_from, d_to):
                        """Returns (asin_spend, sku_spend, camp_total)."""
                        # Per-ASIN proportions from product snapshots
                        prod_rows = _PPCSnap.objects.filter(
                            marketplace=marketplace,
                            date__gte=d_from, date__lte=d_to,
                            campaign_type='sp',
                        ).values('asin', 'sku', 'spend')
                        a, k = {}, {}
                        for s in prod_rows:
                            asin_ = (s['asin'] or '').upper()
                            sku_  = (s['sku']  or '').upper()
                            cost_ = float(s['spend'] or 0)
                            if asin_: a[asin_] = a.get(asin_, 0) + cost_
                            if sku_:  k[sku_]  = k.get(sku_,  0) + cost_

                        # True total from campaign snapshots (much more accurate)
                        camp_total = _CampSnap.objects.filter(
                            marketplace=marketplace,
                            date__gte=d_from, date__lte=d_to,
                        ).values_list('spend', flat=True)
                        camp_total = round(sum(float(v or 0) for v in camp_total), 2)

                        # Scale per-ASIN proportions to match campaign total
                        prod_total = sum(a.values()) or 0
                        if prod_total and camp_total and camp_total > prod_total:
                            scale = camp_total / prod_total
                            a = {k_: round(v_ * scale, 2) for k_, v_ in a.items()}
                            k = {k_: round(v_ * scale, 2) for k_, v_ in k.items()}

                        return a, k, camp_total

                    asin_spend, sku_spend, camp_total = _load_snaps(s_d, e_d)
                    ppc_note = None

                    # If the range is exactly today and we got nothing,
                    # fall back to yesterday as an estimate
                    import datetime as _dt
                    _today = _dt.date.today()
                    if not asin_spend and s_d == _today and e_d == _today:
                        _yest = _today - _td(days=1)
                        asin_spend, sku_spend, camp_total = _load_snaps(_yest, _yest)
                        if asin_spend or camp_total:
                            ppc_note = f'Estimated from {_yest:%b %d} — today\'s report processing'

                    if asin_spend or camp_total:
                        db_ppc_used = True
                        _live_camp_total = _camp_total   # save live value before DB overwrite
                        _camp_total      = camp_total    # use DB total for scaling

                        # Helper: SP/SB/SD breakdown from DB snapshots
                        def _db_type_spend(ct):
                            return round(sum(float(v or 0) for v in _CampSnap.objects.filter(
                                marketplace=marketplace, date__gte=s_d, date__lte=e_d,
                                campaign_type=ct,
                            ).values_list('spend', flat=True)), 2)

                        # Override ads_payload with DB totals when DB is more authoritative:
                        #   • Past dates (yesterday/mtd/etc): DB always wins — live API
                        #     returns partial/stale data for completed days.
                        #   • Today: DB wins when it has MORE spend than the partial live
                        #     result (e.g., DB has SP+SB+SD from a backfill, live only
                        #     returned SP because SB/SD hadn't completed in the 30s window).
                        _db_more_complete = camp_total > _live_camp_total
                        if camp_total and ads_payload and (
                            date_range != 'today' or _db_more_complete
                        ):
                            ads_payload['total_spend'] = float(camp_total)
                            ads_payload['sp']          = _db_type_spend('sp')
                            ads_payload['sb']          = _db_type_spend('sb')
                            ads_payload['sd']          = _db_type_spend('sd')
                            ads_payload['source']      = 'db_cache'
                            ads_payload['status']      = 'ok'

                        # Use campaign total as the authoritative spend figure
                        total_db_spend = camp_total or round(sum(asin_spend.values()), 2)
                        if not camp_ok and total_db_spend:
                            ads_payload = {
                                'status':         'ok',
                                'source':         'db_cache',
                                'sp_report_id':   raw_all.get('sp_report_id'),
                                'sb_report_id':   raw_all.get('sb_report_id'),
                                'sd_report_id':   raw_all.get('sd_report_id'),
                                'report_id':      raw_all.get('sp_report_id'),
                                'prod_report_id': raw_prod.get('report_id'),
                                'total_spend':    total_db_spend,
                                'sp':             _db_type_spend('sp'),
                                'sb':             _db_type_spend('sb'),
                                'sd':             _db_type_spend('sd'),
                                'acos':           0,
                                'note':           ppc_note,
                            }

                # Patch variant and group rows
                if asin_spend or sku_spend:
                    for grp in data.get('skus', []):
                        grp_sp = 0.0
                        for variant in grp.get('variants', []):
                            v_sku  = (variant.get('sku') or '').upper()
                            v_asin = (variant.get('asin') or '').upper()
                            sp = sku_spend.get(v_sku) or asin_spend.get(v_asin) or 0.0
                            v_cm  = variant.get('cm', 0)
                            v_rev = variant.get('revenue', 0)
                            variant['spSpend']     = round(sp, 2)
                            variant['totalPpc']    = round(sp, 2)
                            variant['tacos']       = round((sp / v_rev * 100) if v_rev else 0, 2)
                            # ── GM = CM − PPC ──────────────────────────────
                            variant['grossMargin'] = round(v_cm - sp, 2)
                            variant['gmPct']       = round(((v_cm - sp) / v_rev * 100) if v_rev else 0, 2)
                            grp_sp += sp
                        grp['spSpend']  = round(grp_sp, 2)
                        grp['totalPpc'] = round(grp_sp, 2)
                        grp_rev = grp.get('revenue', 0)
                        grp_cm  = grp.get('cm', 0)
                        grp['tacos']       = round((grp_sp / grp_rev * 100) if grp_rev else 0, 2)
                        # ── GM = CM − PPC at group level ───────────────────
                        grp['grossMargin'] = round(grp_cm - grp_sp, 2)
                        grp['gmPct']       = round(((grp_cm - grp_sp) / grp_rev * 100) if grp_rev else 0, 2)

                    # ── Patch overall metrics GM = CM − total PPC ─────────
                    # Prefer campaign-level total (accurate); fall back to scaled asin sum
                    total_ppc = _camp_total if _camp_total else round(sum(asin_spend.values()), 2)
                    total_cm_val  = data['metrics'].get('cm', 0)
                    total_rev_val = data['metrics'].get('ordered_revenue', 0)
                    data['metrics']['gross_margin'] = round(total_cm_val - total_ppc, 2)
                    data['metrics']['gm_pct'] = round(
                        ((total_cm_val - total_ppc) / total_rev_val * 100) if total_rev_val else 0, 2)
                    data['metrics']['ppc_spend'] = total_ppc

                    # Persist live data to PPCProductSnapshot (only when fresh from API)
                    if prod_ok:
                        try:
                            from apps.dashboard.models import PPCProductSnapshot
                            _s_d2, _e_d2, _ = SPAPIClient._resolve_local_dates(
                                date_range, marketplace=marketplace)
                            snap_date = _e_d2
                            objs = []
                            for p in raw_prod.get('products', []):
                                asin = (p.get('advertisedAsin') or '').upper()
                                if not asin:
                                    continue
                                objs.append(PPCProductSnapshot(
                                    marketplace=marketplace,
                                    date=snap_date,
                                    asin=asin,
                                    sku=(p.get('advertisedSku') or '').upper(),
                                    campaign_type='sp',
                                    impressions=int(p.get('impressions') or 0),
                                    clicks=int(p.get('clicks') or 0),
                                    spend=round(float(p.get('cost') or 0), 2),
                                    sales_7d=round(float(p.get('sales7d') or 0), 2),
                                    orders_7d=int(p.get('purchases7d') or 0),
                                    units_7d=int(p.get('unitsSoldClicks7d') or 0),
                                ))
                            if objs:
                                PPCProductSnapshot.objects.bulk_create(
                                    objs,
                                    update_conflicts=True,
                                    update_fields=['impressions', 'clicks', 'spend', 'sales_7d',
                                                   'orders_7d', 'units_7d'],
                                    unique_fields=['marketplace', 'date', 'asin', 'campaign_type'],
                                )
                        except Exception as snap_err:
                            logger.warning('PPCProductSnapshot save error: %s', snap_err)

            except Exception as ads_err:
                logger.warning('Ads API fetch error: %s', ads_err)
                ads_payload = {'status': 'error', 'error': str(ads_err)}

        return JsonResponse({
            'success': True,
            'sales':   data,
            'ads':     ads_payload,
            'targets': _build_targets_payload(),
        })
    except Exception as e:
        logger.error('Dashboard data fetch error: %s', e, exc_info=True)
        return JsonResponse({'error': str(e), 'targets': _build_targets_payload()}, status=500)
