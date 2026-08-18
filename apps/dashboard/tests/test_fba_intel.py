"""FBA Fee Intelligence (Phase A) — calculation tests.

The money figures on this page all come from one rule, so it is tested hard:

    weighted_fee = sum(fba_fee_total) / sum(units)     per window
    impact       = (current_weighted - previous_weighted) x BILLED units
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from apps.dashboard import fba_intel
from apps.dashboard.models import (DailySkuSnapshot, InventorySnapshot,
                                   Product, SkuFeeActual)

ANCHOR = date(2026, 8, 18)


def fee(sku, day, units, total, mp='usa'):
    return SkuFeeActual.objects.create(
        marketplace=mp, sku=sku, date=day, units=units,
        fba_fee_total=Decimal(str(total)),
        fee_per_unit=Decimal(str(total / units if units else 0)))


class PeriodTests(TestCase):
    def test_windows_are_adjacent_and_equal_length(self):
        cur, prev = fba_intel.resolve_periods(30, ANCHOR)
        self.assertEqual(cur.end, ANCHOR)
        self.assertEqual((cur.end - cur.start).days + 1, 30)
        self.assertEqual((prev.end - prev.start).days + 1, 30)
        self.assertEqual(prev.end, cur.start - timedelta(days=1))

    def test_seven_day_window(self):
        cur, prev = fba_intel.resolve_periods(7, ANCHOR)
        self.assertEqual(cur.start, date(2026, 8, 12))
        self.assertEqual(prev.start, date(2026, 8, 5))


class WeightedFeeTests(TestCase):
    def test_weighted_not_simple_average(self):
        """100 units at $5 and 1 unit at $50 is $5.44, not $27.50."""
        fee('S1', ANCHOR, 100, 500.0)
        fee('S1', ANCHOR - timedelta(days=1), 1, 50.0)
        d = fba_intel.compute('usa', 7, anchor=ANCHOR)
        row = next(r for r in d['rows'] if r['sku'] == 'S1')
        self.assertAlmostEqual(row['current_fee'], 550 / 101, places=4)

    def test_zero_units_is_none_not_zero(self):
        self.assertIsNone(fba_intel._weighted(0.0, 0))


class ImpactTests(TestCase):
    def _two_windows(self, prev_fee, cur_fee, units):
        cur, prev = fba_intel.resolve_periods(7, ANCHOR)
        fee('S1', prev.end, units, prev_fee * units)
        fee('S1', cur.end, units, cur_fee * units)
        d = fba_intel.compute('usa', 7, anchor=ANCHOR)
        return next(r for r in d['rows'] if r['sku'] == 'S1')

    def test_fee_increase_is_incremental_cost(self):
        r = self._two_windows(7.73, 8.60, 1000)
        self.assertAlmostEqual(r['fee_delta'], 0.87, places=4)
        self.assertAlmostEqual(r['incremental_cost'], 870.0, places=2)
        self.assertEqual(r['savings'], 0.0)
        self.assertAlmostEqual(r['net_impact'], 870.0, places=2)

    def test_fee_decrease_is_saving(self):
        r = self._two_windows(8.60, 7.73, 500)
        self.assertAlmostEqual(r['fee_delta'], -0.87, places=4)
        self.assertAlmostEqual(r['savings'], 435.0, places=2)
        self.assertEqual(r['incremental_cost'], 0.0)
        self.assertAlmostEqual(r['net_impact'], -435.0, places=2)

    def test_impact_uses_billed_units_not_units_sold(self):
        """The confirmed business rule: billed units drive the money."""
        cur, prev = fba_intel.resolve_periods(7, ANCHOR)
        fee('S1', prev.end, 100, 700.0)      # $7.00
        fee('S1', cur.end, 100, 800.0)       # $8.00 → +$1.00 x 100 billed
        DailySkuSnapshot.objects.create(     # 9,999 SOLD — must NOT be used
            marketplace='usa', date=cur.end, sku='S1', qty=9999,
            revenue=Decimal('0'))
        d = fba_intel.compute('usa', 7, anchor=ANCHOR)
        r = next(x for x in d['rows'] if x['sku'] == 'S1')
        self.assertEqual(r['billed_units'], 100)
        self.assertEqual(r['units_sold'], 9999)
        self.assertAlmostEqual(r['incremental_cost'], 100.0, places=2)


class DriftTests(TestCase):
    def test_7_14_30_computed_independently(self):
        for i in range(60):
            d = ANCHOR - timedelta(days=i)
            fee('S1', d, 10, 10 * (8.0 if i < 30 else 7.0))
        d = fba_intel.compute('usa', 30, anchor=ANCHOR)
        r = next(x for x in d['rows'] if x['sku'] == 'S1')
        self.assertAlmostEqual(r['drift_30d'], 1.0, places=3)   # 8.00 vs 7.00
        self.assertAlmostEqual(r['drift_7d'], 0.0, places=3)    # flat recently


class EdgeCaseTests(TestCase):
    def test_no_data_returns_has_data_false(self):
        d = fba_intel.compute('uk', 30)
        self.assertFalse(d['has_data'])
        self.assertEqual(d['rows'], [])

    def test_marketplace_isolation(self):
        fee('S1', ANCHOR, 10, 80.0, mp='usa')
        fee('S1', ANCHOR, 10, 20.0, mp='uk')
        usa = fba_intel.compute('usa', 7, anchor=ANCHOR)
        self.assertAlmostEqual(
            next(r for r in usa['rows'] if r['sku'] == 'S1')['current_fee'],
            8.0, places=3)

    def test_sku_with_no_previous_window_has_no_impact(self):
        """A new SKU must not read as a huge saving/cost."""
        fee('NEW', ANCHOR, 10, 80.0)
        d = fba_intel.compute('usa', 7, anchor=ANCHOR)
        r = next(x for x in d['rows'] if x['sku'] == 'NEW')
        self.assertIsNone(r['previous_fee'])
        self.assertIsNone(r['fee_delta'])
        self.assertEqual(r['net_impact'], 0.0)

    def test_duplicate_sku_day_is_prevented_by_the_database(self):
        """Double-counting is impossible: (marketplace, sku, date) is unique,
        and the ingest upserts on that key. Verified rather than assumed."""
        from django.db import IntegrityError, transaction

        fee('S1', ANCHOR, 5, 25.0)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                fee('S1', ANCHOR, 5, 35.0)

        d = fba_intel.compute('usa', 7, anchor=ANCHOR)
        r = next(x for x in d['rows'] if x['sku'] == 'S1')
        self.assertEqual(r['billed_units'], 5)
        self.assertAlmostEqual(r['current_fee'], 5.0, places=3)

    def test_recomputation_is_idempotent(self):
        fee('S1', ANCHOR, 10, 80.0)
        a = fba_intel.compute('usa', 7, anchor=ANCHOR)
        b = fba_intel.compute('usa', 7, anchor=ANCHOR)
        self.assertEqual(a['position'], b['position'])
        self.assertEqual(a['rows'], b['rows'])


class CorrelationTests(TestCase):
    def test_insufficient_points_reports_insufficient(self):
        self.assertEqual(
            fba_intel._inventory_signal(-0.9, 0.5, points=2), 'insufficient_data')

    def test_fee_up_while_cover_down_is_flagged(self):
        self.assertEqual(
            fba_intel._inventory_signal(-0.8, 0.5, points=10), 'fee_up_cover_down')

    def test_weak_correlation_is_no_clear_relationship(self):
        self.assertEqual(
            fba_intel._inventory_signal(-0.1, 0.5, points=10),
            'no_clear_relationship')

    def test_flat_series_returns_none_not_a_fake_correlation(self):
        self.assertIsNone(fba_intel._pearson([1, 1, 1], [3, 2, 1]))

    def test_missing_inventory_does_not_break_compute(self):
        fee('S1', ANCHOR, 10, 80.0)
        d = fba_intel.compute('usa', 7, anchor=ANCHOR)   # no InventorySnapshot
        r = next(x for x in d['rows'] if x['sku'] == 'S1')
        self.assertIsNone(r['current_days_cover'])
        self.assertEqual(r['inventory_signal'], 'insufficient_data')

    def test_inventory_is_read_when_present(self):
        p = Product.objects.create(marketplace='usa', sku='S1', asin='B01',
                                   title='T', category='C')
        InventorySnapshot.objects.create(product=p, date=ANCHOR,
                                         afn_fulfillable=42,
                                         days_cover=Decimal('7.5'))
        fee('S1', ANCHOR, 10, 80.0)
        d = fba_intel.compute('usa', 7, anchor=ANCHOR)
        r = next(x for x in d['rows'] if x['sku'] == 'S1')
        self.assertEqual(r['current_inventory'], 42)
        self.assertAlmostEqual(r['current_days_cover'], 7.5, places=2)
