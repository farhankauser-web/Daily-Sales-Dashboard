"""
apps/dashboard/management/commands/calculate_margins.py

Reads COGS entries and re-calculates GM% and CM% on DailyMetric rows.
Run after uploading new COGS data:
  python manage.py calculate_margins
  python manage.py calculate_margins --month 2026-05
"""
from datetime import date
from decimal import Decimal
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Calculate gross and contribution margins from COGS data'

    def add_arguments(self, parser):
        parser.add_argument('--month', default=None, help='YYYY-MM to recalculate (default: current month)')
        parser.add_argument('--marketplace', default='all')

    def handle(self, *args, **opts):
        from apps.dashboard.models import DailyMetric, COGSEntry

        today = date.today()
        if opts['month']:
            y, m = opts['month'].split('-')
            month_start = date(int(y), int(m), 1)
        else:
            month_start = today.replace(day=1)

        qs = DailyMetric.objects.filter(date__year=month_start.year, date__month=month_start.month)
        if opts['marketplace'] != 'all':
            qs = qs.filter(marketplace=opts['marketplace'])

        # Build COGS lookup: {(asin, mp, month_start): total_cost}
        cogs_map = {}
        for ce in COGSEntry.objects.filter(month=month_start):
            key = (ce.product.marketplace, month_start)
            cogs_map.setdefault(key, []).append(float(ce.total_cost))

        # Average COGS per unit per marketplace per month
        avg_cogs = {}
        for (mp, month), costs in cogs_map.items():
            avg_cogs[(mp, month)] = sum(costs) / len(costs) if costs else 0

        updated = 0
        for m in qs:
            key      = (m.marketplace, month_start)
            unit_cgs = Decimal(str(avg_cogs.get(key, 4.5)))  # $4.50 fallback

            # Gross Margin = Revenue - (COGS × Units) - FBA fees (approx 20% of rev) - Referral fees (15%)
            amazon_fees = m.revenue * Decimal('0.35')   # FBA + referral approx
            total_cogs  = unit_cgs * m.units
            gm          = m.revenue - total_cogs - amazon_fees
            gm_pct      = gm / m.revenue if m.revenue else Decimal('0')

            # Contribution Margin = GM - PPC spend
            cm     = gm - m.ppc_spend
            cm_pct = cm / m.revenue if m.revenue else Decimal('0')

            DailyMetric.objects.filter(pk=m.pk).update(
                gross_margin=gm, gm_pct=gm_pct,
                contribution_margin=cm, cm_pct=cm_pct,
            )
            updated += 1

        self.stdout.write(self.style.SUCCESS(f'✓ Updated margins for {updated} daily metric rows.'))
