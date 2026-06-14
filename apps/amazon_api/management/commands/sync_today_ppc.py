"""
sync_today_ppc — Hourly live-layer PPC sync for the current day.

Calls the Amazon Ads API v3 SUMMARY report (SP + SB + SD) for today and
upserts the per-campaign totals into `PPCCampaignSnapshot`. The existing
allocator's `_load_campaign_spend` then picks today's daily snapshot via
its `max(AMS, daily)` rule, so the live values automatically flow through
to the dashboard without any allocator changes.

Why SUMMARY instead of DAILY?
    Amazon's DAILY aggregate isn't built until after the day closes, so
    `backfill_ppc --today` returns 0 rows during the day. SUMMARY is
    continuously refreshed (~15-30 min) — the same backend the Ads UI uses.

Audit:
    Each successful run writes an `AdsDataSyncLog` row with
    source='sp_hourly' / 'sb_daily' / 'sd_daily' so the existing
    completeness layer recognises the source.

Usage:
    python manage.py sync_today_ppc                  # all active marketplaces
    python manage.py sync_today_ppc --marketplace usa
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = ('Pull TODAY\'s campaign-level SP/SB/SD spend via the Ads API '
            'SUMMARY report and upsert into PPCCampaignSnapshot.')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; default: all active.')
        parser.add_argument('--max-wait', type=int, default=120,
                            help='Seconds to poll for the report (default 120).')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import AdsAPIClient
        from apps.dashboard.models import PPCCampaignSnapshot
        from apps.dashboard.completeness import log_sync

        if opts['marketplace']:
            mps = [opts['marketplace']]
        else:
            mps = list(AmazonAPIConfig.objects.filter(is_active=True)
                       .values_list('marketplace', flat=True))

        if not mps:
            self.stderr.write(self.style.WARNING('No active marketplaces.'))
            return

        for mp in mps:
            cfg = AmazonAPIConfig.objects.filter(marketplace=mp, is_active=True).first()
            if not cfg or not cfg.has_ads_credentials():
                self.stderr.write(self.style.WARNING(
                    f'  [{mp}] no Ads credentials — skipping.'))
                continue

            # Today in marketplace local TZ
            tz   = ZoneInfo(settings.AMAZON_MARKETPLACES.get(mp, {})
                            .get('timezone', settings.TIME_ZONE))
            today = datetime.now(tz=tz).date()

            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n📡  [{mp.upper()}] live PPC sync for {today}'))

            client = AdsAPIClient(cfg)

            # Resume any in-flight Amazon reports from the previous cron run.
            # Each AdsDataSyncLog row with status='pending' carries the report_id
            # Amazon assigned — passing those back to get_all_campaigns_summary
            # makes it poll the existing reports instead of submitting fresh ones
            # (Amazon would 425-dedupe anyway, but explicit resume is faster).
            from apps.dashboard.models import AdsDataSyncLog as _Log
            pending_ids: dict[str, str] = {}
            for src in ('sp_hourly', 'sb_daily', 'sd_daily'):
                row = _Log.objects.filter(
                    marketplace=mp, date=today, source=src, status='pending'
                ).first()
                if row and row.report_id:
                    pending_ids[src] = row.report_id

            # Single API call returns SP+SB+SD totals (SUMMARY time-unit).
            try:
                result = client.get_all_campaigns_summary(
                    date_range='today',
                    existing_sp_id=pending_ids.get('sp_hourly'),
                    existing_sb_id=pending_ids.get('sb_daily'),
                    existing_sd_id=pending_ids.get('sd_daily'),
                )
            except Exception as e:
                err = f'{type(e).__name__}: {e}'
                self.stderr.write(self.style.ERROR(
                    f'  ✗ get_all_campaigns_summary failed: {err}'))
                for src in ('sp_hourly', 'sb_daily', 'sd_daily'):
                    log_sync(mp, today, src, 'failed', error_message=err)
                continue

            status = result.get('status', '?')
            # Map our completeness sources to the per-type report_ids so
            # next run resumes the RIGHT report for each ad product
            # (without this, sb_daily was being resumed against the SP report
            # because they all stored the same report_id — see git history).
            _src_to_report = {
                'sp_hourly': str(result.get('sp_report_id') or ''),
                'sb_daily':  str(result.get('sb_report_id') or ''),
                'sd_daily':  str(result.get('sd_report_id') or ''),
            }

            if status == 'pending':
                self.stdout.write(self.style.WARNING(
                    f'  ⏳ Amazon still building '
                    f'(sp={_src_to_report["sp_hourly"][:8]} '
                    f'sb={_src_to_report["sb_daily"][:8]} '
                    f'sd={_src_to_report["sd_daily"][:8]}); next run resumes.'))
                for src, rid in _src_to_report.items():
                    log_sync(mp, today, src, 'pending', report_id=rid)
                continue
            if status != 'ok':
                err = f'unexpected status {status!r}'
                self.stderr.write(self.style.ERROR(f'  ✗ {err}'))
                for src in ('sp_hourly', 'sb_daily', 'sd_daily'):
                    log_sync(mp, today, src, 'failed', error_message=err)
                continue

            # Persist: one PPCCampaignSnapshot row per (campaign_id, type)
            campaigns = result.get('campaigns') or []
            sp_count = sb_count = sd_count = 0
            sp_total = sb_total = sd_total = Decimal('0')
            objs = []
            for c in campaigns:
                ad_type = c.get('_adType', 'sp')
                cid     = str(c.get('campaignId') or '')
                if not cid:
                    continue
                cost    = Decimal(str(round(float(c.get('cost') or 0), 4)))
                clicks  = int(c.get('clicks') or 0)
                impr    = int(c.get('impressions') or 0)
                sales   = Decimal(str(round(float(
                    c.get('sales7d') or c.get('sales14d')
                    or c.get('sales') or 0), 4)))
                orders  = int(c.get('purchases7d') or c.get('purchases14d')
                              or c.get('purchasesClicks') or c.get('purchases') or 0)
                # Derived metrics (avoid div0)
                acos = Decimal(str(float(cost) / float(sales) if sales else 0))
                roas = Decimal(str(float(sales) / float(cost) if cost  else 0))
                cpc  = Decimal(str(float(cost) / clicks if clicks else 0))
                ctr  = Decimal(str(clicks / impr if impr else 0))

                objs.append(PPCCampaignSnapshot(
                    marketplace   = mp,
                    date          = today,
                    campaign_id   = cid,
                    campaign_name = (c.get('campaignName') or '')[:256],
                    campaign_type = ad_type,
                    state         = 'enabled',
                    impressions   = impr,
                    clicks        = clicks,
                    spend         = cost,
                    sales_7d      = sales,
                    orders_7d     = orders,
                    units_7d      = 0,
                    acos          = acos,
                    roas          = roas,
                    cpc           = cpc,
                    ctr           = ctr,
                ))
                if ad_type == 'sp':
                    sp_count += 1; sp_total += cost
                elif ad_type == 'sb':
                    sb_count += 1; sb_total += cost
                elif ad_type == 'sd':
                    sd_count += 1; sd_total += cost

            if objs:
                PPCCampaignSnapshot.objects.bulk_create(
                    objs,
                    update_conflicts=True,
                    update_fields=['campaign_name', 'campaign_type', 'state',
                                   'impressions', 'clicks', 'spend',
                                   'sales_7d', 'orders_7d', 'units_7d',
                                   'acos', 'roas', 'cpc', 'ctr'],
                    unique_fields=['marketplace', 'date', 'campaign_id'],
                )

            # AdsDataSyncLog — one row per ad type, each with its OWN report_id
            log_sync(mp, today, 'sp_hourly', 'ok' if sp_count else 'empty_from_amazon',
                     rows_received=sp_count, report_id=_src_to_report['sp_hourly'])
            log_sync(mp, today, 'sb_daily', 'ok' if sb_count else 'empty_from_amazon',
                     rows_received=sb_count, report_id=_src_to_report['sb_daily'])
            log_sync(mp, today, 'sd_daily', 'ok' if sd_count else 'empty_from_amazon',
                     rows_received=sd_count, report_id=_src_to_report['sd_daily'])

            self.stdout.write(self.style.SUCCESS(
                f'  ✓ wrote {len(objs)} campaign rows for {today}  '
                f'(SP ${sp_total:.2f} · SB ${sb_total:.2f} · SD ${sd_total:.2f})'
            ))

        self.stdout.write(self.style.SUCCESS('\n✅  Today live PPC sync complete.\n'))
