"""
Sync one month's Management P&L from Amazon's Finances API (provisional).
Use for months where the Seller Central Unified Transaction report hasn't been
uploaded yet. A later manual upload overrides with the book-exact figure.

Usage:
    manage.py sync_pnl_finances --marketplace usa --month 2026-06
"""
from __future__ import annotations

import json
from datetime import date
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Sync a month of P&L from the Amazon Finances API (provisional).'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--month', required=True, help='YYYY-MM')

    def handle(self, marketplace, month, **_):
        from apps.dashboard.finances_importer import sync_finances_month
        y, m = month.split('-')[:2]
        res = sync_finances_month(marketplace, date(int(y), int(m), 1))
        self.stdout.write(json.dumps(res, indent=2, default=str))
        self.stdout.write(self.style.SUCCESS('Done.'))
