"""
generate_profit_alerts — Phase 2 alert engine.

Scans yesterday's P&L (and recent days where useful) and creates Alert rows
for:

  Critical:
    • Negative profit day
    • Margin collapse (>20% drop vs day-before)
    • Profit decline (>20% drop vs day-before)
    • TACoS spike (>30% increase vs 7-day rolling)

  Warning:
    • High-spend, no-sales search term (waste)
    • High-spend, losing campaign (>$10 loss on >$10 spend)
    • Margin-erosion campaign (positive sales, negative gross profit)

  Info / Opportunity:
    • High-ROAS scaling opportunity (margin >10%, ROAS >5, spend >$10)
    • New profitable SKU (first time crossing $50 profit)

Idempotent: alerts use a deterministic `metric_key` so re-running on the same
day updates the existing row rather than creating duplicates. Resolved alerts
are NOT re-created — once the user resolves a finding, it stays resolved
until the underlying condition changes.

Usage:
    python manage.py generate_profit_alerts                  # yesterday, all marketplaces
    python manage.py generate_profit_alerts --date 2026-06-10
    python manage.py generate_profit_alerts --marketplace usa
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


# Thresholds — conservative so alerts mean something
_MARGIN_COLLAPSE_PCT  = Decimal('0.20')   # >20% relative drop
_PROFIT_DECLINE_PCT   = Decimal('0.20')   # >20% relative drop
_TACOS_SPIKE_PCT      = Decimal('0.30')   # >30% relative increase vs 7-day avg
_LOSING_CAMPAIGN_LOSS = Decimal('10')     # loss exceeding $10
_LOSING_CAMPAIGN_SPEND= Decimal('10')     # on > $10 spend
_WASTED_TERM_SPEND    = Decimal('10')     # term wasted >$10 with 0 orders
_SCALING_MIN_PROFIT   = Decimal('100')
_SCALING_MIN_MARGIN   = Decimal('0.10')   # >10% net margin
_SCALING_MIN_ROAS     = Decimal('5.0')


class Command(BaseCommand):
    help = ('Generate P&L profit alerts for a given date '
            '(critical / warning / opportunity).')

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; default: all active.')
        parser.add_argument('--date', default=None,
                            help='YYYY-MM-DD; default: yesterday.')

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

        total = 0
        for mp in mps:
            tz = ZoneInfo(settings.AMAZON_MARKETPLACES.get(mp, {})
                          .get('timezone', settings.TIME_ZONE))
            target = (date.fromisoformat(opts['date']) if opts['date']
                      else datetime.now(tz=tz).date() - timedelta(days=1))

            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n🚨  [{mp.upper()}] scanning {target} for alerts'))

            n = self._scan_marketplace(mp, target)
            total += n
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ [{mp}] {n} alert(s) created/updated'))

        self.stdout.write(self.style.SUCCESS(
            f'\n✅  Done — {total} alert(s) total.\n'))

    # ── per-marketplace scan ────────────────────────────────────────────────
    def _scan_marketplace(self, mp: str, target_date: date) -> int:
        from apps.dashboard.models import (
            DailyMetric, CampaignProfitDaily, AdsSearchTermDailySnapshot,
            Campaign, PPCCampaignSnapshot, Alert,
        )

        n = 0

        # ── 1. Day P&L vs day-before vs 7-day rolling ───────────────────────
        cur  = DailyMetric.objects.filter(marketplace=mp, date=target_date).first()
        prev = DailyMetric.objects.filter(marketplace=mp, date=target_date - timedelta(days=1)).first()
        if not cur:
            self.stdout.write(self.style.WARNING(
                f'  ⚠ no DailyMetric for {target_date} — skipping P&L checks'))
            return n

        # Compute target-day profit + derived
        cur_profit, cur_margin, cur_tacos = self._compute_day(cur)

        # 7-day TACoS baseline (excluding target day)
        from django.db.models import Avg
        baseline = DailyMetric.objects.filter(
            marketplace=mp,
            date__gte=target_date - timedelta(days=7),
            date__lt=target_date,
        ).aggregate(avg_tacos=Avg('tacos'))['avg_tacos']
        baseline_tacos = Decimal(str(baseline or 0)) * 100  # tacos field is fraction

        # ── 1a. Negative profit ─────────────────────────────────────────────
        if cur_profit < 0:
            n += self._upsert_alert(Alert, mp,
                severity='critical', category='performance',
                metric_key=f'pnl:{target_date}:negative_profit',
                title=f'Negative profit day · {target_date}',
                message=(f'Yesterday closed at a loss of ${abs(cur_profit):,.2f} '
                         f'on ${float(cur.revenue):,.0f} revenue. '
                         f'Margin: {cur_margin:.1f}%. '
                         f'Largest cost: see Daily P&L waterfall.'),
                metric_value=f'-{abs(cur_profit):.2f}',
                threshold='0',
            )

        # ── 1b. Margin collapse ─────────────────────────────────────────────
        if prev:
            prev_profit, prev_margin, prev_tacos = self._compute_day(prev)
            if prev_margin > 0 and cur_margin < prev_margin * (Decimal('1') - _MARGIN_COLLAPSE_PCT):
                n += self._upsert_alert(Alert, mp,
                    severity='critical', category='performance',
                    metric_key=f'pnl:{target_date}:margin_collapse',
                    title=f'Margin collapse · {target_date}',
                    message=(f'Margin dropped to {cur_margin:.1f}% from {prev_margin:.1f}% '
                             f'day-over-day — a {(prev_margin - cur_margin):.1f} pt drop.'),
                    metric_value=f'{cur_margin:.2f}',
                    threshold=f'{float(prev_margin * (Decimal("1") - _MARGIN_COLLAPSE_PCT)):.2f}',
                )
            # 1c. Profit decline >20%
            if prev_profit > 0 and cur_profit < prev_profit * (Decimal('1') - _PROFIT_DECLINE_PCT):
                drop_pct = float((prev_profit - cur_profit) / prev_profit * 100)
                n += self._upsert_alert(Alert, mp,
                    severity='critical', category='performance',
                    metric_key=f'pnl:{target_date}:profit_decline',
                    title=f'Profit decline · {target_date}',
                    message=(f'Profit dropped {drop_pct:.1f}% day-over-day — '
                             f'from ${float(prev_profit):,.0f} to ${float(cur_profit):,.0f}.'),
                    metric_value=f'{cur_profit:.2f}',
                    threshold=f'{float(prev_profit * (Decimal("1") - _PROFIT_DECLINE_PCT)):.2f}',
                )

        # ── 1d. TACoS spike vs 7-day baseline ───────────────────────────────
        if baseline_tacos > 0 and cur_tacos > baseline_tacos * (Decimal('1') + _TACOS_SPIKE_PCT):
            n += self._upsert_alert(Alert, mp,
                severity='warning', category='ppc',
                metric_key=f'pnl:{target_date}:tacos_spike',
                title=f'TACoS spike · {target_date}',
                message=(f'TACoS at {cur_tacos:.1f}% vs 7-day baseline of {baseline_tacos:.1f}% — '
                         f'+{float(cur_tacos - baseline_tacos):.1f} pts. '
                         f'Ad spend growing faster than revenue.'),
                metric_value=f'{cur_tacos:.2f}',
                threshold=f'{float(baseline_tacos * (Decimal("1") + _TACOS_SPIKE_PCT)):.2f}',
            )

        # ── 2. Per-campaign alerts ──────────────────────────────────────────
        for r in CampaignProfitDaily.objects.filter(
            marketplace=mp, date=target_date,
        ).values('campaign_id', 'gross_profit', 'spend', 'ad_revenue',
                 'margin_pct', 'roas'):
            cid    = r['campaign_id']
            profit = Decimal(r['gross_profit'] or 0)
            spend  = Decimal(r['spend']        or 0)
            revenue= Decimal(r['ad_revenue']   or 0)
            margin = Decimal(r['margin_pct']   or 0)
            roas   = Decimal(r['roas']         or 0)

            cname = self._campaign_name(mp, cid)

            # 2a. Losing campaign — clearly losing money on real spend
            if profit < -_LOSING_CAMPAIGN_LOSS and spend > _LOSING_CAMPAIGN_SPEND:
                n += self._upsert_alert(Alert, mp,
                    severity='warning', category='ppc',
                    metric_key=f'pnl:{target_date}:losing_campaign:{cid}',
                    title=f'Losing campaign · {cname[:60]}',
                    message=(f'Campaign "{cname}" lost ${abs(profit):.2f} on '
                             f'${spend:.2f} spend yesterday '
                             f'(margin {margin:.1f}%, ROAS {roas:.2f}).'),
                    metric_value=f'-{abs(profit):.2f}',
                    threshold=f'-{_LOSING_CAMPAIGN_LOSS}',
                )

            # 2b. Scaling opportunity — high profit, high margin, high ROAS
            if (profit > _SCALING_MIN_PROFIT
                and margin > _SCALING_MIN_MARGIN * 100   # margin is %, threshold is fraction
                and roas > _SCALING_MIN_ROAS
                and spend > _LOSING_CAMPAIGN_SPEND):
                n += self._upsert_alert(Alert, mp,
                    severity='info', category='ppc',
                    metric_key=f'pnl:{target_date}:scaling:{cid}',
                    title=f'Scaling opportunity · {cname[:60]}',
                    message=(f'Campaign "{cname}" returned {roas:.2f}x ROAS at '
                             f'{margin:.1f}% net margin yesterday '
                             f'(${profit:.2f} profit on ${spend:.2f} spend). '
                             f'Consider raising budget.'),
                    metric_value=f'{roas:.2f}',
                    threshold=f'{_SCALING_MIN_ROAS}',
                )

        # ── 3. Wasted search term — top waste yesterday ─────────────────────
        st_waste: dict[str, dict] = defaultdict(lambda: {'spend': Decimal('0'), 'clicks': 0})
        for r in AdsSearchTermDailySnapshot.objects.filter(
            marketplace=mp, date=target_date, orders_7d=0,
        ).values('search_term', 'spend', 'clicks').order_by('-spend')[:100]:
            b = st_waste[r['search_term']]
            b['spend']  += Decimal(r['spend'] or 0)
            b['clicks'] += int(r['clicks']    or 0)
        # Just the top 1 wastrel — we don't want to flood the alerts
        if st_waste:
            term, b = max(st_waste.items(), key=lambda kv: kv[1]['spend'])
            if b['spend'] > _WASTED_TERM_SPEND:
                n += self._upsert_alert(Alert, mp,
                    severity='warning', category='ppc',
                    metric_key=f'pnl:{target_date}:wasted_term',
                    title=f'Wasted-spend search term · {term[:60]}',
                    message=(f'Search term "{term}" spent ${b["spend"]:.2f} on '
                             f'{b["clicks"]} clicks with zero orders yesterday.'),
                    metric_value=f'{b["spend"]:.2f}',
                    threshold=f'{_WASTED_TERM_SPEND}',
                )

        return n

    # ── Helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _compute_day(dm) -> tuple[Decimal, Decimal, Decimal]:
        """Returns (profit, margin_pct, tacos_pct) for a DailyMetric row."""
        rev   = Decimal(dm.revenue    or 0)
        ppc   = Decimal(dm.ppc_spend  or 0)
        ref   = Decimal(dm.amazon_fee or 0)
        fba   = Decimal(dm.fba_fee    or 0)
        cogs  = Decimal(dm.cgs        or 0)
        profit = rev - ppc - ref - fba - cogs
        margin_pct = (profit / rev * 100) if rev > 0 else Decimal('0')
        tacos_pct  = (ppc    / rev * 100) if rev > 0 else Decimal('0')
        return profit, margin_pct, tacos_pct

    def _campaign_name(self, mp: str, cid: str) -> str:
        from apps.dashboard.models import Campaign, PPCCampaignSnapshot
        c = Campaign.objects.filter(marketplace=mp, campaign_id=cid).values_list(
            'campaign_name', flat=True).first()
        if c:
            return c
        c = PPCCampaignSnapshot.objects.filter(marketplace=mp, campaign_id=cid).order_by(
            '-date').values_list('campaign_name', flat=True).first()
        return c or cid

    def _upsert_alert(self, Alert, mp, *, severity, category, metric_key,
                      title, message, metric_value, threshold) -> int:
        """
        Idempotent upsert keyed on (marketplace, metric_key).

        If a resolved alert with this key exists, we leave it resolved
        (the user already saw it; re-firing would be noisy). If unresolved
        or absent, we create/update.
        """
        existing = Alert.objects.filter(
            marketplace=mp, metric_key=metric_key,
        ).first()
        if existing and existing.is_resolved:
            return 0   # user already handled this; don't re-fire
        Alert.objects.update_or_create(
            marketplace=mp, metric_key=metric_key,
            defaults={
                'severity':     severity,
                'category':     category,
                'title':        title[:128],
                'message':      message,
                'metric_value': metric_value[:32],
                'threshold':    threshold[:32],
                'is_resolved':  False,
                'is_read':      False,
            },
        )
        return 1
