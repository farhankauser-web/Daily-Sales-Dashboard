"""
seed_ams_subscriptions — Refresh AdsStreamSubscription rows from Amazon's API.

Calls GET /streams/subscriptions for each configured marketplace and upserts
one AdsStreamSubscription row per (marketplace, dataset). Status, ARNs, and
S3 destination are all read from Amazon's response — no local guessing.

Idempotent: re-run anytime. Old subscriptions whose IDs no longer appear in
Amazon's response are not removed (set --prune to delete them).

Usage:
    python manage.py seed_ams_subscriptions                   # all configured MPs
    python manage.py seed_ams_subscriptions --marketplace usa
    python manage.py seed_ams_subscriptions --prune           # delete locally
                                                              # rows not in Amazon
    python manage.py seed_ams_subscriptions --dry-run
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


_API_ENDPOINTS = {
    'usa': 'https://advertising-api.amazon.com',
    'ca':  'https://advertising-api.amazon.com',
    'uk':  'https://advertising-api-eu.amazon.com',
    'de':  'https://advertising-api-eu.amazon.com',
    'ae':  'https://advertising-api-eu.amazon.com',
    'sa':  'https://advertising-api-eu.amazon.com',
}


class Command(BaseCommand):
    help = 'Refresh AdsStreamSubscription rows from the Amazon Ads streams API.'

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', default=None,
                            help='Single marketplace; defaults to every MP with Ads credentials')
        parser.add_argument('--prune', action='store_true',
                            help='Delete local rows whose subscription_id is no longer in Amazon')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be written; touch no rows.')

    def handle(self, *args, **opts):
        from apps.amazon_api.models import AmazonAPIConfig
        from apps.dashboard.models import AdsStreamSubscription

        mps = ([opts['marketplace']] if opts['marketplace']
               else list(AmazonAPIConfig.objects
                         .filter(is_active=True).values_list('marketplace', flat=True)))

        for mp in mps:
            cfg = AmazonAPIConfig.objects.filter(marketplace=mp, is_active=True).first()
            if not cfg or not cfg.has_ads_credentials():
                self.stdout.write(self.style.WARNING(
                    f'  [{mp}] no Ads credentials — skipping.'))
                continue

            endpoint = _API_ENDPOINTS.get(mp, 'https://advertising-api.amazon.com')
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n  [{mp.upper()}] refreshing subscriptions from {endpoint}'))

            tok = self._access_token(cfg)
            subs = self._list_subscriptions(endpoint, cfg, tok)
            if subs is None:
                continue

            seen_local_ids = set()
            for sub in subs:
                sid    = sub['subscriptionId']
                ds     = sub['dataSetId']
                status = sub.get('status', 'UNKNOWN')
                dest   = (sub.get('destination') or {}).get('firehoseDestination', {}) or {}
                seen_local_ids.add(sid)

                # Resolve bucket/prefix from settings (we know Firehose writes there)
                cfg_s3 = settings.AMS_S3.get(mp, {})

                fields = dict(
                    marketplace           = mp,
                    dataset_id            = ds,
                    status                = status,
                    delivery_stream_arn   = dest.get('deliveryStreamArn',   ''),
                    subscription_role_arn = dest.get('subscriptionRoleArn', ''),
                    subscriber_role_arn   = dest.get('subscriberRoleArn',   ''),
                    s3_bucket             = cfg_s3.get('bucket', ''),
                    s3_prefix             = cfg_s3.get('prefix', ''),
                    last_status_check     = timezone.now(),
                )

                if opts['dry_run']:
                    self.stdout.write(
                        f'    (dry-run) {ds:<15s}  status={status:<22s}  {sid}')
                    continue

                obj, created = AdsStreamSubscription.objects.update_or_create(
                    subscription_id=sid,
                    defaults=fields,
                )
                tag = '✚ added' if created else '↻ updated'
                self.stdout.write(
                    self.style.SUCCESS(
                        f'    {tag}  {ds:<15s}  status={status:<22s}  {sid}'))

            # --prune: drop rows for this MP whose IDs vanished from Amazon
            if opts['prune'] and not opts['dry_run']:
                stale = AdsStreamSubscription.objects.filter(marketplace=mp) \
                            .exclude(subscription_id__in=seen_local_ids)
                n = stale.count()
                if n:
                    stale.delete()
                    self.stdout.write(self.style.WARNING(
                        f'    ⊘ pruned {n} stale local subscription(s).'))

        self.stdout.write(self.style.SUCCESS('\n✅  Subscription sync complete.\n'))

    # ─────────────────────────────────────────────────────────────────────
    def _access_token(self, cfg) -> str:
        r = requests.post('https://api.amazon.com/auth/o2/token', data={
            'grant_type':    'refresh_token',
            'refresh_token': cfg.ads_refresh_token,
            'client_id':     cfg.ads_client_id,
            'client_secret': cfg.ads_client_secret,
        }, timeout=15)
        r.raise_for_status()
        return r.json()['access_token']

    def _list_subscriptions(self, endpoint, cfg, tok):
        r = requests.get(f'{endpoint}/streams/subscriptions', headers={
            'Amazon-Advertising-API-ClientId': cfg.ads_client_id,
            'Amazon-Advertising-API-Scope':    cfg.ads_profile_id,
            'Content-Type':  'application/vnd.MarketingStreamSubscriptions.StreamSubscriptionResource.v1.0+json',
            'Authorization': f'Bearer {tok}',
        }, timeout=20)
        if not r.ok:
            self.stderr.write(self.style.ERROR(
                f'    ✗ list failed: HTTP {r.status_code}  {r.text[:200]}'))
            return None
        return r.json().get('subscriptions', [])
