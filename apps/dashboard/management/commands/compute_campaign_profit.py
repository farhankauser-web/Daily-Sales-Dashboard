"""
compute_campaign_profit — Build CampaignProfitDaily + CampaignSearchTermSummary
for one or more dates.

Inputs (per marketplace × date):
    • AdsAdvertisedProductDailySnapshot  → per (campaign, ASIN, SKU) attributed
                                            revenue / units / spend
    • DailySkuSnapshot                    → per-SKU per-day revenue / cgs /
                                            amz_fee (referral) / fulfill (FBA fee)
    • PPCCampaignSnapshot                 → campaign-level totals — drives
                                            attribution_coverage_pct
    • DailyMetric                         → total marketplace revenue — drives TACoS
    • Product / COGSEntry / FBAFeeRate    → fallback margin lookup when
                                            DailySkuSnapshot is missing the SKU

Outputs:
    • CampaignProfitDaily                 — one row per (marketplace, date, campaign)
    • CampaignSearchTermSummary           — one row per (marketplace, date, campaign)

Run order:
    Cron schedule should call this AFTER ingest_ads_detail_reports has populated
    AdsAdvertisedProductDailySnapshot and AdsSearchTermDailySnapshot for the day.

Usage:
    python manage.py compute_campaign_profit                       # yesterday
    python manage.py compute_campaign_profit --date 2026-06-10
    python manage.py compute_campaign_profit --date 2026-06-10 --rewind 7
    python manage.py compute_campaign_profit --marketplace usa --backfill-window 30
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

logger = logging.getLogger(__name__)


# Auto-tag thresholds. Kept here (rather than per-marketplace config) so the
# search-term summary stays reproducible; can be promoted to MarketplaceConfig
# later without schema change.
_HIGH_SPEND_THRESHOLD  = Decimal('5.00')
_HIGH_CTR_THRESHOLD    = Decimal('0.005')
_LOW_CVR_THRESHOLD     = Decimal('0.02')
_HIGH_ROAS_THRESHOLD   = Decimal('5.0')
_HIGH_PROFIT_THRESHOLD = Decimal('50.00')
_LOSING_MONEY_FLOOR    = Decimal('-20.00')


def _parse_brand_family(campaign_name: str) -> tuple[str, str, str]:
    """
    Heuristic parse of brand / product family / initials from a campaign name.

    Conventions observed in this account (e.g. "6KTH-EXT-Kitchen Towels-KW",
    "4BTH-SP-EXT-bath towels-WHT"):
        • Leading token "<digits><initials>" (e.g. "6KTH" or "4BTH") encodes
          the product code → initials
        • A descriptive segment ("Kitchen Towels", "bath towels") between
          dashes → product_family
        • Brand defaults blank; managers will set it manually then lock.

    Returns (brand, product_family, initials). Empty strings if not parseable.
    """
    import re
    if not campaign_name:
        return ('', '', '')

    name = campaign_name.strip()
    initials = ''
    family = ''

    # Initials: first token may be like "6KTH", "4BTH", "BTW"
    head = name.split('-', 1)[0].strip()
    m = re.match(r'^\d*([A-Z]{2,6})$', head)
    if m:
        initials = m.group(1)

    # Family: look for a Title-Case multi-word segment in the dashed name
    segs = [s.strip() for s in name.split('-') if s.strip()]
    for s in segs:
        # Skip pure ad-type / placement / colour / numeric tokens
        if re.match(r'^(SP|SB|SD|EXT|VDO|KW|TOS|ROS|WHT|BLK|GRY|B2B|\d+)$', s, re.I):
            continue
        # Prefer multi-word segments — more descriptive than initials
        if ' ' in s:
            family = s.title()
            break
        if not family and s.isalpha() and len(s) > 4:
            family = s.title()

    return ('', family, initials)


class Command(BaseCommand):
    help = ('Aggregate AdsAdvertisedProductDailySnapshot + DailySkuSnapshot '
            'into CampaignProfitDaily, and AdsSearchTermDailySnapshot into '
            'CampaignSearchTermSummary, for the given date(s).')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; default: all active.')
        parser.add_argument('--date', default=None,
                            help='YYYY-MM-DD; default: yesterday.')
        parser.add_argument('--rewind', type=int, default=0,
                            help='Re-compute the prior N days too (default 0).')
        parser.add_argument('--backfill-window', type=int, default=0,
                            help='Compute the trailing N days ending at --date '
                                 '(or yesterday). Convenience for large backfills.')
        parser.add_argument('--skip-search-terms', action='store_true',
                            help='Skip the CampaignSearchTermSummary rebuild.')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig

        if opts['marketplace']:
            mps = [opts['marketplace']]
        else:
            mps = list(AmazonAPIConfig.objects.filter(is_active=True)
                       .values_list('marketplace', flat=True))
        if not mps:
            self.stderr.write(self.style.WARNING('No active marketplaces.'))
            return

        for mp in mps:
            tz = ZoneInfo(settings.AMAZON_MARKETPLACES.get(mp, {})
                          .get('timezone', settings.TIME_ZONE))
            anchor = (date.fromisoformat(opts['date']) if opts['date']
                      else datetime.now(tz=tz).date() - timedelta(days=1))

            if opts['backfill_window'] > 0:
                dates = [anchor - timedelta(days=i) for i in range(opts['backfill_window'])]
            else:
                dates = [anchor - timedelta(days=i) for i in range(opts['rewind'] + 1)]

            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n💰  [{mp.upper()}] computing campaign profit for '
                f'{len(dates)} day(s)'))

            for d in sorted(dates):
                self._compute_profit_for_day(mp, d)
                if not opts['skip_search_terms']:
                    self._compute_search_term_summary_for_day(mp, d)

            # Always refresh the Campaign dim table for this marketplace —
            # idempotent, cheap, and keeps drill-downs / search-term pages
            # rendering readable campaign names without a separate backfill.
            self._upsert_campaign_dim(mp)

        self.stdout.write(self.style.SUCCESS('\n✅  Campaign profit computation done.\n'))

    # ── Campaign dim refresh ────────────────────────────────────────────────
    def _upsert_campaign_dim(self, mp: str):
        """
        Idempotent upsert into the Campaign dim from PPCCampaignSnapshot.

        Only writes name/type/portfolio/state and first/last seen dates.
        brand/product_family/initials are left untouched on existing rows
        so manual overrides aren't blown away. New rows get them parsed
        from the campaign name (see _parse_brand_family).
        """
        from apps.dashboard.models import Campaign, PPCCampaignSnapshot
        from django.db.models import Min, Max

        # GROUP BY campaign_id only — names can change across dates, but the
        # dim should hold one row per (mp, campaign_id) using the LATEST name.
        per_campaign: dict[str, dict] = {}
        for row in PPCCampaignSnapshot.objects.filter(
            marketplace=mp
        ).order_by('campaign_id', '-date').values(
            'campaign_id', 'campaign_name', 'campaign_type', 'state',
            'portfolio', 'date',
        ):
            cid = row['campaign_id']
            if cid not in per_campaign:
                # First row for this campaign — it's the latest because of order_by
                per_campaign[cid] = {
                    'campaign_id':   cid,
                    'campaign_name': row['campaign_name'],
                    'campaign_type': row['campaign_type'],
                    'state':         row['state'],
                    'portfolio':     row['portfolio'],
                    'first_seen':    row['date'],
                    'last_seen':     row['date'],
                }
            else:
                # Roll the first/last seen
                b = per_campaign[cid]
                if row['date'] < b['first_seen']:
                    b['first_seen'] = row['date']
                if row['date'] > b['last_seen']:
                    b['last_seen'] = row['date']
        agg = per_campaign.values()

        # Existing rows so we don't overwrite locked brand/family fields
        existing = {c.campaign_id: c for c in Campaign.objects.filter(
            marketplace=mp)}

        to_create, to_update = [], []
        for row in agg:
            cid       = row['campaign_id']
            name      = row['campaign_name'] or cid
            ctype     = row['campaign_type'] or 'sp'
            state     = row['state'] or ''
            portfolio = row['portfolio'] or ''
            brand, family, initials = _parse_brand_family(name)

            if cid in existing:
                c = existing[cid]
                c.campaign_name  = name[:256]
                c.campaign_type  = ctype
                c.portfolio_name = portfolio[:128]
                c.state          = state[:12]
                c.last_seen_date = row['last_seen']
                if c.first_seen_date is None or c.first_seen_date > row['first_seen']:
                    c.first_seen_date = row['first_seen']
                if not c.brand_locked and brand:
                    c.brand = brand[:64]
                if not c.product_family_locked and family:
                    c.product_family = family[:64]
                if not c.initials_locked and initials:
                    c.initials = initials[:16]
                to_update.append(c)
            else:
                to_create.append(Campaign(
                    marketplace      = mp,
                    campaign_id      = cid,
                    campaign_name    = name[:256],
                    campaign_type    = ctype,
                    portfolio_name   = portfolio[:128],
                    state            = state[:12],
                    brand            = brand[:64],
                    product_family   = family[:64],
                    initials         = initials[:16],
                    first_seen_date  = row['first_seen'],
                    last_seen_date   = row['last_seen'],
                ))

        if to_create:
            Campaign.objects.bulk_create(to_create, batch_size=500)
        if to_update:
            Campaign.objects.bulk_update(
                to_update, batch_size=500,
                fields=['campaign_name', 'campaign_type', 'portfolio_name',
                        'state', 'first_seen_date', 'last_seen_date',
                        'brand', 'product_family', 'initials'])

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ [{mp}] Campaign dim — {len(to_create)} new, {len(to_update)} updated'))

    # ── CampaignProfitDaily ─────────────────────────────────────────────────
    def _compute_profit_for_day(self, mp: str, d: date):
        from apps.dashboard.models import (
            AdsAdvertisedProductDailySnapshot, DailySkuSnapshot,
            PPCCampaignSnapshot, CampaignProfitDaily, DailyMetric, Product,
        )

        ap_rows = list(AdsAdvertisedProductDailySnapshot.objects.filter(
            marketplace=mp, date=d).values(
            'campaign_id', 'source_ad_type', 'asin', 'advertised_sku',
            'spend', 'sales_7d', 'units_7d', 'orders_7d',
        ))
        if not ap_rows:
            self.stdout.write(self.style.WARNING(
                f'  ⚠  [{mp}] {d} no advertised-product rows; skipping.'))
            return

        # ── 1. Per-SKU per-unit cost lookup ─────────────────────────────────
        # DailySkuSnapshot stores totals (cgs, amz_fee, fulfill) and qty for
        # the day. Per-unit values come from dividing by qty. Skipping zero-qty
        # rows avoids div-zero (those SKUs sold via ads but the report wasn't
        # synced yet — fall through to Product/COGS fallback).
        skus_in_play = {(r['advertised_sku'] or '') for r in ap_rows
                        if r['advertised_sku']}
        sku_cost_map: dict[str, dict] = {}
        if skus_in_play:
            for row in DailySkuSnapshot.objects.filter(
                marketplace=mp, date=d, sku__in=skus_in_play
            ).values('sku', 'qty', 'cgs', 'amz_fee', 'fulfill', 'revenue'):
                qty = max(int(row['qty'] or 0), 1)  # guard div-zero; 0 qty → treat as 1
                sku_cost_map[row['sku']] = {
                    'cogs_per_unit':     Decimal(row['cgs'])     / qty,
                    'referral_per_unit': Decimal(row['amz_fee']) / qty,
                    'fba_per_unit':      Decimal(row['fulfill']) / qty,
                    'price_per_unit':    (Decimal(row['revenue']) / qty
                                          if int(row['qty'] or 0) > 0 else Decimal('0')),
                }

        # Pre-fetch fallback Product rows for SKUs missing from DailySkuSnapshot
        missing_skus = skus_in_play - set(sku_cost_map.keys())
        product_fallback: dict[str, dict] = {}
        if missing_skus:
            for p in Product.objects.filter(marketplace=mp, sku__in=missing_skus).only(
                'sku', 'fba_fee', 'referral_fee_pct', 'sale_price', 'list_price',
            ):
                price = Decimal(p.sale_price or p.list_price or 0)
                fba   = Decimal(p.fba_fee or 0)
                ref_p = Decimal(p.referral_fee_pct or 15) / Decimal('100')
                product_fallback[p.sku] = {
                    'fba_per_unit':       fba,
                    'referral_pct':       ref_p,
                    'price_per_unit':     price,
                    'cogs_per_unit':      Decimal('0'),    # no COGS row → 0 is safer than guess
                    'cogs_missing':       True,
                }

        # ── 2. Campaign-level totals from PPCCampaignSnapshot ───────────────
        camp_totals: dict[str, dict] = {}
        for row in PPCCampaignSnapshot.objects.filter(
            marketplace=mp, date=d
        ).values('campaign_id', 'campaign_type', 'spend', 'sales_7d',
                 'orders_7d', 'units_7d', 'impressions', 'clicks'):
            camp_totals[row['campaign_id']] = row

        # ── 3. Day-level marketplace revenue for TACoS ──────────────────────
        day_total = DailyMetric.objects.filter(
            marketplace=mp, date=d
        ).aggregate(total=Sum('revenue'))['total'] or Decimal('0')

        # ── 4. Aggregate ad rows → per-campaign P&L ─────────────────────────
        agg: dict[str, dict] = defaultdict(lambda: {
            'source_ad_type': 'sp',
            'ad_revenue': Decimal('0'),
            'attributed_units': 0,
            'attributed_orders': 0,
            'cogs_attributed': Decimal('0'),
            'referral_fee_attributed': Decimal('0'),
            'fba_fee_attributed': Decimal('0'),
            'other_fees_attributed': Decimal('0'),
            'sku_set': set(),
        })

        for r in ap_rows:
            cid  = r['campaign_id']
            sku  = r['advertised_sku'] or ''
            units   = int(r['units_7d'] or 0)
            revenue = Decimal(r['sales_7d'] or 0)
            orders  = int(r['orders_7d'] or 0)

            bucket = agg[cid]
            bucket['source_ad_type']    = r['source_ad_type']
            bucket['ad_revenue']       += revenue
            bucket['attributed_units'] += units
            bucket['attributed_orders'] += orders
            if sku:
                bucket['sku_set'].add(sku)

            # Per-unit cost lookup
            if sku and sku in sku_cost_map:
                m = sku_cost_map[sku]
                bucket['cogs_attributed']         += units * m['cogs_per_unit']
                bucket['referral_fee_attributed'] += units * m['referral_per_unit']
                bucket['fba_fee_attributed']      += units * m['fba_per_unit']
            elif sku and sku in product_fallback:
                m = product_fallback[sku]
                # No DailySku row → use referral% of revenue + per-unit FBA
                bucket['cogs_attributed']         += units * m['cogs_per_unit']
                bucket['referral_fee_attributed'] += revenue * m['referral_pct']
                bucket['fba_fee_attributed']      += units * m['fba_per_unit']
            # else: no SKU mapping at all — contributes to ad_revenue and units
            # but no fees/cogs. attribution_coverage_pct will surface this.

        # ── 5. Write CampaignProfitDaily rows ───────────────────────────────
        objs = []
        for cid, b in agg.items():
            ct = camp_totals.get(cid, {})
            campaign_spend = Decimal(ct.get('spend')   or 0)
            campaign_sales = Decimal(ct.get('sales_7d') or 0)

            contribution_margin = (b['ad_revenue']
                                   - b['cogs_attributed']
                                   - b['referral_fee_attributed']
                                   - b['fba_fee_attributed']
                                   - b['other_fees_attributed'])
            gross_profit = contribution_margin - campaign_spend

            margin_pct = (gross_profit / b['ad_revenue'] * 100
                          if b['ad_revenue'] > 0 else Decimal('0'))
            acos = (campaign_spend / b['ad_revenue']
                    if b['ad_revenue'] > 0 else Decimal('0'))
            roas = (b['ad_revenue'] / campaign_spend
                    if campaign_spend > 0 else Decimal('0'))
            tacos = (campaign_spend / day_total
                     if day_total > 0 else Decimal('0'))

            # attribution_coverage_pct — what fraction of the campaign-reported
            # sales did we actually attribute to advertised-product rows?
            #
            # PPCCampaignSnapshot.sales_7d is the truth-of-record. If the
            # advertised-product report covers all of it (typical), coverage is
            # 100%. If a campaign has a sales total but only some ASIN rows came
            # through, coverage flags the gap.
            if campaign_sales > 0:
                coverage = min(Decimal('100.00'),
                               (b['ad_revenue'] / campaign_sales * 100)
                               .quantize(Decimal('0.01')))
            else:
                # No reported sales for the campaign → if we attributed nothing
                # the campaign is rightly 100% covered (nothing to miss); if we
                # attributed something but campaign reports 0, flag it as
                # over-attribution (coverage > 100% → cap at 100).
                coverage = Decimal('100.00') if b['ad_revenue'] == 0 else Decimal('0.00')

            objs.append(dict(
                marketplace        = mp,
                date               = d,
                campaign_id        = cid,
                source_ad_type     = b['source_ad_type'],
                spend              = campaign_spend.quantize(Decimal('0.01')),
                ad_revenue         = b['ad_revenue'].quantize(Decimal('0.01')),
                attributed_units   = b['attributed_units'],
                attributed_orders  = b['attributed_orders'],
                cogs_attributed    = b['cogs_attributed'].quantize(Decimal('0.01')),
                referral_fee_attributed = b['referral_fee_attributed'].quantize(Decimal('0.01')),
                fba_fee_attributed = b['fba_fee_attributed'].quantize(Decimal('0.01')),
                other_fees_attributed = b['other_fees_attributed'].quantize(Decimal('0.01')),
                contribution_margin = contribution_margin.quantize(Decimal('0.01')),
                gross_profit       = gross_profit.quantize(Decimal('0.01')),
                margin_pct         = margin_pct.quantize(Decimal('0.0001')),
                tacos              = tacos.quantize(Decimal('0.0001')),
                acos               = acos.quantize(Decimal('0.0001')),
                roas               = roas.quantize(Decimal('0.0001')),
                sku_count_attributed     = len(b['sku_set']),
                attribution_coverage_pct = coverage,
            ))

        if not objs:
            return

        with transaction.atomic():
            CampaignProfitDaily.objects.bulk_create(
                [CampaignProfitDaily(**o) for o in objs],
                batch_size=500,
                update_conflicts=True,
                unique_fields=['marketplace', 'date', 'campaign_id'],
                update_fields=['source_ad_type', 'spend', 'ad_revenue',
                               'attributed_units', 'attributed_orders',
                               'cogs_attributed', 'referral_fee_attributed',
                               'fba_fee_attributed', 'other_fees_attributed',
                               'contribution_margin', 'gross_profit',
                               'margin_pct', 'tacos', 'acos', 'roas',
                               'sku_count_attributed', 'attribution_coverage_pct'],
            )

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ [{mp}] {d} CampaignProfitDaily — {len(objs)} campaigns'))

    # ── CampaignSearchTermSummary ───────────────────────────────────────────
    def _compute_search_term_summary_for_day(self, mp: str, d: date):
        from apps.dashboard.models import (
            AdsSearchTermDailySnapshot, CampaignSearchTermSummary,
        )

        rows = AdsSearchTermDailySnapshot.objects.filter(
            marketplace=mp, date=d
        ).values('campaign_id', 'source_ad_type',
                 'spend', 'sales_7d', 'orders_7d', 'impressions',
                 'clicks', 'ctr', 'cvr', 'roas')
        if not rows:
            return

        agg: dict[str, dict] = defaultdict(lambda: {
            'source_ad_type': 'sp',
            'distinct_terms': 0,
            'high_spend_no_sales': 0,
            'high_ctr_low_cvr': 0,
            'losing_money': 0,
            'scaling_opportunity': 0,
            'high_profit': 0,
            'impressions': 0, 'clicks': 0,
            'spend': Decimal('0'), 'sales_7d': Decimal('0'),
            'orders_7d': 0, 'wasted_spend': Decimal('0'),
        })

        # Tag thresholds — defined at top of file
        for r in rows:
            cid = r['campaign_id']
            b   = agg[cid]
            b['source_ad_type'] = r['source_ad_type']

            spend = Decimal(r['spend']    or 0)
            sales = Decimal(r['sales_7d'] or 0)
            orders = int(r['orders_7d']   or 0)
            ctr   = Decimal(r['ctr']  or 0)
            cvr   = Decimal(r['cvr']  or 0)
            roas  = Decimal(r['roas'] or 0)

            b['distinct_terms'] += 1
            b['impressions']    += int(r['impressions'] or 0)
            b['clicks']         += int(r['clicks']      or 0)
            b['spend']          += spend
            b['sales_7d']       += sales
            b['orders_7d']      += orders

            # Auto-tag counters
            if spend > _HIGH_SPEND_THRESHOLD and orders == 0:
                b['high_spend_no_sales'] += 1
                b['wasted_spend']        += spend
            if ctr > _HIGH_CTR_THRESHOLD and cvr < _LOW_CVR_THRESHOLD and int(r['clicks'] or 0) > 10:
                b['high_ctr_low_cvr'] += 1
            if roas > _HIGH_ROAS_THRESHOLD and spend > _HIGH_SPEND_THRESHOLD:
                b['high_profit'] += 1
                b['scaling_opportunity'] += 1
            if spend > 0 and (sales - spend) < _LOSING_MONEY_FLOOR:
                b['losing_money'] += 1

        objs = []
        for cid, b in agg.items():
            objs.append(dict(
                marketplace        = mp,
                date               = d,
                campaign_id        = cid,
                source_ad_type     = b['source_ad_type'],
                distinct_terms     = b['distinct_terms'],
                high_spend_no_sales = b['high_spend_no_sales'],
                high_ctr_low_cvr   = b['high_ctr_low_cvr'],
                losing_money       = b['losing_money'],
                scaling_opportunity = b['scaling_opportunity'],
                high_profit        = b['high_profit'],
                impressions        = b['impressions'],
                clicks             = b['clicks'],
                spend              = b['spend'].quantize(Decimal('0.01')),
                sales_7d           = b['sales_7d'].quantize(Decimal('0.01')),
                orders_7d          = b['orders_7d'],
                wasted_spend       = b['wasted_spend'].quantize(Decimal('0.01')),
            ))

        with transaction.atomic():
            CampaignSearchTermSummary.objects.bulk_create(
                [CampaignSearchTermSummary(**o) for o in objs],
                batch_size=500,
                update_conflicts=True,
                unique_fields=['marketplace', 'date', 'campaign_id'],
                update_fields=['source_ad_type', 'distinct_terms',
                               'high_spend_no_sales', 'high_ctr_low_cvr',
                               'losing_money', 'scaling_opportunity', 'high_profit',
                               'impressions', 'clicks', 'spend', 'sales_7d',
                               'orders_7d', 'wasted_spend'],
            )

        self.stdout.write(self.style.SUCCESS(
            f'  ✓ [{mp}] {d} CampaignSearchTermSummary — {len(objs)} campaigns'))
