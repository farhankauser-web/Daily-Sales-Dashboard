"""
Import a Seller Central Unified Transaction (Date-Range Transaction) CSV into
monthly P&L line actuals — the single authoritative source for the P&L.

Usage:
    manage.py import_unified_txn --marketplace usa --month 2026-05 --file /path/to.csv
"""
from __future__ import annotations

from datetime import date
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Import a Unified Transaction CSV → monthly P&L actuals.'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--month', required=True, help='YYYY-MM')
        parser.add_argument('--file', required=True)

    def handle(self, marketplace, month, file, **_):
        from apps.dashboard.unified_txn_importer import import_unified_csv_bytes
        y, m = month.split('-')[:2]
        month_d = date(int(y), int(m), 1)
        with open(file, 'rb') as fh:
            data = fh.read()
        res = import_unified_csv_bytes(
            file_bytes=data, original_filename=file.split('/')[-1],
            marketplace=marketplace, month=month_d, user=None)
        self.stdout.write(self.style.SUCCESS(res['message']))
        if res.get('missing_cogs'):
            self.stdout.write(self.style.WARNING(
                f'{len(res["missing_cogs"])} SKUs sold without COGS: '
                + ', '.join(res['missing_cogs'][:10])))
