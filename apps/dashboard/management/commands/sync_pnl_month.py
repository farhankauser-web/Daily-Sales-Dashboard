"""
sync_pnl_month — build the Management P&L for a month from the Reports
Repository (Date Range Transaction report).

WHY THIS EXISTS
    The P&L used to be assembled from Settlement Flat File V2 by
    rebuild_settlement_month. Measured against Amazon's own Date Range Summary
    statement for USA July 2026, settlement overstated revenue by 22.2% and
    understated promotional cost by 89%:

        gross_sales   settlement 1,361,983.70   Amazon 1,114,203.14
        promo         settlement     2,425.60   Amazon    22,117.54

    The Date Range Transaction report is the same data Amazon builds that
    statement from. unified_txn_importer parses it and reconciles EXACTLY —
    contribution before COGS of 324,440.90 against Amazon's 324,440.90,
    delta -0.00, every order-level line to the cent.

    Settlement keeps cash reconciliation and per-SKU FBA fee drift, where it
    is accurate. It no longer writes a single P&L line.

WHEN A MONTH IS COMPLETE
    This report is posted-date based and includes deferred transactions, so a
    month is complete on the 1st of the following month. It does not wait for
    settlement cycles, which close between the 3rd and the 12th depending on
    marketplace — that difference is the whole point of the change.

IF THE REPORT DOES NOT EXIST YET
    Amazon blocks createReport for this type ("report type 1202 is not
    allowed"), so only reports that already exist can be downloaded. Amazon
    auto-generates a daily one per marketplace; monthly ones are generated in
    Seller Central. This command says so plainly rather than writing a partial
    month — a half-built P&L is worse than a visibly stale one.

USAGE
    manage.py sync_pnl_month --marketplace usa --month 2026-07
    manage.py sync_pnl_month --all-marketplaces --month 2026-08
    manage.py sync_pnl_month --all-marketplaces              # previous month
    manage.py sync_pnl_month --marketplace usa --month 2026-07 --dry-run
"""
from __future__ import annotations

import calendar
from datetime import date

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ('Build a month of Management P&L from the Reports Repository '
            '(Date Range Transaction report).')

    def add_arguments(self, p):
        p.add_argument('--marketplace', default='usa')
        p.add_argument('--all-marketplaces', action='store_true',
                       help='Every active marketplace; overrides --marketplace.')
        p.add_argument('--month', help='YYYY-MM (default: previous month)')
        p.add_argument('--dry-run', action='store_true',
                       help='Parse and print the lines; write nothing.')

    def handle(self, marketplace, all_marketplaces, month, dry_run, **_):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import SPAPIClient
        from apps.dashboard.unified_txn_importer import (
            import_unified_csv_bytes, parse_unified_csv)

        if month:
            try:
                y, m = (int(x) for x in month.split('-')[:2])
                month_d = date(y, m, 1)
            except (ValueError, TypeError):
                raise CommandError('--month must be YYYY-MM')
        else:
            today = date.today()
            month_d = date(today.year, today.month, 1)
            month_d = (month_d.replace(year=month_d.year - 1, month=12)
                       if month_d.month == 1
                       else month_d.replace(month=month_d.month - 1))

        last_day = calendar.monthrange(month_d.year, month_d.month)[1]
        start_local = month_d
        end_local = date(month_d.year, month_d.month, last_day)

        if all_marketplaces:
            configs = list(AmazonAPIConfig.objects.filter(is_active=True))
        else:
            configs = list(AmazonAPIConfig.objects.filter(
                marketplace=marketplace, is_active=True))
        if not configs:
            raise CommandError(f'No active API config for: {marketplace}')

        self.stdout.write(self.style.SUCCESS(
            f'P&L from Reports Repository — {month_d:%Y-%m} '
            f'({start_local} to {end_local})\n'))

        ok = failed = 0
        for cfg in configs:
            mp = cfg.marketplace
            self.stdout.write(f'▸ {mp.upper()} — ', ending='')
            self.stdout.flush()
            try:
                client = SPAPIClient(cfg)
                raw = client.fetch_date_range_transaction_report(
                    start_local, end_local)
            except LookupError as exc:
                self.stdout.write(self.style.WARNING(f'no report yet\n    {exc}'))
                failed += 1
                continue
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f'fetch failed — {type(exc).__name__}: {exc}'))
                failed += 1
                continue

            self.stdout.write(f'{len(raw):,} bytes → ', ending='')
            self.stdout.flush()

            try:
                if dry_run:
                    res = parse_unified_csv(raw, marketplace=mp, month=month_d)
                    lines = res['lines']
                    self.stdout.write(
                        f'{res["rows_parsed"]:,} rows, '
                        f'{res["order_units"]:,} order units (DRY RUN)')
                    for k in sorted(lines):
                        self.stdout.write(
                            f'    {k:<26}{lines[k]["amount"]:>15,.2f}')
                    if res.get('missing_cogs'):
                        self.stdout.write(self.style.WARNING(
                            f'    {len(res["missing_cogs"])} SKU(s) sold '
                            f'without COGS'))
                else:
                    res = import_unified_csv_bytes(
                        file_bytes=raw,
                        original_filename=f'daterange_{mp}_{month_d:%Y-%m}.csv',
                        marketplace=mp, month=month_d, user=None)
                    self.stdout.write(self.style.SUCCESS(res['message']))
                    if res.get('missing_cogs'):
                        self.stdout.write(self.style.WARNING(
                            f'    {len(res["missing_cogs"])} SKU(s) sold '
                            f'without COGS: '
                            + ', '.join(res['missing_cogs'][:8])))
                ok += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f'import failed — {type(exc).__name__}: {exc}'))
                failed += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'DONE: {ok} imported, {failed} failed'))
