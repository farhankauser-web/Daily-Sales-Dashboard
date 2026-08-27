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

            # Use the CLIENT's resolved marketplace id, not the raw config
            # field. AmazonAPIConfig.marketplace_id is blank=True and is in
            # fact empty for 'ae' and 'sa'; SPAPIClient.__init__ resolves it
            # as `config.marketplace_id or settings.AMAZON_MARKETPLACES[..]`.
            # Reading the config field directly bypassed that fallback and
            # sent `marketplaceIds: [""]` to Data Kiosk for UAE and KSA — a
            # query that can only ever come back empty. Every other caller in
            # the codebase already goes through the resolved value.
            if not client.mp_id:
                raise CommandError(
                    f'No marketplace id for "{marketplace}": it is blank on '
                    f'AmazonAPIConfig and absent from '
                    f'settings.AMAZON_MARKETPLACES.')

            response = client.get_fba_economics(
                start_date=start_date,
                end_date=end_date,
                marketplace_id=client.mp_id,
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

    # ── Data Kiosk money helper ─────────────────────────────────────────────
    @staticmethod
    def _money(detail: dict, key: str) -> tuple[Decimal, str]:
        """
        Every monetary field in the economics schema is an OBJECT:

            "amount": {"amount": 3.5, "currencyCode": "USD"}

        never a bare number. The previous parser did Decimal(str(row['amount']))
        straight onto that dict, which would have produced garbage even if the
        rest of the walk had been correct. Returns (Decimal, currency) and
        tolerates null, a missing key, or a scalar.
        """
        v = (detail or {}).get(key)
        if isinstance(v, dict):
            return Decimal(str(v.get('amount') or 0)), (v.get('currencyCode') or '')
        if v is None:
            return Decimal('0'), ''
        return Decimal(str(v)), ''

    def _filter_and_prepare(self, marketplace: str, rows: list[dict]):
        """
        Walk Data Kiosk economics rows into FbaFeeComponent objects.

        WHAT WAS WRONG BEFORE
            This method read a FLAT row:

                row['date'], row['productId'], row['amount'], row['components']

            The economics schema returns a NESTED one, and none of those keys
            exist on it:

                { startDate, endDate, marketplaceId, msku, fnsku,
                  childAsin, parentAsin,
                  sales { ... },
                  fees [ { feeTypeName,
                           charges [ { identifier, startDate, endDate,
                                       properties [],
                                       aggregatedDetail { quantity,
                                           amount/amountPerUnit/promotionAmount/
                                           taxAmount/totalAmount
                                             { amount, currencyCode } },
                                       components [ { name, properties,
                                                      aggregatedDetail {...} } ]
                                     } ] } ] }

            So row.get('components') was None on every row, K3 fired, and the
            first live run discarded all 6,965 rows it had just fetched
            (0 inserted, 6965 filtered). The endpoint fix made the data arrive;
            this made it arrive nowhere.

        COMPONENTS ARE OPTIONAL
            build_economics_query sends includeComponentsForFeeTypes:
            [FBA_FULFILLMENT_FEE], so ONLY fulfilment charges carry a component
            breakdown. In a one-day USA sample, 577 charges: 193 had components
            (all FbaFulfilmentFee), 384 had components: null — ReferralFee,
            DisposalFee, storage, liquidation, reimbursement and the rest.

            Storing only components would therefore drop two thirds of the fees
            Amazon billed and the totals would never reconcile against the
            settlement. Where a charge has no breakdown we store the charge
            itself, named by its identifier (e.g. 'DisposalFee_Standard'), so
            every billed amount lands exactly once.

        DUPLICATES ARE SUMMED, NEVER DROPPED
            The table is unique on
            (marketplace, msku, date, fee_type, component_name) and the caller
            uses bulk_create(update_conflicts=True), which OVERWRITES on
            collision. If one row ever produced two charges sharing that tuple,
            the second would silently replace the first and the day would
            under-report — the same failure mode that inflated the P&L for
            months. So collisions are accumulated here, in the parser, where
            they are visible and counted, and what reaches the DB is already
            unique.

        Returns: (components_list, stats_dict)
        """
        stats = {
            'filtered_amzn':      0,   # K1 — amzn.* grade-and-resell MSKUs
            'marked_adjustment':  0,   # K2 — qty 0 or negative amount
            'skipped_empty':      0,   # K3 — row carried no fees at all
            'total_filtered':     0,
            'charges_seen':       0,
            'charges_no_breakdown': 0,
            'duplicates_merged':  0,
            'rows_no_msku':       0,
        }

        # keyed by the table's unique tuple so nothing can collide downstream
        merged: dict[tuple, FbaFeeComponent] = {}

        for row in rows:
            msku = (row.get('msku') or '').strip()
            row_date = row.get('startDate')
            fees = row.get('fees') or []

            # ── K3: no fees billed for this MSKU/day ────────────────────────
            # Expected and dominant: most SKUs are simply not charged on a
            # given day (1,767 of 1,990 in the USA sample). Not an error.
            if not fees:
                stats['skipped_empty'] += 1
                stats['total_filtered'] += 1
                continue

            # ── K1: Amazon grade-and-resell MSKUs ───────────────────────────
            # 1,510 of 1,990 rows in the sample. Excluded from impact analysis
            # by business rule — they are Amazon's inventory, not ours.
            if msku.startswith('amzn.'):
                stats['filtered_amzn'] += 1
                stats['total_filtered'] += 1
                continue

            if not msku:
                stats['rows_no_msku'] += 1
                stats['total_filtered'] += 1
                continue

            for fee in fees:
                fee_type = (fee.get('feeTypeName') or '').strip()

                for charge in (fee.get('charges') or []):
                    stats['charges_seen'] += 1

                    charge_date = charge.get('startDate') or row_date
                    if not charge_date:
                        continue

                    comps = charge.get('components') or []
                    if comps:
                        units = [(c.get('name') or '', c.get('aggregatedDetail') or {})
                                 for c in comps]
                    else:
                        # No breakdown for this fee type — keep the charge so
                        # the money is not lost, named by its identifier.
                        stats['charges_no_breakdown'] += 1
                        units = [(
                            (charge.get('identifier') or fee_type or 'Charge'),
                            charge.get('aggregatedDetail') or {},
                        )]

                    for comp_name, detail in units:
                        amount,   cur_a = self._money(detail, 'amount')
                        per_unit, cur_b = self._money(detail, 'amountPerUnit')
                        promo,    _     = self._money(detail, 'promotionAmount')
                        tax,      _     = self._money(detail, 'taxAmount')
                        try:
                            qty = float(detail.get('quantity') or 0)
                        except (TypeError, ValueError):
                            qty = 0.0

                        # ── K2: adjustment rows ─────────────────────────────
                        # quantity 0 or a negative amount means a
                        # reclassification between components, not a new
                        # charge. Stored, flagged, excluded from rate
                        # derivation downstream.
                        is_adjustment = (qty == 0.0) or (amount < 0)
                        if is_adjustment:
                            stats['marked_adjustment'] += 1

                        # model caps: fee_type 64, component_name 48
                        ft = fee_type[:64]
                        cn = comp_name[:48]
                        key = (marketplace, msku, charge_date, ft, cn)

                        prior = merged.get(key)
                        if prior is None:
                            merged[key] = FbaFeeComponent(
                                marketplace=marketplace,
                                msku=msku,
                                date=charge_date,
                                fee_type=ft,
                                component_name=cn,
                                quantity=qty,
                                amount=amount,
                                amount_per_unit=per_unit,
                                promotion_amount=promo,
                                tax_amount=tax,
                                currency_code=(cur_a or cur_b or 'USD')[:4],
                                is_amzn_generated=False,   # K1 already excluded
                                is_adjustment=is_adjustment,
                                query_date_range_start=None,
                                query_date_range_end=None,
                            )
                        else:
                            # Same unique tuple twice — accumulate rather than
                            # let the DB overwrite one with the other.
                            stats['duplicates_merged'] += 1
                            prior.quantity         += qty
                            prior.amount           += amount
                            prior.promotion_amount += promo
                            prior.tax_amount       += tax
                            prior.is_adjustment     = prior.is_adjustment or is_adjustment
                            # per-unit is a rate, not a total: recompute it
                            if prior.quantity:
                                prior.amount_per_unit = (
                                    prior.amount / Decimal(str(prior.quantity))
                                )

        return list(merged.values()), stats

    @staticmethod
    def _parse_date(date_str: str | None) -> date | None:
        """Parse YYYY-MM-DD string to date object."""
        if not date_str:
            return None
        try:
            return date.fromisoformat(date_str)
        except ValueError:
            raise CommandError(f'Invalid date format: {date_str!r} (use YYYY-MM-DD)')
