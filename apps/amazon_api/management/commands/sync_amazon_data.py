"""
apps/amazon_api/management/commands/sync_amazon_data.py

Pulls live SP-API + Ads API data and persists into DailyMetric.
Run via cron or scheduler:
  python manage.py sync_amazon_data
  python manage.py sync_amazon_data --marketplace usa --days 7
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync Amazon SP-API data into DailyMetric table'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='all')
        parser.add_argument('--days', type=int, default=1,
                            help='Number of days back to sync (default: 1 = yesterday+today)')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig, APISyncLog
        from apps.amazon_api.services import SPAPIClient, AdsAPIClient
        from apps.dashboard.models import DailyMetric

        mp_filter = opts['marketplace']
        configs = AmazonAPIConfig.objects.filter(is_active=True)
        if mp_filter != 'all':
            configs = configs.filter(marketplace=mp_filter)

        if not configs.exists():
            self.stdout.write(self.style.WARNING('No active API configs found.'))
            return

        today = date.today()
        for cfg in configs:
            self.stdout.write(f'Syncing {cfg.get_marketplace_display()}…')

            if not cfg.has_sp_api_credentials():
                self.stdout.write(self.style.WARNING(f'  ⚠ Missing credentials, skipping.'))
                continue

            start_time = timezone.now()
            records    = 0
            error_msg  = ''

            try:
                client = SPAPIClient(cfg)

                for i in range(opts['days'], -1, -1):
                    d = today - timedelta(days=i)
                    range_str = 'today' if i == 0 else str(d)

                    try:
                        sales_data = client.get_sales_data(range_str if i == 0 else 'custom')
                        ads_data   = {}
                        if cfg.has_ads_credentials():
                            ads_client = AdsAPIClient(cfg)
                            try:
                                ads_data = ads_client.get_campaign_summary(range_str)
                            except Exception as ae:
                                logger.warning(f'Ads API error for {cfg.marketplace}: {ae}')

                        # Parse and save
                        metrics = self._parse_metrics(sales_data, ads_data, d)
                        DailyMetric.objects.update_or_create(
                            marketplace=cfg.marketplace, date=d,
                            defaults=metrics
                        )
                        records += 1

                    except Exception as e:
                        logger.error(f'Error syncing {cfg.marketplace} for {d}: {e}')
                        error_msg += f'{d}: {e}; '

                cfg.last_test_status = 'ok' if not error_msg else 'error'
                cfg.last_tested_at   = timezone.now()
                cfg.last_test_detail = error_msg or 'Sync OK'
                cfg.save(update_fields=['last_test_status', 'last_tested_at', 'last_test_detail'])

            except Exception as e:
                error_msg = str(e)
                logger.error(f'Fatal sync error for {cfg.marketplace}: {e}')

            duration = int((timezone.now() - start_time).total_seconds() * 1000)
            APISyncLog.objects.create(
                config=cfg, data_type='sales+ads',
                date_range=f'{today - timedelta(days=opts["days"])} to {today}',
                status='ok' if not error_msg else 'error',
                records=records,
                error_msg=error_msg,
                duration_ms=duration,
            )
            self.stdout.write(self.style.SUCCESS(f'  ✓ {records} days synced in {duration}ms'))

    def _parse_metrics(self, sales_data, ads_data, d):
        """Parse raw SP-API response into DailyMetric fields."""
        # SP-API Sales & Traffic response structure
        payload = sales_data.get('payload', [])
        day_data = next((p for p in payload if p.get('date', '').startswith(str(d))), {}) if payload else {}

        sales   = day_data.get('sales', {})
        traffic = day_data.get('traffic', {})

        revenue  = Decimal(str(sales.get('orderedProductSales', {}).get('amount', 0) or 0))
        units    = int(sales.get('unitsOrdered', 0) or 0)
        orders   = int(sales.get('totalOrderItems', 0) or 0)
        sessions = int(traffic.get('sessions', 0) or 0)
        pviews   = int(traffic.get('pageViews', 0) or 0)
        cvr      = Decimal(str(traffic.get('unitSessionPercentage', 0) or 0)) / 100

        # Ads data
        ppc_spend  = Decimal(str(ads_data.get('spend', 0) or 0))
        ppc_sales  = Decimal(str(ads_data.get('sales7d', 0) or 0))
        impressions= int(ads_data.get('impressions', 0) or 0)
        clicks     = int(ads_data.get('clicks', 0) or 0)

        tacos = ppc_spend / revenue if revenue else Decimal('0')
        acos  = ppc_spend / ppc_sales if ppc_sales else Decimal('0')
        roas  = ppc_sales / ppc_spend if ppc_spend else Decimal('0')

        return {
            'revenue': revenue, 'units': units, 'orders': orders,
            'sessions': sessions, 'page_views': pviews, 'conversion_rate': cvr,
            'ppc_spend': ppc_spend, 'ppc_sales': ppc_sales,
            'ppc_impressions': impressions, 'ppc_clicks': clicks,
            'acos': acos, 'roas': roas, 'tacos': tacos,
            # GM and CM require COGS — computed separately by calculate_margins command
            'gross_margin': Decimal('0'), 'gm_pct': Decimal('0'),
            'contribution_margin': Decimal('0'), 'cm_pct': Decimal('0'),
        }
