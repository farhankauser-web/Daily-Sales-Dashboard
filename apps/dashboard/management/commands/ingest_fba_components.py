"""
Management command: ingest_fba_components

P0.2 — FBA Fee Intelligence Phase 2 Ingest
Pulls component-level FBA fee data from Amazon Data Kiosk (economics query)
and persists to FbaFeeComponent, applying data hazard filters (K1, K2, K3).

Usage:
    python manage.py ingest_fba_components --marketplace usa --start-date 2026-01-01 --end-date 2026-08-19
    python manage.py ingest_fba_components --marketplace uk --days 7  # last 7 days
    python manage.py ingest_fba_components --all-marketplaces --days 1  # daily incremental
"""
import json
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.amazon_api.models import AmazonAPIConfig
from apps.amazon_api.services import SPAPIClient
from apps.dashboard.models import FbaFeeComponent, DataKioskIngestLog

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Ingest FBA fee components from Data Kiosk economics query'

    def add_arguments(self, parser):
        parser.add_argument(
            '--marketplace', type=str, default='usa',
            help='Marketplace code (usa, uk, de, ae, sa). Default: usa'
        )
        parser.add_argument(
            '--all-marketplaces', action='store_true',
            help='Process all configured marketplaces. Overrides --marketplace.'
        )
        parser.add_argument(
            '--start-date', type=str,
            help='Query start date (YYYY-MM-DD). Mutually exclusive with --days.'
        )
        parser.add_argument(
            '--end-date', type=str,
            help='Query end date (YYYY-MM-DD). Defaults to today.'
        )
        parser.add_argument(
            '--days', type=int,
            help='Fetch last N days (mutually exclusive with --start-date). Default: 1'
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Parse and validate but do not insert to database.'
        )
        parser.add_argument(
            '--note', type=str, default='',
            help='Operational note to log with this run.'
        )

    def handle(self, *args, **options):
        """Main entry point."""
        marketplace = options['marketplace']
        all_marketplaces = options['all_marketplaces']
        start_date_str = options.get('start_date')
        end_date_str = options.get('end_date')
        days = options.get('days')
        dry_run = options['dry_run']
        note = options.get('note', '')

        # ── Resolve date range ──────────────────────────────────────────────
        end_date = self._parse_date(end_date_str) or date.today()

        if start_date_str and days:
            raise CommandError('--start-date and --days are mutually exclusive')

        if start_date_str:
            start_date = self._parse_date(start_date_str)
        elif days:
            start_date = end_date - timedelta(days=days - 1)
        else:
            # Default: last 1 day
            start_date = end_date

        # ── Resolve marketplace list ────────────────────────────────────────
        if all_marketplaces:
            configs = AmazonAPIConfig.objects.filter(is_active=True)
        else:
            configs = AmazonAPIConfig.objects.filter(marketplace=marketplace, is_active=True)

        if not configs.exists():
            raise CommandError(f'No active API config for marketplace: {marketplace}')

        # ── Process each marketplace ────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(
            f'Ingesting FBA components: {start_date} → {end_date}\n'))

        total_inserted = 0
        total_filtered = 0

        for config in configs:
            self.stdout.write(f'\n▸ {config.marketplace.upper()} — ', ending='')
            self.stdout.flush()

            try:
                inserted, filtered = self._ingest_one_marketplace(
                    config, start_date, end_date, dry_run, note
                )
                total_inserted += inserted
                total_filtered += filtered
                self.stdout.write(self.style.SUCCESS(
                    f'{inserted} inserted, {filtered} filtered\n'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'ERROR: {e}\n'))
                logger.exception('Ingest failed for %s', config.marketplace)

        self.stdout.write(self.style.SUCCESS(
            f'\n✓ Complete: {total_inserted} inserted, {total_filtered} filtered\n'))

    def _ingest_one_marketplace(self, config, start_date, end_date, dry_run, note):
        """Ingest components for one marketplace."""
        marketplace = config.marketplace

        # ── create/begin log entry ──────────────────────────────────────────
        log = DataKioskIngestLog.objects.create(
            marketplace=marketplace,
            fee_type='FBA_FULFILLMENT_FEE',
            query_start_date=start_date,
            query_end_date=end_date,
            status='submitted',
            triggered_by='management_command',
            note=note,
        )
        logger.info(
            'Ingest log created: %s (%s–%s)',
            marketplace, start_date, end_date
        )

        try:
            # ── Call Data Kiosk API (P0.1) ──────────────────────────────────
            client = SPAPIClient(config)

            self.stdout.write('querying… ', ending='')
            self.stdout.flush()

            response = client.get_fba_economics(
                start_date=start_date,
                end_date=end_date,
                marketplace_id=config.marketplace_id,
            )

            log.query_id = response.get('queryId', '')
            log.status = 'parsing'
            log.save(update_fields=['query_id', 'status'])

            # ── Parse and filter JSONL ──────────────────────────────────────
            self.stdout.write('parsing… ', ending='')
            self.stdout.flush()

            rows = self._parse_kiosk_response(response)
            rows_received = len(rows)
            log.rows_received = rows_received

            # ── Apply data hazards filters (K1, K2, K3) ────────────────────
            components, stats = self._filter_and_prepare(marketplace, rows)

            log.rows_filtered_amzn = stats['filtered_amzn']
            log.rows_marked_adjustment = stats['marked_adjustment']
            log.rows_skipped_empty = stats['skipped_empty']
            log.rows_inserted = len(components)

            if dry_run:
                self.stdout.write('(dry-run, not persisting)')
                log.status = 'ok'
                log.completed_at = timezone.now()
                log.duration_seconds = 0
                log.note = (log.note or '') + ' [DRY_RUN]'
                log.save()
                return len(components), stats['total_filtered']

            # ── Persist to database ─────────────────────────────────────────
            self.stdout.write('ingesting… ', ending='')
            self.stdout.flush()

            with transaction.atomic():
                FbaFeeComponent.objects.bulk_create(
                    components,
                    batch_size=500,
                    update_conflicts=True,
                    update_fields=[
                        'quantity', 'amount', 'amount_per_unit',
                        'promotion_amount', 'tax_amount', 'currency_code',
                        'is_adjustment', 'updated_at'
                    ],
                    unique_fields=[
                        'marketplace', 'msku', 'date', 'fee_type', 'component_name'
                    ]
                )
                logger.info(
                    'Persisted %d components for %s',
                    len(components), marketplace
                )

            log.status = 'ok'
            log.completed_at = timezone.now()
            log.save(update_fields=['status', 'completed_at'])

            return len(components), stats['total_filtered']

        except Exception as e:
            log.status = 'error'
            log.error_message = str(e)
            log.error_stage = 'ingest'
            log.completed_at = timezone.now()
            log.save(update_fields=['status', 'error_message', 'error_stage', 'completed_at'])
            raise CommandError(f'Ingest failed for {marketplace}: {e}')

    def _parse_kiosk_response(self, response: dict) -> list[dict]:
        """
        Extract JSONL rows from Data Kiosk response.

        Amazon returns a presigned S3 URL; caller must have fetched it.
        Response should be {'records': [jsonl_line_1, jsonl_line_2, ...]}.
        """
        records = response.get('records', [])
        if isinstance(records, str):
            # Sometimes Amazon returns raw JSONL as a single string
            records = [
                json.loads(line)
                for line in records.strip().split('\n')
                if line.strip()
            ]
        return records

    def _filter_and_prepare(self, marketplace: str, rows: list[dict]):
        """
        Apply K1, K2, K3 filters and prepare FbaFeeComponent objects.

        K1: Exclude amzn.* MSKUs (Amazon-generated grade-and-resell)
        K2: Guard zero-quantity and negative-amount rows (store but mark adjustment)
        K3: Skip empty-fee rows (no fees charged)

        Returns: (components_list, stats_dict)
        """
        components = []
        stats = {
            'filtered_amzn': 0,
            'marked_adjustment': 0,
            'skipped_empty': 0,
            'total_filtered': 0,
        }

        for row in rows:
            # ── Extract fields per Section B (Data Kiosk schema) ──────────────
            try:
                # Charge-level data
                charge_date = row.get('date')
                charge_msku = row.get('productId')
                charge_amount = Decimal(str(row.get('amount', 0) or 0))
                charge_promotion = Decimal(str(row.get('promotionAmount', 0) or 0))
                charge_tax = Decimal(str(row.get('taxAmount', 0) or 0))
                charge_qty = float(row.get('quantity') or 0)
                charge_currency = row.get('currencyCode', 'USD')

                # Fee type
                fee_type = row.get('feeType', 'FBA_FULFILLMENT_FEE')

                # Components (nested array)
                fee_components = row.get('components', []) or []

            except (KeyError, ValueError, TypeError) as e:
                logger.warning('Skipped malformed row: %s', e)
                continue

            # ── K3: Skip empty-fee rows ────────────────────────────────────
            if not fee_components or len(fee_components) == 0:
                stats['skipped_empty'] += 1
                stats['total_filtered'] += 1
                continue

            # ── K1: Exclude amzn.* MSKUs ──────────────────────────────────
            if charge_msku and charge_msku.startswith('amzn.'):
                stats['filtered_amzn'] += 1
                stats['total_filtered'] += 1
                continue

            # ── Process each component ──────────────────────────────────────
            for comp in fee_components:
                try:
                    comp_name = comp.get('name', '')
                    comp_amount = Decimal(str(comp.get('amount', 0) or 0))
                    comp_qty = float(comp.get('quantity') or 0)
                    comp_amount_per_unit = Decimal(str(comp.get('amountPerUnit', 0) or 0))
                    comp_promotion = Decimal(str(comp.get('promotionAmount', 0) or 0))
                    comp_tax = Decimal(str(comp.get('taxAmount', 0) or 0))

                except (KeyError, ValueError, TypeError):
                    continue

                # ── K2: Mark zero-quantity or negative-amount as adjustment ─
                is_adjustment = (comp_qty == 0.0) or (comp_amount < 0)
                if is_adjustment:
                    stats['marked_adjustment'] += 1

                # ── Create component record ─────────────────────────────────
                component = FbaFeeComponent(
                    marketplace=marketplace,
                    msku=charge_msku or '',
                    date=charge_date,
                    fee_type=fee_type,
                    component_name=comp_name,
                    quantity=comp_qty,
                    amount=comp_amount,
                    amount_per_unit=comp_amount_per_unit,
                    promotion_amount=comp_promotion,
                    tax_amount=comp_tax,
                    currency_code=charge_currency,
                    is_amzn_generated=False,  # K1 already filtered
                    is_adjustment=is_adjustment,
                    query_date_range_start=None,  # populated by caller if needed
                    query_date_range_end=None,
                )
                components.append(component)

        return components, stats

    @staticmethod
    def _parse_date(date_str: str | None) -> date | None:
        """Parse YYYY-MM-DD string to date object."""
        if not date_str:
            return None
        try:
            return date.fromisoformat(date_str)
        except ValueError:
            raise CommandError(f'Invalid date format: {date_str!r} (use YYYY-MM-DD)')
