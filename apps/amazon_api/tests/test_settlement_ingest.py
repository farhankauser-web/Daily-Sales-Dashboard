"""
Regression tests for settlement ingestion.

The bug these exist for: `SettlementLineActual.amount` comes back from the DB
as Decimal, the parsed settlement value is a float, and `Decimal + float`
raises TypeError. Because that line ran inside the SAME transaction.atomic()
as the SkuFeeActual writes, the exception rolled the FEE ROWS BACK as well —
so the FBA "actual fee" silently stopped updating on every single run.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.dashboard.models import SettlementLineActual, SkuFeeActual


class SettlementLineAmountAccumulationTests(TestCase):
    """The exact arithmetic that crashed, plus the accumulate-not-replace rule."""

    def test_decimal_plus_float_would_raise(self):
        """Guards the root cause: the naive form is a TypeError, not a warning."""
        amount_from_db = Decimal('10.00')
        parsed_from_report = 5.25            # float, as the parser produces
        with self.assertRaises(TypeError):
            _ = amount_from_db + parsed_from_report   # noqa: F841

    def test_amount_accumulates_across_reports(self):
        """Settlement reports are incremental — a second report ADDS."""
        row = SettlementLineActual.objects.create(
            marketplace='usa', month=date(2026, 7, 1), line_key='fba_fees',
            amount=Decimal('0'), units=0, currency='USD',
            source_note='settlement')

        for parsed_amount, parsed_units in ((10.005, 3), (5.25, 2)):
            row.amount = (row.amount or Decimal('0')) + Decimal(
                str(round(float(parsed_amount), 2)))
            row.units = (row.units or 0) + int(parsed_units)
            row.save(update_fields=['amount', 'units'])

        row.refresh_from_db()
        # 10.01 (rounded from 10.005) + 5.25 — exact, no binary-float drift.
        self.assertEqual(row.amount, Decimal('15.26'))
        self.assertEqual(row.units, 5)

    def test_rounding_goes_through_str_not_binary_float(self):
        """Decimal(float) would carry binary error; Decimal(str(...)) does not."""
        self.assertEqual(Decimal(str(round(float(0.1 + 0.2), 2))), Decimal('0.30'))


class SkuFeeActualUpsertTests(TestCase):
    """The fee rows the drift page reads."""

    def _upsert(self, sku, day, units, fee_total):
        return SkuFeeActual.objects.update_or_create(
            marketplace='usa', sku=sku, date=day,
            defaults={'units': units, 'fba_fee_total': fee_total,
                      'fee_per_unit': (fee_total / units) if units else 0},
        )

    def test_ingest_is_idempotent_per_sku_and_day(self):
        """Re-running the same report must not duplicate or double-count."""
        self._upsert('SKU-A', date(2026, 7, 10), 10, 50.0)
        self._upsert('SKU-A', date(2026, 7, 10), 10, 50.0)   # same report again

        rows = SkuFeeActual.objects.filter(marketplace='usa', sku='SKU-A')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().units, 10)
        self.assertEqual(Decimal(rows.first().fba_fee_total), Decimal('50.0000'))

    def test_fee_per_unit_is_derived_from_totals(self):
        self._upsert('SKU-B', date(2026, 7, 11), 4, 10.0)
        row = SkuFeeActual.objects.get(sku='SKU-B')
        self.assertEqual(Decimal(row.fee_per_unit), Decimal('2.5000'))

    def test_zero_units_does_not_divide_by_zero(self):
        self._upsert('SKU-C', date(2026, 7, 12), 0, 0.0)
        self.assertEqual(Decimal(SkuFeeActual.objects.get(sku='SKU-C').fee_per_unit),
                         Decimal('0'))

    def test_separate_days_are_separate_rows(self):
        """The 14-day window aggregates by posted DATE, so days must not merge."""
        self._upsert('SKU-D', date(2026, 7, 10), 5, 25.0)
        self._upsert('SKU-D', date(2026, 7, 11), 5, 30.0)
        self.assertEqual(
            SkuFeeActual.objects.filter(sku='SKU-D').count(), 2)


class DriftWindowTests(TestCase):
    """The 14-day window must follow available settlement data, not the clock."""

    def test_window_anchors_to_latest_actual_not_today(self):
        from apps.dashboard.fba_drift import compute_drift

        # Data that is weeks old — as real settlements always are.
        SkuFeeActual.objects.create(
            marketplace='usa', sku='SKU-E', date=date(2026, 6, 10),
            units=20, fba_fee_total=Decimal('100'), fee_per_unit=Decimal('5'))

        rows = compute_drift('usa', today=date(2026, 8, 17),
                             include_zero_volume=True)
        # Anchored to today the window would be empty and this SKU invisible.
        self.assertTrue(any(r.sku == 'SKU-E' for r in rows),
                        'stale-but-real settlement data must still be reported')

    def test_no_actuals_is_not_reported_as_critical_drift(self):
        """units==0 means no data — not a -100% fee collapse."""
        from apps.dashboard.fba_drift import _classify

        self.assertEqual(_classify(pct=-100.0, impact=0.0,
                                   uploaded_fee=5.0, units=0), 'no_actuals')


class SettlementDateFormatTests(TestCase):
    """Amazon localises posted-date per marketplace — all forms must parse."""

    def _parse(self, s):
        from apps.amazon_api.management.commands.ingest_settlement_reports \
            import Command
        return Command._parse_iso_date(s)

    def test_us_iso_format(self):
        self.assertEqual(self._parse('2026-07-23'), date(2026, 7, 23))

    def test_us_iso_with_time(self):
        self.assertEqual(self._parse('2026-07-23T09:46:55+00:00'),
                         date(2026, 7, 23))

    def test_uk_eu_dotted_format(self):
        """The exact string that silently dropped every UK fee row."""
        self.assertEqual(self._parse('23.07.2026'), date(2026, 7, 23))

    def test_uk_eu_dotted_with_time_and_zone(self):
        self.assertEqual(self._parse('23.07.2026 09:46:55 UTC'),
                         date(2026, 7, 23))

    def test_day_is_not_read_as_month(self):
        """13.07 must be 13 July, never 7 December."""
        self.assertEqual(self._parse('13.07.2026'), date(2026, 7, 13))

    def test_garbage_returns_none_not_exception(self):
        for bad in ('', None, 'not-a-date', '99.99.9999'):
            self.assertIsNone(self._parse(bad))
