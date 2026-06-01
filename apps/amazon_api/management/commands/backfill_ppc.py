"""
apps/amazon_api/management/commands/backfill_ppc.py

Backfills historical PPC data from the Amazon Advertising API v3.

Fetches four DAILY reports per chunk for the requested date range:
  • spCampaigns          → PPCCampaignSnapshot (campaign_type='sp')
  • sbCampaigns          → PPCCampaignSnapshot (campaign_type='sb')
  • sdCampaigns          → PPCCampaignSnapshot (campaign_type='sd')
  • spAdvertisedProduct  → PPCProductSnapshot  (per-ASIN spend, campaign_type='sp')

SP+SB+SD reports are polled in parallel so the total wait time is the
same as polling one report. Correct total = SP + SB + SD campaign spend.

Usage:
  python manage.py backfill_ppc
  python manage.py backfill_ppc --marketplace usa --days 60
  python manage.py backfill_ppc --marketplace usa --start 2026-04-01 --end 2026-05-18
"""
import gzip
import json
import logging
import time
from datetime import date, timedelta
from decimal import Decimal

import requests
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

POLL_INTERVAL = 15   # seconds between status checks
MAX_WAIT      = 1800 # 30 minutes max wait per report

# ── Column definitions per report type ───────────────────────────────────────
SP_CAMP_COLS = ['date', 'campaignId', 'campaignName', 'campaignStatus',
                'impressions', 'clicks', 'cost',
                'purchases7d', 'sales7d', 'unitsSoldClicks7d']
# SB uses different column names: purchasesClicks / sales / unitsSold
SB_CAMP_COLS = ['date', 'campaignId', 'campaignName', 'campaignStatus',
                'impressions', 'clicks', 'cost',
                'purchasesClicks', 'sales', 'unitsSold']
# SD uses: purchases / sales / unitsSold (no attribution suffix)
SD_CAMP_COLS = ['date', 'campaignId', 'campaignName', 'campaignStatus',
                'impressions', 'clicks', 'cost',
                'purchases', 'sales', 'unitsSold']
PROD_COLS    = ['date', 'advertisedAsin', 'advertisedSku',
                'impressions', 'clicks', 'cost',
                'purchases7d', 'sales7d', 'unitsSoldClicks7d']


class Command(BaseCommand):
    help = 'Backfill historical PPC data from Amazon Ads API v3'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--days', type=int, default=39,
                            help='Days back from yesterday (default: 39)')
        parser.add_argument('--start', default=None, help='YYYY-MM-DD start date')
        parser.add_argument('--end',   default=None, help='YYYY-MM-DD end date (default: yesterday)')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.amazon_api.services import AdsAPIClient

        mp = opts['marketplace']
        cfg = AmazonAPIConfig.objects.filter(marketplace=mp, is_active=True).first()
        if not cfg:
            self.stderr.write(f'No active config for marketplace "{mp}"'); return
        if not cfg.has_ads_credentials():
            self.stderr.write('Ads API credentials not set on this config.'); return

        yesterday = date.today() - timedelta(days=1)
        if opts['end']:
            end_d = date.fromisoformat(opts['end'])
        else:
            end_d = yesterday

        if opts['start']:
            start_d = date.fromisoformat(opts['start'])
        else:
            start_d = end_d - timedelta(days=opts['days'] - 1)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n📊 Backfilling PPC: {mp.upper()} | {start_d} → {end_d}'
        ))

        client  = AdsAPIClient(cfg)

        # Amazon Ads API v3 allows max 31 days per report — split into chunks
        CHUNK = 30
        chunks = []
        chunk_start = start_d
        while chunk_start <= end_d:
            chunk_end = min(chunk_start + timedelta(days=CHUNK - 1), end_d)
            chunks.append((chunk_start, chunk_end))
            chunk_start = chunk_end + timedelta(days=1)

        self.stdout.write(f'  Splitting into {len(chunks)} chunk(s) of ≤{CHUNK} days')

        all_sp_rows   = []
        all_sb_rows   = []
        all_sd_rows   = []
        all_prod_rows = []

        for i, (cs, ce) in enumerate(chunks, 1):
            self.stdout.write(self.style.MIGRATE_LABEL(
                f'\n  Chunk {i}/{len(chunks)}: {cs} → {ce}'
            ))
            headers = client._headers()   # refresh token per chunk

            # ── Submit all 4 reports simultaneously ───────────────────────────
            self.stdout.write('    Submitting SP campaign report…')
            camp_sp_id = self._submit_report(
                headers, str(cs), str(ce), 'spCampaigns', SP_CAMP_COLS,
                ad_product='SPONSORED_PRODUCTS', group_by=['campaign'])
            self.stdout.write(f'      reportId: {camp_sp_id}')

            camp_sb_id = None
            self.stdout.write('    Submitting SB campaign report…')
            try:
                camp_sb_id = self._submit_report(
                    headers, str(cs), str(ce), 'sbCampaigns', SB_CAMP_COLS,
                    ad_product='SPONSORED_BRANDS', group_by=['campaign'],
                    pre_delay=3)   # brief pause to avoid 429 after SP submission
                self.stdout.write(f'      reportId: {camp_sb_id}')
            except Exception as e:
                self.stdout.write(f'      (SB skipped: {e})')

            camp_sd_id = None
            self.stdout.write('    Submitting SD campaign report…')
            try:
                camp_sd_id = self._submit_report(
                    headers, str(cs), str(ce), 'sdCampaigns', SD_CAMP_COLS,
                    ad_product='SPONSORED_DISPLAY', group_by=['campaign'],
                    pre_delay=3)   # brief pause to avoid 429 after SB submission
                self.stdout.write(f'      reportId: {camp_sd_id}')
            except Exception as e:
                self.stdout.write(f'      (SD skipped: {e})')

            self.stdout.write('    Submitting SP advertised-product report…')
            prod_id = self._submit_report(
                headers, str(cs), str(ce), 'spAdvertisedProduct', PROD_COLS,
                ad_product='SPONSORED_PRODUCTS', group_by=['advertiser'])
            self.stdout.write(f'      reportId: {prod_id}')

            # ── Poll all reports in parallel (interleaved) ────────────────────
            self.stdout.write('    Waiting for reports (15-25 min)…')
            id_label_pairs = [
                (camp_sp_id, f'SP-Camp[{i}]'),
                (camp_sb_id, f'SB-Camp[{i}]'),
                (camp_sd_id, f'SD-Camp[{i}]'),
                (prod_id,    f'Product[{i}]'),
            ]
            results = self._wait_and_download_all(headers, id_label_pairs)

            sp_rows   = results.get(camp_sp_id)
            sb_rows   = results.get(camp_sb_id) or []
            sd_rows   = results.get(camp_sd_id) or []
            prod_rows = results.get(prod_id)    or []

            if sp_rows is None:
                self.stderr.write(self.style.ERROR(
                    f'  SP Campaign chunk {i} failed — skipping.')); continue

            self.stdout.write(self.style.SUCCESS(
                f'    ✓ SP:{len(sp_rows)} SB:{len(sb_rows)} SD:{len(sd_rows)} '
                f'camp rows, {len(prod_rows)} product rows'
            ))
            all_sp_rows.extend(sp_rows)
            all_sb_rows.extend(sb_rows)
            all_sd_rows.extend(sd_rows)
            all_prod_rows.extend(prod_rows)

        all_camp_rows = all_sp_rows + all_sb_rows + all_sd_rows
        if not all_sp_rows:
            self.stderr.write(self.style.ERROR('No SP campaign data retrieved.')); return

        # ── Persist all collected data ────────────────────────────────────────
        self.stdout.write('\n  Saving to database…')
        if all_sp_rows:
            self._save_campaign_data(all_sp_rows, mp, start_d, end_d, campaign_type='sp')
        if all_sb_rows:
            self._save_campaign_data(all_sb_rows, mp, start_d, end_d, campaign_type='sb')
        if all_sd_rows:
            self._save_campaign_data(all_sd_rows, mp, start_d, end_d, campaign_type='sd')
        self._save_product_data(all_prod_rows, mp, start_d, end_d)
        self._update_daily_metrics(all_camp_rows, mp)

        self.stdout.write(self.style.SUCCESS('\n✅  PPC backfill complete.\n'))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _submit_report(self, headers, start_str, end_str, report_type_id, columns,
                       ad_product='SPONSORED_PRODUCTS', group_by=None, pre_delay=0):
        """Submit an Ads v3 report. Returns reportId.
        pre_delay: seconds to sleep before submitting (avoids 429 when submitting sequentially).
        Retries up to 2 times on 429 Too Many Requests.
        """
        if group_by is None:
            group_by = ['advertiser'] if report_type_id == 'spAdvertisedProduct' else ['campaign']
        if pre_delay:
            time.sleep(pre_delay)
        ADS = 'https://advertising-api.amazon.com'
        for attempt in range(3):
            resp = requests.post(
                f'{ADS}/reporting/reports',
                headers=headers,
                json={
                    'name': f'{report_type_id} backfill {start_str}:{end_str}',
                    'startDate': start_str,
                    'endDate':   end_str,
                    'configuration': {
                        'adProduct':    ad_product,
                        'groupBy':      group_by,
                        'columns':      columns,
                        'reportTypeId': report_type_id,
                        'timeUnit':     'DAILY',
                        'format':       'GZIP_JSON',
                    },
                },
                timeout=20,
            )
            if resp.status_code == 425:
                # Duplicate — reuse existing report
                rid = resp.json().get('detail', '').split(': ')[-1].strip()
                self.stdout.write(f'    (duplicate — reusing {rid})')
                return rid
            if resp.status_code == 429:
                retry_after = int(resp.headers.get('Retry-After', 10))
                self.stdout.write(f'    (429 rate-limit — waiting {retry_after}s before retry…)')
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()['reportId']
        resp.raise_for_status()   # re-raise on last attempt

    def _wait_and_download_all(self, headers, id_label_pairs):
        """
        Poll multiple reports in parallel (interleaved loop).
        id_label_pairs: list of (report_id_or_None, label) tuples.
        Returns dict { report_id: rows_list_or_None }.
          rows_list = None  → timed-out or hard failure (caller should skip chunk)
          rows_list = []    → completed but empty / non-fatal error (SB/SD may be empty)
        """
        ADS = 'https://advertising-api.amazon.com'
        deadline = time.time() + MAX_WAIT

        # Build tracking state
        results  = {}      # rid → None (pending) | list (done)
        labels   = {}      # rid → label
        pending  = []      # (rid, label) still waiting
        for rid, lbl in id_label_pairs:
            if not rid:
                continue
            results[rid] = None
            labels[rid]  = lbl
            pending.append((rid, lbl))

        elapsed = 0
        while pending and time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            still_pending = []
            for rid, lbl in pending:
                try:
                    sr = requests.get(f'{ADS}/reporting/reports/{rid}',
                                      headers=headers, timeout=15)
                    sr.raise_for_status()
                    data  = sr.json()
                    state = data.get('status', '')
                    self.stdout.write(f'    [{elapsed:>4}s] {lbl}: {state}')
                    if state == 'COMPLETED':
                        dl = requests.get(data['url'], timeout=60)
                        dl.raise_for_status()
                        rows = json.loads(gzip.decompress(dl.content).decode('utf-8'))
                        results[rid] = rows
                    elif state in ('FAILED', 'CANCELLED'):
                        reason = data.get('failureReason', state)
                        self.stderr.write(f'  ✗ {lbl} {state}: {reason}')
                        results[rid] = []   # empty but not None → non-fatal
                    else:
                        still_pending.append((rid, lbl))
                except Exception as e:
                    self.stderr.write(f'  ✗ {lbl} poll error: {e}')
                    results[rid] = []       # treat as non-fatal
            pending = still_pending

        for rid, lbl in pending:
            self.stderr.write(f'  ✗ {lbl} timed out after {MAX_WAIT}s')
            # results[rid] stays None → caller treats as hard failure

        return results

    def _save_campaign_data(self, rows, marketplace, start_d, end_d, campaign_type='sp'):
        """Save campaign rows to PPCCampaignSnapshot.
        SP uses sales7d / purchases7d; SB and SD use sales14d / purchases14d.
        The field is stored as sales_7d regardless (name is legacy).
        """
        from apps.dashboard.models import PPCCampaignSnapshot
        objs = []
        skipped = 0
        for r in rows:
            d_str = r.get('date') or r.get('reportDate') or r.get('startDate') or ''
            if not d_str:
                skipped += 1; continue
            try:
                snap_date = date.fromisoformat(d_str[:10])
            except ValueError:
                skipped += 1; continue
            if not (start_d <= snap_date <= end_d):
                continue
            spend    = Decimal(str(r.get('cost') or 0))
            # SP:  sales7d / purchases7d / unitsSoldClicks7d
            # SB:  sales   / purchasesClicks / unitsSold
            # SD:  sales   / purchases       / unitsSold
            sales    = Decimal(str(r.get('sales7d') or r.get('sales14d') or r.get('sales') or 0))
            orders   = int(r.get('purchases7d') or r.get('purchases14d')
                           or r.get('purchasesClicks') or r.get('purchases') or 0)
            units    = int(r.get('unitsSoldClicks7d') or r.get('unitsSoldClicks14d')
                           or r.get('unitsSold') or 0)
            impr     = int(r.get('impressions') or 0)
            clicks   = int(r.get('clicks') or 0)
            acos_val = Decimal(str(spend / sales if sales else 0))
            roas_val = Decimal(str(sales / spend if spend else 0))
            cpc_val  = Decimal(str(spend / clicks if clicks else 0))
            ctr_val  = Decimal(str(clicks / impr  if impr   else 0))
            state_v  = (r.get('campaignStatus') or r.get('state') or 'enabled').lower()[:12]

            objs.append(PPCCampaignSnapshot(
                marketplace   = marketplace,
                date          = snap_date,
                campaign_id   = str(r.get('campaignId') or ''),
                campaign_name = (r.get('campaignName') or '')[:256],
                campaign_type = campaign_type,
                state         = state_v,
                impressions   = impr,
                clicks        = clicks,
                spend         = spend,
                sales_7d      = sales,
                orders_7d     = orders,
                units_7d      = units,
                acos          = acos_val,
                roas          = roas_val,
                cpc           = cpc_val,
                ctr           = ctr_val,
            ))

        if objs:
            PPCCampaignSnapshot.objects.bulk_create(
                objs,
                update_conflicts=True,
                update_fields=['impressions', 'clicks', 'spend', 'sales_7d',
                               'orders_7d', 'units_7d', 'acos', 'roas', 'cpc', 'ctr', 'state'],
                unique_fields=['marketplace', 'date', 'campaign_id'],
            )
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ {campaign_type.upper()} Campaign snapshots: '
            f'{len(objs)} saved, {skipped} skipped (missing date)'
        ))

    def _save_product_data(self, rows, marketplace, start_d, end_d):
        from apps.dashboard.models import PPCProductSnapshot
        objs = []
        skipped = 0
        for r in rows:
            d_str = r.get('date') or r.get('reportDate') or r.get('startDate') or ''
            if not d_str:
                skipped += 1; continue
            try:
                snap_date = date.fromisoformat(d_str[:10])
            except ValueError:
                skipped += 1; continue
            if not (start_d <= snap_date <= end_d):
                continue
            asin = (r.get('advertisedAsin') or '').upper()
            if not asin:
                skipped += 1; continue
            objs.append(PPCProductSnapshot(
                marketplace   = marketplace,
                date          = snap_date,
                asin          = asin,
                sku           = (r.get('advertisedSku') or '').upper(),
                campaign_type = 'sp',
                impressions   = int(r.get('impressions') or 0),
                clicks        = int(r.get('clicks') or 0),
                spend         = Decimal(str(r.get('cost') or 0)),
                sales_7d      = Decimal(str(r.get('sales7d') or 0)),
                orders_7d     = int(r.get('purchases7d') or 0),
                units_7d      = int(r.get('unitsSoldClicks7d') or 0),
            ))

        if objs:
            PPCProductSnapshot.objects.bulk_create(
                objs,
                update_conflicts=True,
                update_fields=['impressions', 'clicks', 'spend', 'sales_7d',
                               'orders_7d', 'units_7d'],
                unique_fields=['marketplace', 'date', 'asin', 'campaign_type'],
            )
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Product snapshots:  {len(objs)} saved, {skipped} skipped'
        ))

    def _update_daily_metrics(self, camp_rows, marketplace):
        """Aggregate campaign rows by date and patch ix_daily_metrics."""
        from apps.dashboard.models import DailyMetric
        from decimal import Decimal

        # Aggregate: date → {spend, impressions, clicks, sales, orders}
        by_date = {}
        for r in camp_rows:
            d_str = r.get('date') or r.get('reportDate') or r.get('startDate') or ''
            if not d_str:
                continue
            try:
                d = date.fromisoformat(d_str[:10])
            except ValueError:
                continue
            if d not in by_date:
                by_date[d] = {'spend': Decimal('0'), 'impressions': 0,
                               'clicks': 0, 'sales': Decimal('0'), 'orders': 0}
            agg = by_date[d]
            agg['spend']       += Decimal(str(r.get('cost') or 0))
            agg['impressions'] += int(r.get('impressions') or 0)
            agg['clicks']      += int(r.get('clicks') or 0)
            # SP: sales7d/purchases7d  SB: sales/purchasesClicks  SD: sales/purchases
            agg['sales']       += Decimal(str(r.get('sales7d') or r.get('sales14d')
                                             or r.get('sales') or 0))
            agg['orders']      += int(r.get('purchases7d') or r.get('purchases14d')
                                      or r.get('purchasesClicks') or r.get('purchases') or 0)

        updated = 0
        for d, agg in by_date.items():
            spend = agg['spend']
            sales = agg['sales']
            acos  = spend / sales if sales else Decimal('0')
            roas  = sales / spend if spend else Decimal('0')

            # Compute GM = CM − PPC for this day (need the DailyMetric row)
            dm_obj  = DailyMetric.objects.filter(marketplace=marketplace, date=d).first()
            cm_day  = float(dm_obj.contribution_margin) if dm_obj else 0.0
            rev_day = float(dm_obj.revenue)              if dm_obj else 0.0
            rev_dec = Decimal(str(rev_day))
            tacos      = (spend / rev_dec)      if (spend and rev_dec) else Decimal('0')
            gm_day     = cm_day - float(spend)
            gm_pct_day = Decimal(str(gm_day / rev_day if rev_day else 0))

            rows_updated = DailyMetric.objects.filter(
                marketplace=marketplace, date=d
            ).update(
                ppc_spend       = spend,
                ppc_sales       = sales,
                ppc_impressions = agg['impressions'],
                ppc_clicks      = agg['clicks'],
                tacos           = tacos,
                acos            = acos,
                roas            = roas,
                gross_margin    = Decimal(str(gm_day)),
                gm_pct          = gm_pct_day,
            )
            updated += rows_updated

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ DailyMetric rows patched: {updated} / {len(by_date)} dates'
        ))
