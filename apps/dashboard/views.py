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

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.http import StreamingHttpResponse, JsonResponse, HttpResponse
from django.conf import settings
from django.db.models import Sum, Avg

from apps.core.decorators import permission_required
from apps.users.models import AuditLog
from apps.amazon_api.models import AmazonAPIConfig, AnthropicConfig
from .models import Product, COGSEntry, MonthlyTarget, DailyMetric, ProductTypePackMonthlyTarget
from .forms import COGSBulkUploadForm, COGSEntryForm, MonthlyTargetForm, ProductForm

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
    marketplace = request.GET.get('mp', 'usa')
    period      = request.GET.get('period', '30d')

    if not request.user.can_access_marketplace(marketplace):
        marketplace = (request.user.allowed_marketplaces or ['usa'])[0]

    today = date.today()
    days_map = {'7d': 7, '30d': 30, '90d': 90, 'ytd': (today - today.replace(month=1, day=1)).days}
    days  = days_map.get(period, 30)
    start = today - timedelta(days=days) if period != 'ytd' else today.replace(month=1, day=1)

    metrics = DailyMetric.objects.filter(
        marketplace=marketplace, date__gte=start, date__lte=today
    ).order_by('date')

    totals = metrics.aggregate(
        total_revenue=Sum('revenue'), total_units=Sum('units'),
        total_orders=Sum('orders'),  total_ppc=Sum('ppc_spend'),
        avg_tacos=Avg('tacos'),      avg_acos=Avg('acos'),
        avg_gm_pct=Avg('gm_pct'),
    )

    # ── Build chart data (real DB or demo fallback) ──────────────────────────
    if metrics.exists():
        chart_data = json.dumps({
            'dates':   [str(m.date) for m in metrics],
            'revenue': [float(m.revenue) for m in metrics],
            'units':   [m.units for m in metrics],
            'ppc':     [float(m.ppc_spend) for m in metrics],
            'tacos':   [float(m.tacos) * 100 for m in metrics],
            'gm_pct':  [float(m.gm_pct) * 100 for m in metrics],
        })
        has_data = True
    else:
        # ── Generate demo data so the page is always useful ──────────────────
        import random, math
        random.seed(42)
        demo_dates, demo_rev, demo_units, demo_ppc, demo_tacos, demo_gm = [], [], [], [], [], []
        base_rev = {'usa':22000,'ca':5500,'uk':4800,'de':3200,'ae':2100,'sa':1800}.get(marketplace, 15000)
        for i in range(days, -1, -1):
            d = today - timedelta(days=i)
            # Weekend bump + gentle uptrend
            wf = 1.12 if d.weekday() >= 5 else 1.0
            trend = 1 + (days - i) * 0.002
            noise = 0.85 + random.random() * 0.32
            rev = round(base_rev / 30 * wf * trend * noise, 0)
            units = int(rev / 24.5)
            ppc = round(rev * 0.162, 0)
            tacos = round(ppc / rev * 100 if rev else 16.0, 1)
            gm = round(rev * 0.135, 1)
            demo_dates.append(str(d))
            demo_rev.append(rev)
            demo_units.append(units)
            demo_ppc.append(ppc)
            demo_tacos.append(tacos)
            demo_gm.append(gm)

        chart_data = json.dumps({
            'dates': demo_dates, 'revenue': demo_rev, 'units': demo_units,
            'ppc': demo_ppc, 'tacos': demo_tacos, 'gm_pct': demo_gm,
        })
        # Build demo totals for KPI strip
        totals = {
            'total_revenue': sum(demo_rev),
            'total_units':   sum(demo_units),
            'total_orders':  int(sum(demo_units) * 0.93),
            'total_ppc':     sum(demo_ppc),
            'avg_tacos':     round(sum(demo_tacos)/len(demo_tacos), 1) if demo_tacos else 0,
            'avg_acos':      11.4,
            'avg_gm_pct':    13.5,
        }
        has_data = False  # flag for template to show demo notice

    target = MonthlyTarget.objects.filter(
        marketplace=marketplace, month=today.replace(day=1)
    ).first()

    ctx = {
        'metrics':   metrics,
        'totals':    totals,
        'chart_data': chart_data,
        'has_data':  has_data,
        'marketplace': marketplace,
        'period':    period,
        'start':     start,
        'today':     today,
        'target':    target,
        'allowed_marketplaces': request.user.allowed_marketplaces or list(settings.AMAZON_MARKETPLACES.keys()),
        'show_financials': request.user.has_perm_flag('can_view_financials'),
        'show_ppc':        request.user.has_perm_flag('can_view_ppc'),
    }
    return render(request, 'dashboard/historical.html', ctx)


@login_required
@permission_required('can_manage_cogs')
def cogs(request):
    upload_form   = COGSBulkUploadForm()
    manual_form   = COGSEntryForm()
    upload_result = None

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'upload_csv':
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

        elif action == 'manual_entry':
            manual_form = COGSEntryForm(request.POST)
            if manual_form.is_valid():
                e = manual_form.save(commit=False)
                e.uploaded_by = request.user
                e.save()
                messages.success(request, 'COGS entry saved.')
                return redirect('dashboard:cogs')

    recent = COGSEntry.objects.select_related('product').order_by('-month', 'product__asin')[:100]
    ctx = {'upload_form': upload_form, 'manual_form': manual_form,
           'upload_result': upload_result, 'recent': recent}
    return render(request, 'dashboard/cogs.html', ctx)


def _process_cogs_csv(f, overwrite=False, user=None):
    result = {'created': 0, 'updated': 0, 'errors': []}
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
        except Exception as e:
            result['errors'].append(f'Row {i}: {e}')
    return result


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

    anthropic_cfg = AnthropicConfig.get_active()
    api_key = (anthropic_cfg.api_key if anthropic_cfg else None) or settings.ANTHROPIC_API_KEY

    if not api_key:
        def _err():
            yield 'data: {"error": "Anthropic API key not configured."}\n\n'
        return StreamingHttpResponse(_err(), content_type='text/event-stream')

    model = getattr(anthropic_cfg, 'model', settings.ANTHROPIC_MODEL) if anthropic_cfg else settings.ANTHROPIC_MODEL

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
        w.writerow([m.date,m.marketplace,m.revenue,m.units,m.orders,m.sessions,
                    f'{float(m.conversion_rate)*100:.2f}',m.ppc_spend,
                    f'{float(m.tacos)*100:.2f}',f'{float(m.acos)*100:.2f}',
                    m.gross_margin,f'{float(m.gm_pct)*100:.2f}',
                    m.contribution_margin,f'{float(m.cm_pct)*100:.2f}'])
    return response
