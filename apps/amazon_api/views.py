"""
apps/amazon_api/views.py — API Configuration Management + SP-API service
"""
import json
import time
import logging
from datetime import datetime, timedelta
import calendar
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.conf import settings

from .models import AmazonAPIConfig, AnthropicConfig, APISyncLog
from .forms import AmazonAPIConfigForm, AnthropicConfigForm
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
    anthropic = AnthropicConfig.objects.first()
    return render(request, 'amazon_api/config_list.html', {
        'configs': configs,
        'anthropic': anthropic,
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


# ── ANTHROPIC CONFIG ──────────────────────────────────────────────────────────
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
        period_days, anchor_day = _resolve_period_days()
        month_start = anchor_day.replace(day=1)
        days_in_month = calendar.monthrange(anchor_day.year, anchor_day.month)[1]
        rows = ProductTypePackMonthlyTarget.objects.filter(
            marketplace=marketplace,
            month=month_start,
        )

        by_group = {}
        for r in rows:
            monthly_target = float(r.revenue_target or 0)
            period_target = (monthly_target / days_in_month) * period_days if days_in_month else 0
            group_key = f'{r.product_type} · {r.pack_size}'
            by_group[group_key] = round(by_group.get(group_key, 0) + period_target, 2)
        return {
            'period_days': period_days,
            'days_in_month': days_in_month,
            'month': str(month_start),
            'by_sku': {},
            'by_group': by_group,
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
        client = SPAPIClient(cfg)
        data   = client.get_sales_data(date_range, start_date=start_date, end_date=end_date)
        ads    = AdsAPIClient(cfg).get_campaign_summary(date_range) if cfg.has_ads_credentials() else {}
        return JsonResponse({'success': True, 'sales': data, 'ads': ads, 'targets': _build_targets_payload()})
    except Exception as e:
        logger.error(f'Dashboard data fetch error: {e}')
        return JsonResponse({'error': str(e), 'targets': _build_targets_payload()}, status=500)
