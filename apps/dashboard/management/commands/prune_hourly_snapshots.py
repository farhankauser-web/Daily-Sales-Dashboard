"""
prune_hourly_snapshots — Weekly cleanup of stale hourly rows.

HourlyMetricSnapshot and HourlySkuSnapshot grow ~720 + ~5k rows per marketplace
per month. We keep the last N days (default 30) for the Hourly Patterns page
and delete everything older.

Usage:
    python manage.py prune_hourly_snapshots                # 30-day retention
    python manage.py prune_hourly_snapshots --keep-days 90 # custom retention
    python manage.py prune_hourly_snapshots --dry-run
"""
from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from apps.dashboard.models import HourlyMetricSnapshot, HourlySkuSnapshot


class Command(BaseCommand):
    help = "Delete HourlyMetricSnapshot + HourlySkuSnapshot rows older than retention window."

    def add_arguments(self, parser):
        parser.add_argument('--keep-days', type=int, default=30,
                            help='Retention window in days (default 30)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Count rows that would be deleted, do not delete')

    def handle(self, *args, **opts):
        cutoff = date.today() - timedelta(days=opts['keep_days'])
        self.stdout.write(f'Pruning hourly snapshots older than {cutoff} '
                          f'(keep {opts["keep_days"]} days)')

        m_qs = HourlyMetricSnapshot.objects.filter(date__lt=cutoff)
        s_qs = HourlySkuSnapshot.objects.filter(date__lt=cutoff)
        m_count, s_count = m_qs.count(), s_qs.count()

        if opts['dry_run']:
            self.stdout.write(self.style.NOTICE(
                f'DRY-RUN — would delete {m_count} metric rows + {s_count} SKU rows'
            ))
            return

        m_deleted, _ = m_qs.delete()
        s_deleted, _ = s_qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {m_deleted} metric rows + {s_deleted} SKU rows.'
        ))
