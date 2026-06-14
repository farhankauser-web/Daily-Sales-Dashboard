"""
unmapped_ppc_campaigns — List PPC campaigns whose name prefix doesn't map to a
product group via _CAMP_PREFIX_GROUP.

Run this periodically (weekly is enough) when you launch new campaigns so the
dashboard's group-attribution stays accurate. Any spend on an unmapped campaign
ends up in the "Unallocated PPC" row on the dashboard.

Usage:
    python manage.py unmapped_ppc_campaigns                       # last 30 days, all MPs
    python manage.py unmapped_ppc_campaigns --days 90
    python manage.py unmapped_ppc_campaigns --marketplace usa
    python manage.py unmapped_ppc_campaigns --show-mapped         # also list known ones
"""
from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max, Sum

from apps.amazon_api.views import _CAMP_PREFIX_GROUP, _match_campaign_to_group
from apps.dashboard.models import PPCCampaignSnapshot


def _prefix(name: str) -> str:
    """Extract the leading word before the first '-', uppercase. For display only."""
    return (name or '').split('-')[0].strip().upper()


class Command(BaseCommand):
    help = "List PPC campaigns whose name prefix doesn't map to a product group."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='Days back from today (default 30)')
        parser.add_argument('--marketplace',
                            help='Single marketplace code; defaults to all active')
        parser.add_argument('--show-mapped', action='store_true',
                            help='Also list mapped campaigns (verification mode)')
        parser.add_argument('--min-spend', type=float, default=0.0,
                            help='Skip campaigns whose total spend < this $ amount')

    # ---------------------------------------------------------------- helpers
    def _print_row(self, mp, ad_type, prefix, spend, last_seen, name, group=None, prefix_pad=12):
        line = (
            f'  {mp.upper():4s}  {ad_type:5s}  '
            f'{prefix:<{prefix_pad}s}  '
            f'${spend:>9,.2f}  '
            f'{str(last_seen):10s}  '
        )
        line += f'→ {group}  ' if group else ''
        line += (name[:60] + '…') if len(name) > 60 else name
        self.stdout.write(line)

    # ---------------------------------------------------------------- entry
    def handle(self, *args, **opts):
        cutoff = date.today() - timedelta(days=opts['days'])
        qs = PPCCampaignSnapshot.objects.filter(date__gte=cutoff)
        if opts['marketplace']:
            qs = qs.filter(marketplace=opts['marketplace'])

        agg = (qs.values('marketplace', 'campaign_name', 'campaign_type')
                 .annotate(spend=Sum('spend'), last_seen=Max('date'))
                 .order_by('-spend'))

        unmapped: list[dict] = []
        mapped:   list[dict] = []
        for r in agg:
            spend = float(r['spend'] or 0)
            if spend < opts['min_spend']:
                continue
            name   = r['campaign_name'] or ''
            prefix = _prefix(name)
            # Use the smart matcher (same logic the dashboard uses), not raw prefix lookup
            mapping = _match_campaign_to_group(name)
            row = {
                'marketplace': r['marketplace'],
                'name':        name,
                'type':        r['campaign_type'],
                'prefix':      prefix,
                'spend':       spend,
                'last_seen':   r['last_seen'],
                'group':       mapping,
            }
            (mapped if mapping else unmapped).append(row)

        # ────────────────────────── UNMAPPED ────────────────────────────────
        if not unmapped:
            self.stdout.write(self.style.SUCCESS(
                f'\n✓ No unmapped campaigns over the last {opts["days"]} days '
                f'— prefix map is complete.\n'
            ))
        else:
            total = sum(r['spend'] for r in unmapped)
            self.stdout.write(self.style.WARNING(
                f'\n⚠ {len(unmapped)} unmapped campaign(s) over last {opts["days"]} days '
                f'· total ${total:,.2f}'
            ))
            self.stdout.write(self.style.WARNING(
                '  These show up in "Unallocated PPC" on the dashboard.\n'
            ))
            self.stdout.write(
                f'  {"MP":4s}  {"TYPE":5s}  {"PREFIX":12s}  {"SPEND":>10s}  '
                f'{"LAST SEEN":10s}  CAMPAIGN'
            )
            self.stdout.write('  ' + '─' * 95)
            for r in unmapped:
                self._print_row(r['marketplace'], r['type'], r['prefix'],
                                r['spend'], r['last_seen'], r['name'])

            # ── Suggested additions ───────────────────────────────────────
            seen_prefixes = sorted({r['prefix'] for r in unmapped})
            self.stdout.write(self.style.NOTICE(
                f'\n  → Suggested additions to _CAMP_PREFIX_GROUP '
                f'(apps/amazon_api/views.py around line 230):\n'
            ))
            for p in seen_prefixes:
                sample = next((r['name'] for r in unmapped if r['prefix'] == p), '')
                self.stdout.write(
                    f"      '{p}': ('Product Type', 'N-Pack'),   "
                    f"# e.g. {sample[:60]}"
                )
            self.stdout.write('')

        # ────────────────────────── MAPPED (optional) ───────────────────────
        if opts['show_mapped'] and mapped:
            self.stdout.write(self.style.SUCCESS(
                f'\n✓ {len(mapped)} mapped campaign(s):\n'
            ))
            self.stdout.write(
                f'  {"MP":4s}  {"TYPE":5s}  {"PREFIX":12s}  {"SPEND":>10s}  '
                f'{"LAST SEEN":10s}  → GROUP                              CAMPAIGN'
            )
            self.stdout.write('  ' + '─' * 100)
            for r in mapped:
                group_label = f'{r["group"][0]} · {r["group"][1]}'
                self._print_row(r['marketplace'], r['type'], r['prefix'],
                                r['spend'], r['last_seen'], r['name'],
                                group=group_label.ljust(28))
