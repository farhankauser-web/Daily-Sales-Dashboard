"""
apps/dashboard/management/commands/seed_inventory_ppc.py

Seeds demo InventorySnapshot and PPCCampaignSnapshot records.
Run after seed_demo_data:
  python manage.py seed_inventory_ppc
"""
import random
from datetime import date, timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Seed demo inventory snapshots and PPC campaign data'

    def add_arguments(self, parser):
        parser.add_argument('--days',  type=int, default=60)
        parser.add_argument('--clear', action='store_true')

    def handle(self, *args, **opts):
        from apps.dashboard.models import (
            Product, InventorySnapshot, PPCCampaignSnapshot
        )

        if opts['clear']:
            InventorySnapshot.objects.all().delete()
            PPCCampaignSnapshot.objects.all().delete()
            self.stdout.write('Cleared.')

        products = list(Product.objects.all())
        if not products:
            self.stdout.write(self.style.WARNING('No products found — run seed_demo_data first.'))
            return

        today = date.today()

        # ── INVENTORY ─────────────────────────────────────────────────────────
        self.stdout.write('Seeding inventory snapshots…')
        inv_created = 0

        for p in products:
            # Starting stock + decay model
            stock      = random.randint(400, 2000)
            daily_sales= random.uniform(20, 80)
            reorder_pt = int(daily_sales * 50)   # 50 day lead + buffer
            safety     = int(daily_sales * 14)

            for i in range(opts['days'], -1, -1):
                d = today - timedelta(days=i)

                # Simulate daily sales depletion
                noise   = random.uniform(0.7, 1.3)
                sold    = int(daily_sales * noise)
                stock   = max(0, stock - sold)

                # Simulate periodic replenishment (every 35-45 days)
                if stock < reorder_pt and random.random() < 0.08:
                    stock += random.randint(500, 1200)

                inbound_shipped  = random.randint(0, 200) if random.random() < 0.15 else 0
                inbound_working  = random.randint(0, 100) if random.random() < 0.10 else 0
                fulfillable      = max(0, stock)
                days_cover       = (fulfillable + inbound_shipped) / daily_sales if daily_sales else 0

                InventorySnapshot.objects.update_or_create(
                    product=p, date=d,
                    defaults={
                        'afn_fulfillable':       fulfillable,
                        'afn_reserved':          int(fulfillable * 0.05),
                        'afn_inbound_shipped':   inbound_shipped,
                        'afn_inbound_working':   inbound_working,
                        'afn_inbound_receiving': random.randint(0, 50) if random.random() < 0.05 else 0,
                        'afn_unsellable':        random.randint(0, 5),
                        'warehouse_stock':       random.randint(100, 400),
                        'days_cover':            Decimal(str(round(days_cover, 1))),
                        'reorder_point':         reorder_pt,
                        'safety_stock':          safety,
                    }
                )
                inv_created += 1

        self.stdout.write(self.style.SUCCESS(f'  ✓ {inv_created} inventory rows seeded.'))

        # ── PPC CAMPAIGNS ─────────────────────────────────────────────────────
        self.stdout.write('Seeding PPC campaign snapshots…')
        ppc_created = 0

        campaigns_config = {
            'usa': [
                {'id': 'SP-001', 'name': 'Towels | Exact | Top Terms',    'type': 'sp', 'budget': 150, 'base_spend': 85},
                {'id': 'SP-002', 'name': 'Towels | Broad | Discovery',     'type': 'sp', 'budget': 80,  'base_spend': 45},
                {'id': 'SP-003', 'name': 'Bedsheets | Exact | Main',       'type': 'sp', 'budget': 120, 'base_spend': 70},
                {'id': 'SP-004', 'name': 'Competitor | Conquest',          'type': 'sp', 'budget': 60,  'base_spend': 35},
                {'id': 'SB-001', 'name': 'Brand | Sponsored Brands',       'type': 'sb', 'budget': 50,  'base_spend': 30},
                {'id': 'SD-001', 'name': 'Retargeting | Display',          'type': 'sd', 'budget': 40,  'base_spend': 20},
            ],
            'uk': [
                {'id': 'SP-UK-001', 'name': 'Towels | Exact UK',           'type': 'sp', 'budget': 50, 'base_spend': 28},
                {'id': 'SP-UK-002', 'name': 'Bedsheets | UK Broad',        'type': 'sp', 'budget': 40, 'base_spend': 22},
            ],
            'ca': [
                {'id': 'SP-CA-001', 'name': 'Towels | Exact CA',           'type': 'sp', 'budget': 40, 'base_spend': 22},
            ],
            'de': [
                {'id': 'SP-DE-001', 'name': 'Handtücher | Exact',          'type': 'sp', 'budget': 35, 'base_spend': 18},
            ],
        }

        for mp, clist in campaigns_config.items():
            for camp in clist:
                for i in range(opts['days'], -1, -1):
                    d      = today - timedelta(days=i)
                    noise  = random.uniform(0.75, 1.28)
                    spend  = Decimal(str(round(camp['base_spend'] * noise, 2)))
                    sales  = Decimal(str(round(float(spend) / random.uniform(0.08, 0.18), 2)))
                    clicks = int(float(spend) / random.uniform(0.25, 0.80))
                    impr   = int(clicks * random.uniform(30, 80))
                    orders = int(float(sales) / random.uniform(28, 55))
                    acos   = Decimal(str(round(float(spend) / float(sales) if sales else 0, 6)))
                    roas   = Decimal(str(round(float(sales) / float(spend) if spend else 0, 4)))
                    ctr    = Decimal(str(round(clicks / impr if impr else 0, 6)))
                    cvr    = Decimal(str(round(orders / clicks if clicks else 0, 6)))
                    cpc    = Decimal(str(round(float(spend) / clicks if clicks else 0, 4)))
                    consumed = Decimal(str(min(100, round(float(spend) / camp['budget'] * 100, 2))))

                    PPCCampaignSnapshot.objects.update_or_create(
                        marketplace=mp, date=d, campaign_id=camp['id'],
                        defaults={
                            'campaign_name':  camp['name'],
                            'campaign_type':  camp['type'],
                            'state':          'enabled',
                            'impressions':    impr,
                            'clicks':         clicks,
                            'spend':          spend,
                            'sales_7d':       sales,
                            'orders_7d':      orders,
                            'units_7d':       int(orders * 1.1),
                            'acos':           acos,
                            'roas':           roas,
                            'ctr':            ctr,
                            'cvr':            cvr,
                            'cpc':            cpc,
                            'daily_budget':   Decimal(str(camp['budget'])),
                            'budget_consumed': consumed,
                        }
                    )
                    ppc_created += 1

        self.stdout.write(self.style.SUCCESS(f'  ✓ {ppc_created} PPC rows seeded.'))

        # ── GENERATE SAMPLE ALERTS ────────────────────────────────────────────
        self.stdout.write('Generating sample alerts…')
        from apps.dashboard.models import Alert, InventorySnapshot as IS

        low_stock = IS.objects.filter(
            date=today, afn_fulfillable__lt=100
        ).select_related('product')[:5]

        for s in low_stock:
            dc = float(s.days_cover)
            if dc < 30:
                Alert.create_inventory_alert(s.product, dc, s.afn_fulfillable)

        self.stdout.write(self.style.SUCCESS('✅ Inventory + PPC data seeded successfully.'))
