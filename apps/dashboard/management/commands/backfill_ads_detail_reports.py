"""
backfill_ads_detail_reports — One-shot orchestrator for Phase 1 backfill.

Submits all 13 detail report kinds for the last N days (default 30), then loops
polling until every (date, kind) is either 'ok', 'empty_from_amazon', or 'failed'.
Finally runs `compute_campaign_profit --backfill-window N` so CampaignProfitDaily
and CampaignSearchTermSummary are populated when the command exits.

This is a convenience wrapper around the production commands that the cron
schedule uses every day. The cron runs `ingest_ads_detail_reports` every 15
minutes for the natural T-1 ingestion; this command is for the one-time 30-day
backfill (or future re-backfill if you need to re-ingest historical data).

Usage:
    # First-time 30-day backfill
    python manage.py backfill_ads_detail_reports --days 30

    # Validate first with 7 days
    python manage.py backfill_ads_detail_reports --days 7

    # Restart polling after a crash — picks up pending report_ids automatically
    python manage.py backfill_ads_detail_reports --days 30 --skip-submit

Time budget:
    Amazon typically completes detail reports within 5-15 minutes; an account
    with many campaigns can take longer. The default --max-poll-minutes 90 caps
    the wait so a stuck report doesn't block the whole command. Any reports
    still pending at that time will be re-attempted by the cron later.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = ('One-shot backfill orchestrator: submits 30 days of detail reports, '
            'polls until done, then computes CampaignProfitDaily for the window.')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; default: all active.')
        parser.add_argument('--days', type=int, default=30,
                            help='Trailing days to backfill (default 30).')
        parser.add_argument('--poll-interval', type=int, default=60,
                            help='Seconds between polling sweeps (default 60).')
        parser.add_argument('--max-poll-minutes', type=int, default=90,
                            help='Stop polling after this many minutes (default 90).')
        parser.add_argument('--skip-submit', action='store_true',
                            help='Skip initial submit phase; just poll pending reports.')
        parser.add_argument('--skip-compute', action='store_true',
                            help='Skip the final compute_campaign_profit step.')
        parser.add_argument('--report-kinds', default=None,
                            help='Comma-separated list; default: all 13 kinds.')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.ads_detail_reports import REPORT_CONFIGS
        from apps.dashboard.completeness import DETAIL_SOURCES
        from apps.dashboard.models import AdsDataSyncLog

        days = max(1, opts['days'])
        if opts['marketplace']:
            mps = [opts['marketplace']]
        else:
            mps = list(AmazonAPIConfig.objects.filter(is_active=True)
                       .values_list('marketplace', flat=True))
        if not mps:
            self.stderr.write(self.style.WARNING('No active marketplaces.'))
            return

        report_kinds = opts['report_kinds']

        # Use the FIRST marketplace's timezone to compute the date window; for
        # multi-marketplace runs the date arithmetic is identical (the only
        # variance is what "yesterday" means locally, which `ingest` handles
        # itself per-marketplace).
        ref_mp = mps[0]
        tz = ZoneInfo(settings.AMAZON_MARKETPLACES.get(ref_mp, {})
                      .get('timezone', settings.TIME_ZONE))
        anchor = datetime.now(tz=tz).date() - timedelta(days=1)
        oldest = anchor - timedelta(days=days - 1)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n📦  Backfill window: {oldest} → {anchor}  ({days} day(s))'
            f' · {len(mps)} marketplace(s) · {len(REPORT_CONFIGS)} report kind(s)'))

        # ── Phase 1: SUBMIT ─────────────────────────────────────────────────
        if not opts['skip_submit']:
            self.stdout.write(self.style.HTTP_INFO('\n→ Phase 1 / Submitting reports...\n'))
            for mp in mps:
                ingest_args = dict(
                    marketplace=mp,
                    date=str(anchor),
                    rewind=days - 1,
                    submit_spacing=1.0,
                )
                if report_kinds:
                    ingest_args['report_kinds'] = report_kinds
                call_command('ingest_ads_detail_reports', **ingest_args)
        else:
            self.stdout.write(self.style.WARNING('→ Skipping submit phase (--skip-submit).'))

        # ── Phase 2: POLL until done or timeout ─────────────────────────────
        self.stdout.write(self.style.HTTP_INFO('\n→ Phase 2 / Polling until reports finish...\n'))
        deadline = datetime.now() + timedelta(minutes=opts['max_poll_minutes'])
        sources = list(DETAIL_SOURCES)
        if report_kinds:
            wanted_sources = {f'{k.strip()}_daily' for k in report_kinds.split(',')}
            sources = [s for s in sources if s in wanted_sources]

        sweep = 0
        while True:
            sweep += 1
            pending_count = AdsDataSyncLog.objects.filter(
                marketplace__in=mps,
                date__gte=oldest,
                date__lte=anchor,
                source__in=sources,
                status='pending',
            ).count()

            if pending_count == 0:
                self.stdout.write(self.style.SUCCESS(
                    f'  ✓ Sweep {sweep}: 0 pending — all reports settled.'))
                break

            self.stdout.write(self.style.WARNING(
                f'  ⏳ Sweep {sweep}: {pending_count} pending report(s); '
                f'sleeping {opts["poll_interval"]}s before re-poll...'))

            if datetime.now() >= deadline:
                self.stderr.write(self.style.ERROR(
                    f'  ✗ Reached --max-poll-minutes={opts["max_poll_minutes"]} '
                    f'with {pending_count} still pending. Re-run with --skip-submit '
                    f'to continue polling later.'))
                return

            time.sleep(opts['poll_interval'])

            # Each ingest call resumes pending IDs (no fresh submits because the
            # dates already have AdsDataSyncLog rows).
            for mp in mps:
                ingest_args = dict(
                    marketplace=mp,
                    date=str(anchor),
                    rewind=days - 1,
                    submit_spacing=0.0,  # resume-only — no need to space out
                )
                if report_kinds:
                    ingest_args['report_kinds'] = report_kinds
                try:
                    call_command('ingest_ads_detail_reports', **ingest_args)
                except Exception as e:
                    logger.exception('ingest sweep %d failed for %s: %s', sweep, mp, e)

        # ── Phase 3: COMPUTE ────────────────────────────────────────────────
        if not opts['skip_compute']:
            self.stdout.write(self.style.HTTP_INFO('\n→ Phase 3 / Computing campaign profit...\n'))
            for mp in mps:
                call_command(
                    'compute_campaign_profit',
                    marketplace=mp,
                    date=str(anchor),
                    backfill_window=days,
                )

        self.stdout.write(self.style.SUCCESS(
            f'\n✅  Backfill complete: {oldest} → {anchor} '
            f'({days} day(s) × {len(REPORT_CONFIGS)} kinds × {len(mps)} marketplace(s)).\n'))
