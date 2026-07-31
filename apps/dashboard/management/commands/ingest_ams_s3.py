"""
ingest_ams_s3 — Poll AMS-Firehose S3 buckets and ingest events.

For each configured marketplace:
  1. List S3 objects under `ams/marketplace=<mp>/…/*.gz`
  2. Skip objects already in `AmsProcessedObject` (exactly-once semantics)
  3. Download + gunzip + NDJSON-parse each new object
  4. Unwrap SNS envelopes, identify dataset, aggregate to (campaign, hour)
  5. Upsert PPCCampaignHourlySnapshot rows (separate traffic / conversion
     field-sets so partial coverage is preserved)
  6. Mark the object processed in AmsProcessedObject
  7. Log per-day AdsDataSyncLog 'sp_hourly' = 'ok' for any date that received
     at least one parsed event

Idempotent. Safe to run every minute. Honors --max-objects per run so a
backlog doesn't OOM us.

Usage:
    python manage.py ingest_ams_s3                          # all configured MPs
    python manage.py ingest_ams_s3 --marketplace usa
    python manage.py ingest_ams_s3 --marketplace usa --max-objects 50
    python manage.py ingest_ams_s3 --since 2026-06-10        # only objects with
                                                             # LastModified ≥ date
    python manage.py ingest_ams_s3 --dry-run                 # parse + report; no writes
"""
from __future__ import annotations

import gzip
import logging
from collections import defaultdict
from datetime import datetime, date as date_cls
from io import BytesIO
from typing import Iterable

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.dashboard.ams_consumer import (
    HourlyBucket, parse_envelope, infer_dataset, fold_into_bucket,
    iter_json_objects,
)
from apps.dashboard.completeness import log_sync

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Poll AMS S3 buckets, parse new events, upsert PPCCampaignHourlySnapshot.'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; defaults to every MP with an AMS_S3 entry.')
        parser.add_argument('--max-objects', type=int, default=200,
                            help='Cap S3 objects processed per marketplace per run (default 200).')
        parser.add_argument('--since', default=None,
                            help='Only list objects with LastModified ≥ YYYY-MM-DD (default: 7d ago).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print stats; do not write to the DB.')

    # ─────────────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        try:
            import boto3
        except ImportError:
            self.stderr.write(self.style.ERROR(
                'boto3 not installed. Run: pip install boto3'))
            return

        ams = getattr(settings, 'AMS_S3', {})
        if not ams:
            self.stderr.write(self.style.ERROR(
                'settings.AMS_S3 is empty — no buckets configured.'))
            return

        mps = ([opts['marketplace']] if opts['marketplace']
               else sorted(ams.keys()))

        # Date floor for the S3 LIST call (skip ancient objects).
        from datetime import timedelta, timezone as dt_tz
        if opts['since']:
            since_dt = datetime.fromisoformat(opts['since']).replace(tzinfo=dt_tz.utc)
        else:
            since_dt = timezone.now() - timedelta(days=7)

        for mp in mps:
            cfg = ams.get(mp)
            if not cfg:
                self.stderr.write(self.style.WARNING(
                    f'  [{mp}] no AMS_S3 config — skipping.'))
                continue

            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n  [{mp.upper()}] s3://{cfg["bucket"]}/{cfg["prefix"]}'))

            client = boto3.client(
                's3', region_name=cfg['region'],
                aws_access_key_id     = settings.AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY or None,
            )
            self._ingest_marketplace(client, mp, cfg, since_dt,
                                     opts['max_objects'], opts['dry_run'])

        self.stdout.write(self.style.SUCCESS('\n✅  AMS ingest complete.\n'))

    # ─────────────────────────────────────────────────────────────────────
    def _ingest_marketplace(self, s3, mp, cfg, since_dt, max_objects, dry_run):
        from apps.dashboard.models import (
            AmsProcessedObject, PPCCampaignHourlySnapshot, AdsStreamSubscription,
        )

        bucket = cfg['bucket']
        prefix = cfg['prefix']

        # 1) List new objects
        new_keys = self._list_new_objects(s3, bucket, prefix, since_dt, max_objects)
        if not new_keys:
            self.stdout.write('    ↳ no new objects.')
            return

        self.stdout.write(f'    ↳ {len(new_keys)} new object(s) to process')

        # 2) Find marketplace timezone for date/hour bucketing
        tz_name = settings.AMAZON_MARKETPLACES.get(mp, {}).get(
            'timezone', settings.TIME_ZONE)

        # 3) Process each, accumulating buckets across the batch
        all_buckets: dict[tuple, HourlyBucket] = {}
        per_object_stats = []   # (key, size, records_parsed, records_used)
        total_parsed = total_used = total_skipped = 0
        budget_records = 0

        for key, size in new_keys:
            obj = s3.get_object(Bucket=bucket, Key=key)
            data = obj['Body'].read()
            # Detect gzip from magic bytes (1f 8b) — Firehose may or may not
            # compress depending on its config; we tolerate both.
            if data[:2] == b'\x1f\x8b':
                raw = gzip.decompress(data).decode('utf-8', errors='replace')
            else:
                raw = data.decode('utf-8', errors='replace')

            n_parsed = n_used = 0
            for raw_obj in iter_json_objects(raw):
                payload, hint = parse_envelope(raw_obj)
                if not payload:
                    continue
                n_parsed += 1
                dataset = infer_dataset(payload, hint)
                if dataset is None:
                    total_skipped += 1
                    continue
                if dataset == 'budget-usage':
                    budget_records += 1
                    continue   # not folded into hourly snapshot
                if fold_into_bucket(all_buckets, mp, tz_name, payload, dataset):
                    n_used += 1

            total_parsed += n_parsed
            total_used   += n_used
            per_object_stats.append((key, size, n_parsed, n_used))

        self.stdout.write(
            f'    parsed={total_parsed}  used={total_used}  '
            f'skipped(unknown)={total_skipped}  budget-records={budget_records}'
        )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'    (dry-run) would upsert {len(all_buckets)} hourly buckets'))
            return

        # 4) Upsert buckets in two passes — traffic fields and conversion fields
        #    separately, so a hour that only saw traffic isn't blanked out by
        #    a later conversion-only update.
        traffic_objs    = []
        conversion_objs = []
        for b in all_buckets.values():
            common = dict(
                marketplace   = b.marketplace,
                date          = b.date,
                hour          = b.hour,
                campaign_id   = b.campaign_id,
                campaign_name = b.campaign_name,
                campaign_type = b.campaign_type,    # 'sp' | 'sb' | 'sd'
            )
            if b.saw_traffic:
                traffic_objs.append(PPCCampaignHourlySnapshot(
                    spend=b.spend, impressions=b.impressions, clicks=b.clicks,
                    **common,
                ))
            if b.saw_conversion:
                conversion_objs.append(PPCCampaignHourlySnapshot(
                    orders_7d=b.orders_7d, sales_7d=b.sales_7d, units_7d=b.units_7d,
                    **common,
                ))

        with transaction.atomic():
            if traffic_objs:
                PPCCampaignHourlySnapshot.objects.bulk_create(
                    traffic_objs,
                    update_conflicts=True,
                    update_fields=['spend', 'impressions', 'clicks', 'campaign_name'],
                    unique_fields=['marketplace', 'date', 'hour',
                                   'campaign_id', 'campaign_type'],
                )
            if conversion_objs:
                PPCCampaignHourlySnapshot.objects.bulk_create(
                    conversion_objs,
                    update_conflicts=True,
                    update_fields=['orders_7d', 'sales_7d', 'units_7d', 'campaign_name'],
                    unique_fields=['marketplace', 'date', 'hour',
                                   'campaign_id', 'campaign_type'],
                )

            # 5) Mark each S3 object processed (dedup ledger)
            AmsProcessedObject.objects.bulk_create([
                AmsProcessedObject(
                    marketplace=mp, s3_bucket=bucket, s3_key=key,
                    object_size=size, records_parsed=n_parsed, records_used=n_used,
                )
                for (key, size, n_parsed, n_used) in per_object_stats
            ], ignore_conflicts=True)

            # 6) Log per-day completeness, one row per (date, ad_product).
            #    We map campaign_type to the existing AdsDataSyncLog sources:
            #       sp → 'sp_hourly'
            #       sb → 'sb_daily'  (AMS is hourly — but the source label is
            #                         used by the completeness layer to gate
            #                         the SB metric column; the read side will
            #                         prefer real hourly when present)
            #       sd → 'sd_daily'
            log_sources = {'sp': 'sp_hourly', 'sb': 'sb_daily', 'sd': 'sd_daily'}
            grouped: dict[tuple[str, str], int] = {}
            for b in all_buckets.values():
                src = log_sources.get(b.campaign_type)
                if not src:
                    continue
                grouped[(b.date, src)] = grouped.get((b.date, src), 0) + 1
            dates_seen = sorted({d_iso for (d_iso, _) in grouped})

            for (d_iso, source), count in grouped.items():
                try:
                    log_sync(mp, date_cls.fromisoformat(d_iso),
                             source, 'ok', rows_received=count)
                except Exception as e:
                    logger.warning('AdsDataSyncLog write failed (%s/%s/%s): %s',
                                   mp, d_iso, source, e)

            # 7) Touch AdsStreamSubscription.last_ingest_at for live subs
            AdsStreamSubscription.objects.filter(
                marketplace=mp, status='ACTIVE').update(last_ingest_at=timezone.now())

        self.stdout.write(self.style.SUCCESS(
            f'    ✓ upserted {len(all_buckets)} hourly buckets · '
            f'logged {len(dates_seen)} day(s) as sp_hourly=ok'
        ))

    # ─────────────────────────────────────────────────────────────────────
    def _list_new_objects(self, s3, bucket, prefix, since_dt, max_objects):
        """
        Returns [(key, size), ...] for objects whose LastModified ≥ since_dt
        and which haven't been processed yet.

        Uses the AmsProcessedObject ledger for exactly-once semantics.

        NOTE: S3 lists keys in lexicographic order, and AMS Firehose keys
        ("m-1-YYYY-MM-DD-HH-MM-SS-<uuid>") sort chronologically — so we walk
        OLDEST-first. An earlier version capped the candidate list before
        deduping against the ledger, which deadlocked the ingest once the
        --since window held more already-processed objects than the cap: every
        run re-scanned the same oldest keys, found them all processed, and
        reported "no new objects" forever. We therefore dedup *incrementally*
        while paginating and stop only once we have `max_objects` genuinely
        new keys.
        """
        from apps.dashboard.models import AmsProcessedObject

        paginator = s3.get_paginator('list_objects_v2')
        new: list[tuple[str, int]] = []
        batch: list[tuple[str, int]] = []
        # How many keys to check against the ledger per query.
        probe = max(max_objects * 4, 500)

        def _drain(pending):
            """Filter `pending` against the ledger, appending unseen keys to `new`."""
            if not pending:
                return
            seen = set(
                AmsProcessedObject.objects
                .filter(s3_bucket=bucket, s3_key__in=[k for (k, _) in pending])
                .values_list('s3_key', flat=True)
            )
            for k, sz in pending:
                if k not in seen:
                    new.append((k, sz))

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in (page.get('Contents') or []):
                if obj['LastModified'] < since_dt:
                    continue
                # AMS Firehose writes gzipped content WITHOUT a .gz suffix;
                # the file extension can't be relied upon. We detect gzip from
                # the magic bytes during download instead.
                batch.append((obj['Key'], obj['Size']))
                if len(batch) >= probe:
                    _drain(batch)
                    batch = []
                    if len(new) >= max_objects:
                        return new[:max_objects]
            if len(new) >= max_objects:
                return new[:max_objects]

        _drain(batch)
        return new[:max_objects]
