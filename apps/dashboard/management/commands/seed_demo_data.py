"""
apps/dashboard/management/commands/seed_demo_data.py

Seeds ~90 days of demo DailyMetric data for the dashboard charts to work
without a live SP-API connection.

Usage:
  python manage.py seed_demo_data
  python manage.py seed_demo_data --marketplace usa --days 90
"""
import random
from datetime import date, timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Seed demo historical metrics and product catalog data'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='all',
                            help='usa | ca | uk | de | ae | sa | all')
        parser.add_argument('--days', type=int, default=90)
        parser.add_argument('--clear', action='store_true',
                            help='Clear existing metrics before seeding')

    def handle(self, *args, **opts):
        from apps.dashboard.models import DailyMetric, Product, COGSEntry, MonthlyTarget

        marketplaces = (
            ['usa', 'ca', 'uk', 'de', 'ae', 'sa']
            if opts['marketplace'] == 'all'
            else [opts['marketplace']]
        )

        if opts['clear']:
            DailyMetric.objects.filter(marketplace__in=marketplaces).delete()
            self.stdout.write('Cleared existing metrics.')

        # Base stats per marketplace (realistic for towels/bedsheets)
        base = {
            'usa': {'rev': 22000, 'units': 890, 'ppc': 3500, 'gm': 0.14, 'cm': 0.29},
            'ca':  {'rev':  5500, 'units': 210, 'ppc':  820, 'gm': 0.12, 'cm': 0.26},
            'uk':  {'rev':  4800, 'units': 185, 'ppc':  700, 'gm': 0.13, 'cm': 0.27},
            'de':  {'rev':  3200, 'units': 125, 'ppc':  480, 'gm': 0.11, 'cm': 0.24},
            'ae':  {'rev':  2100, 'units':  80, 'ppc':  310, 'gm': 0.15, 'cm': 0.30},
            'sa':  {'rev':  1800, 'units':  70, 'ppc':  260, 'gm': 0.14, 'cm': 0.28},
        }

        today = date.today()
        created = 0

        for mp in marketplaces:
            b = base.get(mp, base['usa'])
            self.stdout.write(f'  Seeding {mp.upper()} for {opts["days"]} days…')

            for i in range(opts['days'], -1, -1):
                d = today - timedelta(days=i)

                # Add seasonality (weekends +15%, Mondays -10%)
                dow_factor = {0: 0.90, 1: 1.00, 2: 1.00, 3: 1.00, 4: 1.05, 5: 1.15, 6: 1.10}
                wf = dow_factor.get(d.weekday(), 1.0)

                # Long-term growth trend (+0.2% per day)
                trend = 1 + (opts['days'] - i) * 0.002

                noise  = random.uniform(0.85, 1.18)
                rev    = b['rev'] * wf * trend * noise
                units  = int(b['units'] * wf * trend * noise)
                ppc    = b['ppc'] * wf * trend * random.uniform(0.88, 1.12)
                tacos  = ppc / rev if rev else 0
                acos   = ppc / (rev * 0.55) if rev else 0  # ~55% of sales from PPC
                gm_pct = b['gm'] * random.uniform(0.92, 1.08)
                cm_pct = b['cm'] * random.uniform(0.94, 1.06)
                gm     = rev * gm_pct
                cm     = rev * cm_pct
                sess   = int(units * random.uniform(12, 18))
                cvr    = units / sess if sess else 0

                DailyMetric.objects.update_or_create(
                    marketplace=mp, date=d,
                    defaults={
                        'revenue':             Decimal(str(round(rev, 2))),
                        'units':               units,
                        'orders':              int(units * 0.92),
                        'sessions':            sess,
                        'page_views':          int(sess * 2.3),
                        'conversion_rate':     Decimal(str(round(cvr, 6))),
                        'ppc_spend':           Decimal(str(round(ppc, 2))),
                        'ppc_sales':           Decimal(str(round(rev * 0.55, 2))),
                        'ppc_impressions':     int(sess * 8),
                        'ppc_clicks':          int(sess * 0.04),
                        'acos':                Decimal(str(round(acos, 6))),
                        'roas':                Decimal(str(round(1/acos if acos else 0, 4))),
                        'tacos':               Decimal(str(round(tacos, 6))),
                        'gross_margin':        Decimal(str(round(gm, 2))),
                        'gm_pct':              Decimal(str(round(gm_pct, 6))),
                        'contribution_margin': Decimal(str(round(cm, 2))),
                        'cm_pct':              Decimal(str(round(cm_pct, 6))),
                    }
                )
                created += 1

        self.stdout.write(self.style.SUCCESS(f'✓ {created} daily metric rows seeded.'))

        # Seed products
        self._seed_products()

        # Seed targets
        self._seed_targets(today)

    def _seed_products(self):
        from apps.dashboard.models import Product

        products = [
            ('B0C7XL92MN', 'usa', 'Premium Cotton Bath Towel Set 6-Piece', 49.99, 4.50, 39.99),
            ('B0D1KP44RQ', 'usa', 'Egyptian Cotton Bed Sheet Set King', 89.99, 6.20, 74.99),
            ('B0B8MN66TT', 'usa', 'Luxury Spa Collection Towel Bundle', 59.99, 5.10, 49.99),
            ('B0A2QR77LL', 'usa', 'Microfibre Quick-Dry Bath Sheet 2-Pack', 34.99, 2.80, 27.99),
            ('B0F3WX55PP', 'usa', 'Hotel Collection White Bath Towels 4-Pack', 44.99, 3.90, 36.99),
            ('B0C7XL92MN', 'uk',  'Premium Cotton Bath Towel Set 6-Piece UK', 39.99, 4.20, 32.99),
            ('B0D1KP44RQ', 'ca',  'Egyptian Cotton Bed Sheet Set King CA', 99.99, 6.80, 82.99),
        ]

        for asin, mp, title, list_p, fba, sale in products:
            Product.objects.get_or_create(
                asin=asin, marketplace=mp,
                defaults={
                    'title': title, 'brand': 'Infinitee Xclusives',
                    'category': 'Home & Kitchen', 'status': 'active',
                    'list_price': list_p, 'sale_price': sale,
                    'fba_fee': fba, 'referral_fee_pct': 15.0,
                }
            )

        self.stdout.write(f'  ✓ {len(products)} products seeded.')

    def _seed_targets(self, today):
        from apps.dashboard.models import MonthlyTarget

        month = today.replace(day=1)
        targets = [
            ('usa', 700000, 28000, 14.00, 25.00, 110000),
            ('ca',  170000,  6500, 15.00, 24.00,  26000),
            ('uk',  150000,  5800, 15.00, 24.00,  23000),
            ('de',  100000,  3900, 16.00, 22.00,  16000),
            ('ae',   65000,  2500, 15.00, 25.00,  10000),
            ('sa',   56000,  2200, 15.00, 25.00,   8700),
        ]
        for mp, rev, units, tacos, gm, ppc in targets:
            MonthlyTarget.objects.get_or_create(
                marketplace=mp, month=month,
                defaults={
                    'revenue_target': rev, 'units_target': units,
                    'tacos_target': tacos, 'gm_target': gm, 'ppc_budget': ppc,
                }
            )
        self.stdout.write(f'  ✓ {len(targets)} monthly targets seeded.')
