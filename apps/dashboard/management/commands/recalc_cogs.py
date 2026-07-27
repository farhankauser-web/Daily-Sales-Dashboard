"""
Recalculate stored COGS for one (marketplace, month) after a COGS upload.
Refreshes: Management P&L, DailySkuSnapshot/DailyMetric, hourly snapshots,
and CampaignProfitDaily.

Usage:
    manage.py recalc_cogs --marketplace usa --month 2026-05
    manage.py recalc_cogs --marketplace usa --month 2026-05 --skip-campaign-profit
"""
from __future__ import annotations

import json
from datetime import date
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Recalculate stored COGS everywhere for one month.'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--month', required=True, help='YYYY-MM')
        parser.add_argument('--skip-campaign-profit', action='store_true')

    def handle(self, marketplace, month, skip_campaign_profit, **_):
        from apps.dashboard.cogs_recalc import recalc_cogs
        y, m = month.split('-')[:2]
        summary = recalc_cogs(marketplace, date(int(y), int(m), 1),
                              run_campaign_profit=not skip_campaign_profit)
        self.stdout.write(json.dumps(summary, indent=2, default=str))
        self.stdout.write(self.style.SUCCESS('COGS recalculation complete.'))
