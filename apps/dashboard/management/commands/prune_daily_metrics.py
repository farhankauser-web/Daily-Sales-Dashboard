"""
prune_daily_metrics — Weekly cleanup of DailyMetric + DailySkuSnapshot.

Keeps the last N days (default 90) and deletes everything older. Designed to
run weekly via cron — same cadence as prune_hourly_snapshots.

Usage:
    python manage.py prune_daily_metrics
    python manage.py prune_daily_metrics --keep-days 180
    python manage.py prune_daily_metrics --dry-run
"""
from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from apps.dashboard.models import DailyMetric, DailySkuSnapshot


class Command(BaseCommand):
    help = "Delete DailyMetric + DailySkuSnapshot rows older than retention window."

    def add_arguments(self, parser):
        parser.add_argument('--keep-days', type=int, default=90,
                            help='Retention window in days (default 90)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Count rows that would be deleted; do not delete')

    def handle(self, *args, **opts):
        cutoff = date.today() - timedelta(days=opts['keep_days'])
        self.stdout.write(f'Pruning DailyMetric + DailySkuSnapshot older than {cutoff} '
                          f'(keep {opts["keep_days"]} days)')

        m_qs = DailyMetric.objects.filter(date__lt=cutoff)
        s_qs = DailySkuSnapshot.objects.filter(date__lt=cutoff)
        m_count, s_count = m_qs.count(), s_qs.count()

        if opts['dry_run']:
            self.stdout.write(self.style.NOTICE(
                f'DRY-RUN — would delete {m_count} DailyMetric rows + {s_count} DailySkuSnapshot rows'
            ))
            return

        m_deleted, _ = m_qs.delete()
        s_deleted, _ = s_qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {m_deleted} DailyMetric rows + {s_deleted} DailySkuSnapshot rows.'
        ))
