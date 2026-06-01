"""
permissions_check_sqp — end-to-end probe of the Brand-Analytics SQP pipeline.

Walks every step (token → createReport → poll → getDocument → download → parse)
for the most recently completed ISO week, on every active marketplace.
Prints exact API responses and recovery hints when a step fails.

Usage:
    python manage.py permissions_check_sqp
    python manage.py permissions_check_sqp --marketplace usa
    python manage.py permissions_check_sqp --marketplace usa --asin B09NHVSLV4
    python manage.py permissions_check_sqp --max-wait 60
"""
from __future__ import annotations

import json
import time
import traceback

from django.core.management.base import BaseCommand

from apps.amazon_api.models import AmazonAPIConfig
from apps.amazon_api.services import LWATokenManager, SPAPIClient
from apps.sqp.sync import iter_sqp_rows, last_completed_iso_week


HINTS = {
    'lwa': (
        "→ LWA token exchange failed. Check refresh_token / lwa_client_id / lwa_client_secret "
        "for this marketplace in /api-config/. They must come from the *same* developer profile "
        "that's been granted the Brand Analytics role."
    ),
    'create_403': (
        "→ HTTP 403 on createReport — your SP-API app is authenticated but does NOT have the "
        "'Brand Analytics' role. In Seller Central → Apps and Services → Develop Apps, edit "
        "your private app, add the 'Brand Analytics' role, then re-authorize."
    ),
    'create_400': (
        "→ HTTP 400 on createReport — usually means the report type isn't enabled for this "
        "marketplace, or you're not Brand Registered for this marketplace. Verify Brand Registry "
        "covers the marketplace you're testing (US registration does NOT auto-cover CA/UK/etc)."
    ),
    'create_other': (
        "→ createReport failed. Paste the error body above to support; common causes: "
        "marketplaceId mismatch, date range outside the supported window (>2 yrs back), "
        "throttling (retry in 60s)."
    ),
    'poll_fatal': (
        "→ Report status FATAL — Amazon couldn't build the report. Usually transient; re-run. "
        "If persistent, check that the dataStartTime/dataEndTime align to a Monday-Sunday "
        "ISO week (Amazon rejects misaligned WEEK requests)."
    ),
    'download_fail': (
        "→ Download of the report document failed. Network issue or expired URL (URLs expire "
        "after a few minutes; re-run to get a fresh one)."
    ),
    'parse_empty': (
        "→ Report downloaded successfully but contains 0 rows. Either: (a) your brand had no "
        "search traffic this week, or (b) the asin parameter doesn't match a product you own."
    ),
}


class Command(BaseCommand):
    help = "Verify end-to-end SP-API SQP access (auth → createReport → poll → download → parse)."

    def add_arguments(self, parser):
        parser.add_argument('--marketplace', help='Single marketplace code; defaults to all active')
        parser.add_argument('--asin',        help='ASIN-level probe instead of brand-level')
        parser.add_argument('--max-wait',    type=int, default=180,
                            help='Seconds to wait for the report (default 180)')

    # ---------------------------------------------------------------- helpers
    def _ok(self, msg):
        self.stdout.write(self.style.SUCCESS(f'  ✓ {msg}'))

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(f'  ⚠ {msg}'))

    def _err(self, msg):
        self.stdout.write(self.style.ERROR(f'  ✗ {msg}'))

    def _hint(self, key):
        self.stdout.write(self.style.NOTICE(f'    {HINTS.get(key, "")}'))

    # ---------------------------------------------------------------- entry
    def handle(self, *args, **opts):
        mps = ([opts['marketplace']] if opts['marketplace']
               else list(AmazonAPIConfig.objects.filter(is_active=True)
                                                 .values_list('marketplace', flat=True)))
        if not mps:
            self.stdout.write(self.style.ERROR('No active SP-API configurations found in /api-config/.'))
            return

        mon, sun = last_completed_iso_week()
        self.stdout.write(f'Probing ISO week {mon} → {sun}  '
                          f'(scope: {"asin=" + opts["asin"] if opts["asin"] else "brand-level"})\n')

        for mp in mps:
            self.stdout.write(self.style.HTTP_INFO(f'\n=== [{mp.upper()}] ==='))
            cfg = AmazonAPIConfig.objects.filter(marketplace=mp, is_active=True).first()
            if not cfg:
                self._err('No active config row.')
                continue
            if not cfg.has_sp_api_credentials():
                self._err('Config row exists but SP-API credentials are blank.')
                continue

            # 1. LWA
            try:
                LWATokenManager.get_access_token(cfg)
                self._ok('LWA token exchange OK')
            except Exception as exc:
                self._err(f'LWA failed: {exc}')
                self._hint('lwa')
                continue

            client = SPAPIClient(cfg)

            # 2. createReport
            try:
                report_id = client.request_sqp_report(
                    mon.isoformat(), sun.isoformat(),
                    period_type='WEEK', asin=opts['asin'],
                )
                self._ok(f'createReport OK · reportId={report_id}')
            except Exception as exc:
                msg = str(exc)
                self._err(f'createReport failed: {msg}')
                if 'HTTP 403' in msg:
                    self._hint('create_403')
                elif 'HTTP 400' in msg:
                    self._hint('create_400')
                else:
                    self._hint('create_other')
                continue

            # 3. Poll
            deadline = time.time() + opts['max_wait']
            last_status = ''
            document_id = None
            poll_start = time.time()
            while time.time() < deadline:
                try:
                    meta = client.get_report_status(report_id)
                    last_status = meta.get('processingStatus', '')
                    if last_status == 'DONE':
                        document_id = meta.get('reportDocumentId')
                        self._ok(f'Report DONE in {int(time.time() - poll_start)}s · '
                                 f'documentId={document_id}')
                        break
                    if last_status in ('CANCELLED', 'FATAL'):
                        break
                except Exception as exc:
                    self._warn(f'Poll error (will retry): {exc}')
                time.sleep(3)

            if last_status != 'DONE':
                self._err(f'Report did not complete: last status={last_status}')
                if last_status == 'FATAL':
                    self._hint('poll_fatal')
                else:
                    self._warn(f'Still {last_status} after {opts["max_wait"]}s. '
                               f'Re-run later with --max-wait higher.')
                continue

            # 4. Download
            try:
                payload = client.download_sqp_report(document_id)
                self._ok(f'Download + JSON parse OK '
                         f'(top-level keys: {list(payload.keys())[:6]})')
            except Exception as exc:
                self._err(f'Download failed: {exc}')
                self._hint('download_fail')
                continue

            # 5. Parse rows
            rows = list(iter_sqp_rows(payload))
            if not rows:
                self._warn(f'Parsed 0 rows from payload.')
                self._hint('parse_empty')
                continue

            sample = rows[0]
            sq_text = ((sample.get('searchQueryData') or {}).get('searchQuery') or '')[:60]
            asin_in_row = sample.get('asin', '')
            self._ok(f'Parsed {len(rows)} rows · sample: '
                     f'asin={asin_in_row or "(brand)"} query="{sq_text}"')

            # 6. Full one-row dump for visibility
            self.stdout.write('\n  Sample row (truncated):')
            try:
                self.stdout.write('  ' + json.dumps(sample, indent=2, default=str)[:800])
            except Exception:
                self.stdout.write(self.style.WARNING('  (could not pretty-print sample row)'))

        self.stdout.write('\nDone.')
