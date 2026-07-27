"""Atlas Phase 1 tests: SOW pricing formula, quotation flow, funnel."""
from decimal import Decimal

from django.test import TestCase

from apps.atlas import services
from apps.atlas.models import (AtlasCompany, AtlasCustomer, AtlasProduct,
                               CustomerKgRate, NegativeStockLog, PaymentTerm)


def base(company_kwargs=None):
    co = AtlasCompany.objects.create(code='rmt', name='RMT', currency='AED',
                                     vat_rate=Decimal('0.05'),
                                     **(company_kwargs or {}))
    term = PaymentTerm.objects.create(name='60 days', days=60)
    cust = AtlasCustomer.objects.create(
        company=co, name='Al Noor Trading',
        kg_rate_local=Decimal('20'), kg_rate_container=Decimal('17.5'),
        default_payment_term=term)
    # 76×137 cm, 600 GSM bath towel
    prod = AtlasProduct.objects.create(
        company=co, sku='BT-HSP-20D-600', description='Bath towel 600gsm',
        length_cm=Decimal('137'), width_cm=Decimal('76'), gsm=600,
        quality='Hotel Spa', cost=Decimal('8.5'), stock_qty=100)
    return co, cust, prod


class PricingTests(TestCase):
    def test_sow_weight_and_price_formula(self):
        co, cust, prod = base()
        # weight = 137×76/10,000/1,000×600 = 0.624720 kg
        self.assertAlmostEqual(float(prod.weight_kg), 0.62472, places=5)
        p = services.price_product(cust, prod, 'local')
        # 20 AED/kg × 0.62472 = 12.4944
        self.assertEqual(p['unit_price'], Decimal('12.4944'))
        p2 = services.price_product(cust, prod, 'container')
        self.assertEqual(p2['unit_price'], Decimal('10.9326'))

    def test_per_article_override_beats_flat_rate(self):
        co, cust, prod = base()
        CustomerKgRate.objects.create(customer=cust, product=prod,
                                      order_type='local',
                                      kg_rate=Decimal('25'))
        p = services.price_product(cust, prod, 'local')
        self.assertEqual(p['kg_rate'], Decimal('25'))
        self.assertEqual(p['unit_price'], Decimal('15.6180'))

    def test_missing_rate_prices_at_zero_not_error(self):
        co, cust, prod = base()
        cust.kg_rate_local = None
        cust.save()
        p = services.price_product(cust, prod, 'local')
        self.assertEqual(p['unit_price'], Decimal('0.0000'))


class QuotationTests(TestCase):
    def test_create_prices_lines_and_writes_revision(self):
        co, cust, prod = base()
        q = services.create_quotation(
            co, cust, 'local', [{'product_id': prod.pk, 'quantity': 50}])
        ln = q.lines.get()
        self.assertEqual(ln.unit_price, Decimal('12.4944'))
        self.assertEqual(q.reference[:13], 'ATL-RMT-2026-')
        self.assertEqual(q.payment_term.name, '60 days')   # inherited default
        self.assertEqual(q.revisions.count(), 1)
        t = q.totals()
        self.assertEqual(t['subtotal'], Decimal('12.4944') * 50)
        self.assertEqual(t['vat'], t['net'] * Decimal('0.05'))

    def test_stock_shortage_flagged_and_logged_never_blocked(self):
        co, cust, prod = base()
        q = services.create_quotation(
            co, cust, 'local', [{'product_id': prod.pk, 'quantity': 150}])
        self.assertTrue(q.has_stock_shortage)
        self.assertEqual(q.lines.get().stock_short, 50)
        self.assertEqual(NegativeStockLog.objects.get().qty_short, 50)

    def test_status_flow_and_funnel(self):
        co, cust, prod = base()
        refs = []
        for i in range(3):
            q = services.create_quotation(
                co, cust, 'local', [{'product_id': prod.pk, 'quantity': 10}])
            refs.append(q)
        services.set_status(refs[0], 'sent')
        services.set_status(refs[0], 'won')
        services.set_status(refs[1], 'sent')
        services.set_status(refs[1], 'lost', lost_reason='price too high')
        f = services.funnel(co)
        self.assertEqual(f['won']['count'], 1)
        self.assertEqual(f['lost']['count'], 1)
        self.assertEqual(f['draft']['count'], 1)
        self.assertEqual(f['win_rate'], 50.0)
        self.assertEqual(refs[1].lost_reason, 'price too high')
        # every status change wrote a revision
        self.assertEqual(refs[0].revisions.count(), 3)

    def test_reference_sequence_and_line_discount(self):
        co, cust, prod = base()
        q1 = services.create_quotation(co, cust, 'local',
                                       [{'product_id': prod.pk, 'quantity': 1}])
        q2 = services.create_quotation(
            co, cust, 'container',
            [{'product_id': prod.pk, 'quantity': 10, 'discount_pct': 10}],
            container_type='fob')
        self.assertEqual(int(q2.reference[-4:]) - int(q1.reference[-4:]), 1)
        ln = q2.lines.get()
        self.assertEqual(ln.line_total,
                         Decimal('10.9326') * 10 * Decimal('0.9'))
        self.assertEqual(q2.container_type, 'fob')
