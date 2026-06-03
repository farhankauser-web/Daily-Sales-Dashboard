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


# ── SB/SD SPEND ALLOCATION BY CAMPAIGN NAME ──────────────────────────────────
# Maps the leading code in campaign names to (product_type, pack_size) exactly
# as they appear in Product.title (split by " - ").
_CAMP_PREFIX_GROUP = {
    '8BTH':    ('Bath Towels', '8-Pack'),
    '4BTH':    ('Bath Towels', '4-Pack'),
    '2BTH':    ('Bath Towels', '2-Pack'),
    '2BS':     ('Bath Sheet',  '2-Pack'),
    '1BS':     ('Bath Sheet',  '1-Pack'),
    '2BM':     ('Bath Mat',    '2-Pack'),
    '6HNDTWL': ('Hand Towel',  '6-Pack'),
    '6KTH':    ('Kitchen Towel', '6-Pack'),
    '12WCPK':  ('Wash Cloth',  '12-Pack'),
    '4WCPK':   ('Wash Cloth',  '4-Pack'),
}


def _compute_sb_sd_by_group(marketplace, s_d, e_d, sb_sd_campaigns=None):
    """
    Match each SB/SD campaign name prefix to a product group and return
    the spend broken down by ad type per group.

    sb_sd_campaigns: list of campaign dicts from live API (with '_adType' key).
                     When None, reads from PPCCampaignSnapshot for [s_d, e_d].

    Returns {(product_type, pack_size): {'sb': float, 'sd': float}}
    These amounts are applied at the GROUP ROW level (sbSpend / sdSpend columns),
    NOT added to the per-SKU SP spend — so SP, SB, SD columns stay separate.
    """
    from apps.dashboard.models import PPCCampaignSnapshot as _CS
    from django.db.models import Sum as _Sum

    def _prefix(name):
        return (name or '').split('-')[0].strip().upper()

    # Collect SB/SD spend keyed by (campaign_name, ad_type)
    if sb_sd_campaigns is not None:
        camp_rows = {}
        for c in (sb_sd_campaigns or []):
            ad_type = c.get('_adType', '')
            if ad_type not in ('sb', 'sd'):
                continue
            name = (c.get('campaignName') or '')
            key = (name, ad_type)
            camp_rows[key] = camp_rows.get(key, 0) + float(c.get('cost') or 0)
    else:
        qs = (
            _CS.objects
            .filter(marketplace=marketplace, date__gte=s_d, date__lte=e_d,
                    campaign_type__in=['sb', 'sd'])
            .values('campaign_name', 'campaign_type')
            .annotate(spend=_Sum('spend'))
        )
        camp_rows = {
            (r['campaign_name'], r['campaign_type']): float(r['spend'] or 0)
            for r in qs
        }

    # Aggregate by product group and ad type
    result = {}
    for (name, ad_type), spend in camp_rows.items():
        if spend <= 0:
            continue
        group = _CAMP_PREFIX_GROUP.get(_prefix(name))
        if group:
            if group not in result:
                result[group] = {'sb': 0.0, 'sd': 0.0}
            result[group][ad_type] = result[group].get(ad_type, 0.0) + spend

    return result


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


def _build_cached_skus(marketplace: str, date, sp_total: float, sb_total: float, sd_total: float) -> list:
    """
    Build the 'skus' list for the cache response using DailySkuSnapshot + PPC snapshots.
    Returns the same group-level JSON structure that fetch_dashboard_data produces.
    """
    try:
        from apps.dashboard.models import DailySkuSnapshot as _SkuSnap, Product as _Prod
        from apps.dashboard.models import PPCProductSnapshot as _ProdSnap, PPCCampaignSnapshot as _CampSnap
        from django.db.models import Sum as _Sum
        import re as _re

        def _split(title):
            parts = [p.strip() for p in (title or '').split(' - ') if p.strip()]
            return (parts[0] if parts else ''), (parts[1] if len(parts) > 1 else '—'), (parts[2] if len(parts) > 2 else '')

        # Load product catalog
        prod_by_sku, prod_by_asin = {}, {}
        for p in _Prod.objects.filter(marketplace=marketplace):
            if p.sku:  prod_by_sku[p.sku.upper()]   = p
            if p.asin: prod_by_asin[p.asin.upper()]  = p

        # Load SP product spend (from PPCProductSnapshot)
        sp_prod_total = 0.0
        sku_sp_spend, asin_sp_spend = {}, {}
        for s in _ProdSnap.objects.filter(marketplace=marketplace, date=date, campaign_type='sp').values('sku', 'asin', 'spend'):
            sku_  = (s['sku']  or '').upper()
            asin_ = (s['asin'] or '').upper()
            cost  = float(s['spend'] or 0)
            if sku_:  sku_sp_spend[sku_]   = sku_sp_spend.get(sku_,   0) + cost
            if asin_: asin_sp_spend[asin_] = asin_sp_spend.get(asin_, 0) + cost
            sp_prod_total += cost
        # Scale SP product proportions to SP campaign total
        if sp_prod_total and sp_total and sp_total > sp_prod_total:
            _sc = sp_total / sp_prod_total
            sku_sp_spend  = {k: round(v * _sc, 2) for k, v in sku_sp_spend.items()}
            asin_sp_spend = {k: round(v * _sc, 2) for k, v in asin_sp_spend.items()}

        # SB/SD by product group
        _sb_sd_grp = _compute_sb_sd_by_group(marketplace, date, date)

        # Aggregate SKU snapshots into product groups
        grouped = {}
        for snap in _SkuSnap.objects.filter(marketplace=marketplace, date=date):
            sku  = snap.sku.upper()
            asin = (snap.asin or '').upper()
            prod = prod_by_sku.get(sku) or prod_by_asin.get(asin)
            if prod and prod.title:
                pt, pack, var = _split(prod.title)
            else:
                pt, pack, var = sku[:30], '—', ''

            gk = (pt, pack)
            if gk not in grouped:
                grouped[gk] = {
                    'group':     f'{pt}-{pack}'.upper().replace(' ', '-')[:12].rstrip('-'),
                    'groupName': f'{pt} · {pack}' if pack != '—' else pt,
                    '_pt': pt, '_pack': pack,
                    'qty': 0, 'revenue': 0.0, 'cgs': 0.0, 'amzFee': 0.0,
                    'fulfill': 0.0, 'cm': 0.0, 'spSpend': 0.0,
                    'variants': [],
                }
            g = grouped[gk]
            _rev = float(snap.revenue)
            _cm  = float(snap.cm)
            _sp  = sku_sp_spend.get(sku) or asin_sp_spend.get(asin) or 0.0
            g['qty']     += snap.qty
            g['revenue'] += _rev
            g['cgs']     += float(snap.cgs)
            g['amzFee']  += float(snap.amz_fee)
            g['fulfill'] += float(snap.fulfill)
            g['cm']      += _cm
            g['spSpend'] += _sp
            g['variants'].append({
                'sku': sku, 'asin': asin,
                'name': var or sku,
                'qty': snap.qty, 'revenue': round(_rev, 2),
                'cgs': round(float(snap.cgs), 2),
                'amzFee': round(float(snap.amz_fee), 2),
                'fulfill': round(float(snap.fulfill), 2),
                'cm': round(_cm, 2),
                'cmPct': round((_cm / _rev * 100) if _rev else 0, 2),
                'arpu': round(_rev / snap.qty, 2) if snap.qty else 0,
                'spSpend': round(_sp, 2), 'sdSpend': 0, 'sbSpend': 0,
                'totalPpc': round(_sp, 2),
                'grossMargin': round(_cm - _sp, 2),
                'gmPct': round(((_cm - _sp) / _rev * 100) if _rev else 0, 2),
                'tacos': round((_sp / _rev * 100) if _rev else 0, 2),
                'cpa': 0,
            })

        out = []
        for (pt, pack), g in sorted(grouped.items(), key=lambda x: -x[1]['revenue']):
            rev = g['revenue']
            cm  = g['cm']
            sp  = g['spSpend']
            _gk  = (pt, pack)
            grp_sb = _sb_sd_grp.get(_gk, {}).get('sb', 0.0)
            grp_sd = _sb_sd_grp.get(_gk, {}).get('sd', 0.0)
            total  = round(sp + grp_sb + grp_sd, 2)
            gm     = round(cm - total, 2)
            out.append({
                'group':       g['group'],
                'groupName':   g['groupName'],
                '_pt': pt, '_pack': pack,
                'qty':         g['qty'],
                'revenue':     round(rev, 2),
                'cgs':         round(g['cgs'], 2),
                'amzFee':      round(g['amzFee'], 2),
                'fulfill':     round(g['fulfill'], 2),
                'cm':          round(cm, 2),
                'cmPct':       round((cm / rev * 100) if rev else 0, 2),
                'arpu':        round(rev / g['qty'], 2) if g['qty'] else 0,
                'spSpend':     round(sp, 2),
                'sdSpend':     round(grp_sd, 2),
                'sbSpend':     round(grp_sb, 2),
                'totalPpc':    total,
                'grossMargin': gm,
                'gmPct':       round((gm / rev * 100) if rev else 0, 2),
                'tacos':       round((total / rev * 100) if rev else 0, 2),
                'cpa':         0,
                'variants':    g['variants'],
            })
        return out
    except Exception as _err:
        logger.warning('_build_cached_skus failed: %s', _err)
        return []


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

    # ── CACHE-FIRST: for "today" serve DailyMetric cached by the hourly cron ─
    # The cron (sync_daily_metrics --include-today) stores today's data every
    # hour so the dashboard loads instantly without waiting for Amazon.
    # Pass ?force_live=1 (Refresh button) to bypass the cache.
    force_live = request.GET.get('force_live') == '1'
    if date_range == 'today' and not force_live:
        try:
            from django.utils import timezone as _tz
            from apps.dashboard.models import DailyMetric as _DM, PPCCampaignSnapshot as _CS
            from django.db.models import Sum as _Sum

            _mp_tz  = settings.AMAZON_MARKETPLACES.get(marketplace, {}).get('timezone', settings.TIME_ZONE)
            _today  = datetime.now(tz=ZoneInfo(_mp_tz)).date()
            _dm     = _DM.objects.filter(marketplace=marketplace, date=_today).first()

            if _dm and _dm.revenue > 0 and _dm.synced_at:
                _age_sec = (_tz.now() - _dm.synced_at).total_seconds()
                if _age_sec < 7200:          # cache valid for up to 2 hours
                    # PPC breakdown by type
                    _camp = dict(
                        _CS.objects
                        .filter(marketplace=marketplace, date=_today)
                        .values('campaign_type')
                        .annotate(t=_Sum('spend'))
                        .values_list('campaign_type', 't')
                    )
                    _sp   = float(_camp.get('sp', 0) or 0)
                    _sb   = float(_camp.get('sb', 0) or 0)
                    _sd   = float(_camp.get('sd', 0) or 0)
                    _ppc  = float(_dm.ppc_spend or 0) or (_sp + _sb + _sd)
                    _rev  = float(_dm.revenue or 0)
                    _cm   = float(_dm.contribution_margin or 0)
                    _gm   = round(_cm - _ppc, 2)   # always GM = CM − PPC
                    _cached_at = _dm.synced_at.astimezone(
                        ZoneInfo(_mp_tz)).strftime('%-I:%M %p %Z')

                    # Last 7 days for the Revenue Trend chart
                    _hist = _DM.objects.filter(
                        marketplace=marketplace,
                        date__gte=_today - timedelta(days=6),
                        date__lte=_today,
                    ).order_by('date')
                    _daily_breakdown = [
                        {'date': str(r.date), 'revenue': float(r.revenue), 'units': r.units}
                        for r in _hist
                    ]

                    return JsonResponse({
                        'success':  True,
                        'cached':   True,
                        'cached_at': _cached_at,
                        'sales': {
                            'metrics': {
                                'ordered_revenue': _rev,
                                'ordered_units':   _dm.units,
                                'total_orders':    _dm.orders,
                                'cgs':      float(_dm.cgs or 0),
                                'amz_fee':  float(_dm.amazon_fee or 0),
                                'fulfill':  float(_dm.fba_fee or 0),
                                'cm':       _cm,
                                'cm_pct':   round(float(_dm.cm_pct or 0) * 100, 2),
                                'ppc_spend': _ppc,
                                'gross_margin': _gm,
                                'gm_pct':   round((_gm / _rev * 100) if _rev else 0, 2),
                                'arpu':     round(_rev / _dm.units, 2) if _dm.units else 0,
                            },
                            'daily_breakdown': _daily_breakdown,
                            'skus':  _build_cached_skus(marketplace, _today, _sp, _sb, _sd),
                            'debug': {'source': 'dailymetric_cache', 'cached_at': str(_dm.synced_at)},
                        },
                        'ads': {
                            'status':      'ok',
                            'source':      'db_cache',
                            'total_spend': _ppc,
                            'sp':          _sp,
                            'sb':          _sb,
                            'sd':          _sd,
                            'acos':        round(float(_dm.acos or 0) * 100, 2),
                        },
                        'targets': _build_targets_payload(),
                    })
        except Exception as _cache_err:
            logger.warning('Cache-first path failed, falling through to live: %s', _cache_err)

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
                    '_pt': r['_pt'],    # kept for SB/SD group lookup — stripped before JSON
                    '_pack': r['_pack'],
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
                # SB/SD spend per product group — applied at GROUP ROW level so that
                # SP / SB / SD columns stay separate in the table.
                _sb_sd_by_group: dict = {}

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
                    # Scale per-ASIN proportions to the SP-only campaign total.
                    _sp_only_total = float(raw_all.get('sp_spend', 0) or 0)
                    prod_total_live = sum(asin_spend.values()) or 0
                    _scale_base = _sp_only_total if _sp_only_total else _camp_total
                    if prod_total_live and _scale_base and _scale_base > prod_total_live:
                        _scale = _scale_base / prod_total_live
                        asin_spend = {k: round(v * _scale, 2) for k, v in asin_spend.items()}
                        sku_spend  = {k: round(v * _scale, 2) for k, v in sku_spend.items()}
                    # Compute SB/SD by product group (applied at group row level below)
                    _sb_sd_by_group = _compute_sb_sd_by_group(
                        marketplace, None, None,
                        sb_sd_campaigns=raw_all.get('campaigns', []),
                    )
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
                        """Returns (asin_spend, sku_spend, camp_total).
                        camp_total = SP+SB+SD (used for the overall PPC metric).
                        Product proportions are scaled to SP-only campaign total so
                        that SB/SD spend doesn't inflate per-product SP figures.
                        """
                        # Per-ASIN proportions from product snapshots (SP-only data)
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

                        # SP-only campaign total — used for product proportion scaling.
                        # Must NOT use SP+SB+SD here: product snapshots are SP-only so
                        # scaling by the full total would inflate every SKU's SP spend.
                        sp_camp_total = round(sum(
                            float(v or 0) for v in _CampSnap.objects.filter(
                                marketplace=marketplace,
                                date__gte=d_from, date__lte=d_to,
                                campaign_type='sp',
                            ).values_list('spend', flat=True)
                        ), 2)

                        # Full SP+SB+SD total — returned so the KPI tile is accurate
                        camp_total = round(sum(
                            float(v or 0) for v in _CampSnap.objects.filter(
                                marketplace=marketplace,
                                date__gte=d_from, date__lte=d_to,
                            ).values_list('spend', flat=True)
                        ), 2)

                        # Scale SP product proportions to SP campaign total only
                        prod_total = sum(a.values()) or 0
                        if prod_total and sp_camp_total and sp_camp_total > prod_total:
                            scale = sp_camp_total / prod_total
                            a = {k_: round(v_ * scale, 2) for k_, v_ in a.items()}
                            k = {k_: round(v_ * scale, 2) for k_, v_ in k.items()}

                        return a, k, camp_total

                    asin_spend, sku_spend, camp_total = _load_snaps(s_d, e_d)
                    ppc_note = None
                    _eff_s, _eff_e = s_d, e_d   # track effective date range for SB/SD query

                    # If the range is exactly today and we got nothing,
                    # fall back to yesterday as an estimate
                    import datetime as _dt
                    _today = _dt.date.today()
                    if not asin_spend and s_d == _today and e_d == _today:
                        _yest = _today - _td(days=1)
                        asin_spend, sku_spend, camp_total = _load_snaps(_yest, _yest)
                        _eff_s, _eff_e = _yest, _yest
                        if asin_spend or camp_total:
                            ppc_note = f'Estimated from {_yest:%b %d} — today\'s report processing'

                    # Compute SB/SD by product group (applied at group row level below)
                    _sb_sd_by_group = _compute_sb_sd_by_group(marketplace, _eff_s, _eff_e)

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
                if asin_spend or sku_spend or _sb_sd_by_group:
                    for grp in data.get('skus', []):
                        grp_sp = 0.0
                        for variant in grp.get('variants', []):
                            v_sku  = (variant.get('sku') or '').upper()
                            v_asin = (variant.get('asin') or '').upper()
                            # SP only — SB/SD handled at group level
                            sp = sku_spend.get(v_sku) or asin_spend.get(v_asin) or 0.0
                            v_cm  = variant.get('cm', 0)
                            v_rev = variant.get('revenue', 0)
                            variant['spSpend']     = round(sp, 2)
                            variant['sdSpend']     = 0
                            variant['sbSpend']     = 0
                            variant['totalPpc']    = round(sp, 2)
                            variant['tacos']       = round((sp / v_rev * 100) if v_rev else 0, 2)
                            variant['grossMargin'] = round(v_cm - sp, 2)
                            variant['gmPct']       = round(((v_cm - sp) / v_rev * 100) if v_rev else 0, 2)
                            grp_sp += sp

                        # SB/SD applied at group row level — separate columns
                        _gk     = (grp.get('_pt', ''), grp.get('_pack', ''))
                        _grp_sb = _sb_sd_by_group.get(_gk, {}).get('sb', 0.0)
                        _grp_sd = _sb_sd_by_group.get(_gk, {}).get('sd', 0.0)
                        _grp_total = grp_sp + _grp_sb + _grp_sd
                        grp_rev = grp.get('revenue', 0)
                        grp_cm  = grp.get('cm', 0)
                        grp['spSpend']     = round(grp_sp, 2)
                        grp['sbSpend']     = round(_grp_sb, 2)
                        grp['sdSpend']     = round(_grp_sd, 2)
                        grp['totalPpc']    = round(_grp_total, 2)
                        grp['tacos']       = round((_grp_total / grp_rev * 100) if grp_rev else 0, 2)
                        grp['grossMargin'] = round(grp_cm - _grp_total, 2)
                        grp['gmPct']       = round(((grp_cm - _grp_total) / grp_rev * 100) if grp_rev else 0, 2)

                    # ── Patch overall metrics — use authoritative campaign total ──
                    # _camp_total = full SP+SB+SD from live API or DB snapshots,
                    # so this matches the top KPI tile exactly.
                    total_ppc = _camp_total if _camp_total else round(sum(asin_spend.values()), 2)
                    total_cm_val  = data['metrics'].get('cm', 0)
                    total_rev_val = data['metrics'].get('ordered_revenue', 0)
                    data['metrics']['gross_margin'] = round(total_cm_val - total_ppc, 2)
                    data['metrics']['gm_pct'] = round(
                        ((total_cm_val - total_ppc) / total_rev_val * 100) if total_rev_val else 0, 2)
                    data['metrics']['ppc_spend'] = total_ppc

                    # Persist live data to PPCProductSnapshot (only when fresh from API).
                    # Pre-aggregate by ASIN across campaigns before bulk_create —
                    # the same ASIN can appear in multiple rows (one per campaign) and
                    # update_conflicts replaces rather than sums, so row-by-row would
                    # keep only the last campaign's spend.
                    if prod_ok:
                        try:
                            from apps.dashboard.models import PPCProductSnapshot
                            _s_d2, _e_d2, _ = SPAPIClient._resolve_local_dates(
                                date_range, marketplace=marketplace)
                            snap_date = _e_d2
                            agg = {}
                            for p in raw_prod.get('products', []):
                                asin = (p.get('advertisedAsin') or '').upper()
                                if not asin:
                                    continue
                                if asin not in agg:
                                    agg[asin] = {
                                        'sku':         (p.get('advertisedSku') or '').upper(),
                                        'impressions': 0, 'clicks': 0,
                                        'spend':       0.0, 'sales_7d': 0.0,
                                        'orders_7d':   0,   'units_7d': 0,
                                    }
                                a = agg[asin]
                                if not a['sku']:
                                    a['sku'] = (p.get('advertisedSku') or '').upper()
                                a['impressions'] += int(p.get('impressions') or 0)
                                a['clicks']      += int(p.get('clicks') or 0)
                                a['spend']       += float(p.get('cost') or 0)
                                a['sales_7d']    += float(p.get('sales7d') or 0)
                                a['orders_7d']   += int(p.get('purchases7d') or 0)
                                a['units_7d']    += int(p.get('unitsSoldClicks7d') or 0)
                            objs = [
                                PPCProductSnapshot(
                                    marketplace   = marketplace,
                                    date          = snap_date,
                                    asin          = asin,
                                    sku           = a['sku'],
                                    campaign_type = 'sp',
                                    impressions   = a['impressions'],
                                    clicks        = a['clicks'],
                                    spend         = round(a['spend'], 2),
                                    sales_7d      = round(a['sales_7d'], 2),
                                    orders_7d     = a['orders_7d'],
                                    units_7d      = a['units_7d'],
                                )
                                for asin, a in agg.items()
                            ]
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
