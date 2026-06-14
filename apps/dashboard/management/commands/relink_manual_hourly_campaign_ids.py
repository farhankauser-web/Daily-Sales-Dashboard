"""
Re-key existing `source='manual'` PPCCampaignHourlySnapshot rows whose
campaign_id is a slugged name (legacy fallback) so they point at the real
numeric Amazon campaign_id from the Campaign dimension.

The manual CSV importer used to store a slugged copy of the campaign name as
the campaign_id because Seller Central's hourly export has no ID column.
That meant manual rows lived under a different key than the AMS rows and
were invisible on Campaign Detail (which loads by numeric ID).

Usage:
    manage.py relink_manual_hourly_campaign_ids --marketplace usa --dry-run
    manage.py relink_manual_hourly_campaign_ids --marketplace usa
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.dashboard.manual_hourly_importer import _norm_name


class Command(BaseCommand):
    help = 'Repoint manual hourly rows from slugged name → real numeric campaign_id.'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default='usa')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, marketplace: str, dry_run: bool, **_):
        from apps.dashboard.models import (
            Campaign, PPCCampaignSnapshot, PPCCampaignHourlySnapshot,
        )

        # Build name → real_id map (Campaign dim preferred, snapshot rollup
        # as fallback for names that aren't in the dim yet)
        name_to_id: dict[str, str] = {}
        for cid, cname in Campaign.objects.filter(marketplace=marketplace).values_list(
                'campaign_id', 'campaign_name'):
            key = _norm_name(cname)
            if key and key not in name_to_id:
                name_to_id[key] = cid
        for cid, cname in PPCCampaignSnapshot.objects.filter(
                marketplace=marketplace,
            ).values_list('campaign_id', 'campaign_name').distinct():
            key = _norm_name(cname)
            if key and key not in name_to_id:
                name_to_id[key] = cid

        self.stdout.write(f'name → id map built: {len(name_to_id)} campaigns')

        # Find rows to re-key. A row is a candidate when its current campaign_id
        # doesn't match the real id we'd assign by name.
        qs = PPCCampaignHourlySnapshot.objects.filter(
            marketplace=marketplace, source='manual',
        ).values('id', 'campaign_id', 'campaign_name', 'date', 'hour', 'campaign_type')

        to_update_per_target: dict[str, list[int]] = {}
        unmatched_names: dict[str, int] = {}
        kept = 0
        scanned = 0
        for row in qs.iterator(chunk_size=5000):
            scanned += 1
            target_cid = name_to_id.get(_norm_name(row['campaign_name'] or ''))
            if not target_cid:
                unmatched_names[row['campaign_name'] or '?'] = \
                    unmatched_names.get(row['campaign_name'] or '?', 0) + 1
                continue
            if target_cid == row['campaign_id']:
                kept += 1
                continue
            to_update_per_target.setdefault(target_cid, []).append(row['id'])

        n_targets = len(to_update_per_target)
        n_rows = sum(len(v) for v in to_update_per_target.values())
        self.stdout.write(f'scanned: {scanned}')
        self.stdout.write(f'already correct: {kept}')
        self.stdout.write(f'unmatched names: {len(unmatched_names)} '
                          f'(rows: {sum(unmatched_names.values())})')
        self.stdout.write(f'WILL re-key: {n_rows} rows across {n_targets} campaigns')

        if unmatched_names:
            self.stdout.write('  sample unmatched (top 5):')
            for n, c in sorted(unmatched_names.items(),
                                key=lambda kv: -kv[1])[:5]:
                self.stdout.write(f'    {c:>5}× {n[:80]}')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN — no writes performed'))
            return

        # Re-key per target campaign. Design contract: manual supersedes AMS
        # for the same (campaign, date, hour). So if an AMS row already exists
        # at the target key, we DROP the AMS row and re-key the manual row in.
        updated_total = 0
        deleted_ams_conflicts = 0
        with transaction.atomic():
            for target_cid, orphan_ids in to_update_per_target.items():
                orphans = list(PPCCampaignHourlySnapshot.objects.filter(
                    id__in=orphan_ids,
                ).values('id', 'date', 'hour', 'campaign_type'))

                orphan_keys = {(o['date'], o['hour'], o['campaign_type'])
                                for o in orphans}

                # Find AMS rows that would collide with the re-keyed manuals
                ams_colliding_ids = list(PPCCampaignHourlySnapshot.objects.filter(
                    marketplace=marketplace,
                    campaign_id=target_cid,
                    date__in={k[0] for k in orphan_keys},
                ).exclude(source='manual').values_list(
                    'id', 'date', 'hour', 'campaign_type'))
                drop_ids = [
                    rid for (rid, d, h, ct) in ams_colliding_ids
                    if (d, h, ct) in orphan_keys
                ]
                if drop_ids:
                    PPCCampaignHourlySnapshot.objects.filter(
                        id__in=drop_ids).delete()
                    deleted_ams_conflicts += len(drop_ids)

                n = PPCCampaignHourlySnapshot.objects.filter(
                    id__in=[o['id'] for o in orphans],
                ).update(campaign_id=target_cid)
                updated_total += n

        self.stdout.write(self.style.SUCCESS(
            f'DONE: re-keyed {updated_total} manual rows · '
            f'dropped {deleted_ams_conflicts} superseded AMS rows'))
