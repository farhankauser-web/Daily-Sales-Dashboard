"""Atlas Phase 2 tests: RFQ TAT + cost sync, PO stages/TAT, backorders, forecast."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.atlas import supply
from apps.atlas.models import (AtlasCompany, AtlasCustomer, AtlasProduct,
                               AtlasSupplier, Backorder)


def base():
    co = AtlasCompany.objects.create(code='rmt', name='RMT', currency='AED')
    cust = AtlasCustomer.objects.create(company=co, name='Al Noor',
                                        kg_rate_local=Decimal('20'))
    prod = AtlasProduct.objects.create(
        company=co, sku='BT-1', description='Towel',
        length_cm=100, width_cm=50, gsm=500, cost=Decimal('5'),
        stock_qty=10, sell_through_daily=Decimal('4'),
        production_lead_days=20, shipment_lead_days=10)
    return co, cust, prod


class RFQTests(TestCase):
    def test_rfq_lifecycle_and_cost_sync(self):
        co, cust, prod = base()
        rfq = supply.create_rfq(co, customer=cust, product=prod,
                                quantity=500, port='Jebel Ali')
        self.assertEqual(rfq.reference[:8], 'RFQ-RMT-')
        self.assertFalse(rfq.is_overdue)

        resp = supply.respond_rfq(rfq, kind='actual', moq=300,
                                  lead_time_days=45,
                                  fob_rate=Decimal('6.25'),
                                  cnf_rate=Decimal('6.80'))
        rfq.refresh_from_db()
        self.assertEqual(rfq.status, 'responded')
        supply.apply_response_to_cost(resp, 'fob')
        prod.refresh_from_db()
        self.assertEqual(prod.cost, Decimal('6.25'))
        resp.refresh_from_db()
        self.assertTrue(resp.applied_to_cost)

    def test_rfq_overdue_after_24h_and_revalidation_restarts_tat(self):
        co, cust, prod = base()
        rfq = supply.create_rfq(co, product=prod, quantity=100)
        RFQ_cls = type(rfq)
        RFQ_cls.objects.filter(pk=rfq.pk).update(
            created_at=timezone.now() - timedelta(hours=25))
        rfq.refresh_from_db()
        self.assertTrue(rfq.is_overdue)
        self.assertEqual(len(supply.overdue_rfqs(co)), 1)

        supply.respond_rfq(rfq, fob_rate=Decimal('6'))
        rfq.refresh_from_db()
        self.assertFalse(rfq.is_overdue)          # responded → clear

        supply.request_revalidation(rfq)
        rfq.refresh_from_db()
        self.assertEqual(rfq.status, 'revalidation')
        self.assertFalse(rfq.is_overdue)          # fresh 24h window


class POTests(TestCase):
    def test_po_stages_seeded_and_advance(self):
        co, cust, prod = base()
        po = supply.create_po(co, [{'product_id': prod.pk, 'quantity': 100,
                                    'rate': 6}], customer=cust)
        self.assertEqual(po.reference[:7], 'PO-RMT-')
        self.assertEqual(po.stages.count(), 7)
        self.assertEqual(po.current_stage.name, 'Order confirmed')
        nxt = supply.complete_stage(po)
        self.assertEqual(nxt.name, 'In production')
        self.assertIsNotNone(nxt.started_at)

    def test_stage_tat_breach_detection(self):
        co, cust, prod = base()
        po = supply.create_po(co, [{'product_id': prod.pk, 'quantity': 10}])
        st = po.current_stage           # TAT 2 days
        type(st).objects.filter(pk=st.pk).update(
            started_at=timezone.now() - timedelta(days=3))
        self.assertEqual(len(supply.breached_stages(co)), 1)

    def test_short_receipt_creates_backorder_then_resolves(self):
        co, cust, prod = base()
        po = supply.create_po(co, [{'product_id': prod.pk, 'quantity': 100}],
                              customer=cust)
        line = po.lines.get()
        res = supply.receive_po(po, {line.pk: 60})
        self.assertFalse(res['all_received'])
        self.assertEqual(res['backorders_created'], 1)
        prod.refresh_from_db()
        self.assertEqual(prod.stock_qty, 70)      # 10 + 60
        bo = Backorder.objects.get()
        self.assertEqual(bo.quantity, 40)
        self.assertEqual(bo.customer, cust)       # surfaces on their quotes

        supply.resolve_backorder(bo, 'received')
        prod.refresh_from_db()
        self.assertEqual(prod.stock_qty, 110)
        line.refresh_from_db()
        self.assertEqual(line.qty_pending, 0)

    def test_full_receipt_closes_po(self):
        co, cust, prod = base()
        po = supply.create_po(co, [{'product_id': prod.pk, 'quantity': 50}])
        res = supply.receive_po(po, {po.lines.get().pk: 50})
        self.assertTrue(res['all_received'])
        po.refresh_from_db()
        self.assertEqual(po.status, 'received')


class ForecastTests(TestCase):
    def test_refill_formula_non_peak(self):
        co, cust, prod = base()
        # 4/day × 30d lead = 120 demand; stock 10 → reorder 110; cover 2.5d < 30d
        f = supply.forecast_product(prod)
        self.assertEqual(f['lead_days'], 30)
        self.assertEqual(f['demand_over_lead'], 120.0)
        self.assertEqual(f['reorder_qty'], 110)
        self.assertEqual(f['cover_days'], 2.5)
        self.assertTrue(f['refill_due'])

    def test_peak_mode_scales_demand(self):
        co, cust, prod = base()
        prod.is_peak = True
        prod.peak_multiplier = Decimal('2')
        prod.save()
        f = supply.forecast_product(prod)
        self.assertEqual(f['daily_demand'], 8.0)
        self.assertEqual(f['reorder_qty'], 230)   # 240 − 10

    def test_no_demand_no_refill(self):
        co, cust, prod = base()
        prod.sell_through_daily = 0
        prod.save()
        f = supply.forecast_product(prod)
        self.assertFalse(f['refill_due'])
        self.assertIsNone(f['cover_days'])


class AlertCommandTests(TestCase):
    def test_alert_sweep_counts(self):
        from django.core.management import call_command
        from io import StringIO
        co, cust, prod = base()
        rfq = supply.create_rfq(co, product=prod, quantity=1)
        type(rfq).objects.filter(pk=rfq.pk).update(
            created_at=timezone.now() - timedelta(hours=30))
        po = supply.create_po(co, [{'product_id': prod.pk, 'quantity': 5}])
        st = po.current_stage
        type(st).objects.filter(pk=st.pk).update(
            started_at=timezone.now() - timedelta(days=5))
        out = StringIO()
        with mock.patch('apps.atlas.management.commands.atlas_alerts.notify_admin') as n:
            call_command('atlas_alerts', stdout=out)
            self.assertGreaterEqual(n.call_count, 3)   # rfq + stage + refill
        import json
        res = json.loads(out.getvalue())
        self.assertEqual(res['rfq_overdue'], 1)
        self.assertEqual(res['po_stage_breaches'], 1)
        self.assertEqual(res['refill_due'], 1)
        # stage alert is one-shot
        st.refresh_from_db()
        self.assertTrue(st.alerted)
