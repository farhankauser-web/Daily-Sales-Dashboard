"""
apps/amazon_api/services.py — SP-API + Ads API client wrappers
"""
import csv
import gzip
import io
import json
import logging
import time
import zlib
from datetime import datetime, timedelta, date, time as dtime, timezone
from zoneinfo import ZoneInfo

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ── In-memory cache for FlatFileAllOrdersReport rows ─────────────────────────
# Reports take 1-5 min to generate. We cache by (marketplace, start_iso, end_iso)
# with a short TTL so the dashboard stays responsive across refreshes.
_REPORT_CACHE: dict = {}        # key → (timestamp, parsed_rows)
_REPORT_INFLIGHT: dict = {}     # key → reportId currently being generated
_REPORT_TTL_SECONDS = 600       # 10 minutes


def _extract_http_error_detail(resp: requests.Response) -> str:
    """Return readable API error details including response payload."""
    body_text = (resp.text or '').strip()
    try:
        payload = resp.json()
        err = payload.get('error')
        desc = payload.get('error_description') or payload.get('message') or payload.get('detail')
        if err and desc:
            return f'HTTP {resp.status_code} {err}: {desc}'
        if err:
            return f'HTTP {resp.status_code} {err}'
        return f'HTTP {resp.status_code}: {json.dumps(payload)}'
    except Exception:
        return f'HTTP {resp.status_code}: {body_text[:500] or "No response body"}'


class LWATokenManager:
    """
    Login With Amazon (LWA) OAuth token manager.
    Handles refresh_token → access_token exchange with caching.
    """
    _cache = {}   # {config_id: (access_token, expires_at)}

    @classmethod
    def get_access_token(cls, config) -> str:
        now = time.time()
        cached = cls._cache.get(config.pk)
        if cached and now < cached[1] - 60:
            return cached[0]

        resp = requests.post(
            'https://api.amazon.com/auth/o2/token',
            data={
                'grant_type':    'refresh_token',
                'refresh_token': config.refresh_token,
                'client_id':     config.lwa_client_id,
                'client_secret': config.lwa_client_secret,
            },
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f'LWA token request failed: {_extract_http_error_detail(resp)}')
        data = resp.json()
        access_token = data['access_token']
        expires_in   = int(data.get('expires_in', 3600))

        cls._cache[config.pk] = (access_token, now + expires_in)
        return access_token


class SPAPIClient:
    """
    Amazon Selling Partner API client.
    Endpoints used: Orders, Sales & Traffic (Business Report), Inventory.
    """

    def __init__(self, config):
        self.config   = config
        self.mp_info  = settings.AMAZON_MARKETPLACES.get(config.marketplace, {})
        self.endpoint = self.mp_info.get('endpoint', 'https://sellingpartnerapi-na.amazon.com')
        self.mp_id    = config.marketplace_id or self.mp_info.get('id', '')

    def _headers(self) -> dict:
        token = LWATokenManager.get_access_token(self.config)
        return {
            'x-amz-access-token': token,
            'Content-Type': 'application/json',
        }

    def _get(self, path: str, params: dict = None, timeout: int = 20) -> dict:
        resp = requests.get(
            f'{self.endpoint}{path}',
            headers=self._headers(),
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def test_connection(self) -> dict:
        """Hit the Marketplace Participations endpoint as a health check."""
        return self._get('/sellers/v1/marketplaceParticipations')

    def get_sales_data(self, date_range: str = 'today', start_date: str = None, end_date: str = None) -> dict:
        """
        Fetch sales & traffic using the Sales Analytics API.
        date_range: 'today' | 'yesterday' | 'mtd' | '7d' | '30d'
        """
        start_local, end_local, tz_name = self._resolve_local_dates(
            date_range, start_date=start_date, end_date=end_date, marketplace=self.config.marketplace
        )
        start_utc, end_utc = self._local_range_to_utc_interval(start_local, end_local, tz_name)

        # Sales & Traffic (requires Selling Partner Insights role)
        resp = self._get(
            '/sales/v1/orderMetrics',
            params={
                'marketplaceIds': self.mp_id,
                'interval':       f'{start_utc}--{end_utc}',
                'granularity':    'Day',
            }
        )
        return resp

    def get_inventory(self) -> dict:
        """FBA Inventory Summaries."""
        return self._get(
            '/fba/inventory/v1/summaries',
            params={'marketplaceIds': self.mp_id, 'details': True}
        )

    def get_orders(self, date_range: str = 'today', start_date: str = None, end_date: str = None) -> dict:
        start_local, end_local, tz_name = self._resolve_local_dates(
            date_range, start_date=start_date, end_date=end_date, marketplace=self.config.marketplace
        )
        start_utc, end_utc = self._local_range_to_utc_created_after_before(start_local, end_local, tz_name)
        return self._get(
            '/orders/v0/orders',
            params={
                'MarketplaceIds':     self.mp_id,
                'CreatedAfter':       start_utc,
                'CreatedBefore':      end_utc,
                'OrderStatuses':      'Unshipped,PartiallyShipped,Shipped',
            }
        )

    def get_order_items(self, amazon_order_id: str) -> dict:
        return self._get(f'/orders/v0/orders/{amazon_order_id}/orderItems')

    def get_orders_paged(self, date_range: str = 'today', start_date: str = None, end_date: str = None, max_pages: int = 5):
        """
        Fetch orders with NextToken pagination (up to max_pages).
        Includes Pending orders (matches the FlatFileAllOrdersReport view).
        Cancelled / Unfulfillable orders are excluded by the caller.
        """
        start_local, end_local, tz_name = self._resolve_local_dates(
            date_range, start_date=start_date, end_date=end_date, marketplace=self.config.marketplace
        )
        start_utc, end_utc = self._local_range_to_utc_created_after_before(start_local, end_local, tz_name)

        all_orders = []
        next_token = None
        pages = 0
        while pages < max_pages:
            if next_token:
                params = {'NextToken': next_token}
            else:
                # No OrderStatuses filter → SP-API returns ALL statuses (including
                # Pending orders, which the report view also includes). The view
                # layer drops Canceled / Unfulfillable.
                params = {
                    'MarketplaceIds': self.mp_id,
                    'CreatedAfter': start_utc,
                    'CreatedBefore': end_utc,
                }
            resp = self._get('/orders/v0/orders', params=params, timeout=30)
            payload = (resp or {}).get('payload', {}) if isinstance(resp, dict) else {}
            orders = (payload or {}).get('Orders', []) if isinstance(payload, dict) else []
            all_orders.extend(orders)
            next_token = (payload or {}).get('NextToken') if isinstance(payload, dict) else None
            pages += 1
            if not next_token:
                break
        return all_orders

    # ── REPORTS API: FlatFileAllOrdersReport ─────────────────────────────────
    REPORT_TYPE_ALL_ORDERS = 'GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL'

    def request_orders_report(self, start_iso: str, end_iso: str) -> str:
        """Submit a FlatFileAllOrdersReport request. Returns reportId."""
        body = {
            'reportType': self.REPORT_TYPE_ALL_ORDERS,
            'marketplaceIds': [self.mp_id],
            'dataStartTime': start_iso,
            'dataEndTime':   end_iso,
        }
        resp = requests.post(
            f'{self.endpoint}/reports/2021-06-30/reports',
            headers=self._headers(),
            json=body,
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f'createReport failed: {_extract_http_error_detail(resp)}')
        return resp.json()['reportId']

    def get_report_status(self, report_id: str) -> dict:
        """processingStatus: IN_QUEUE | IN_PROGRESS | DONE | CANCELLED | FATAL."""
        return self._get(f'/reports/2021-06-30/reports/{report_id}')

    def get_report_document_meta(self, document_id: str) -> dict:
        """Returns {url, compressionAlgorithm?} for downloading the report."""
        return self._get(f'/reports/2021-06-30/documents/{document_id}')

    @staticmethod
    def _decompress_if_needed(raw: bytes, compression: str) -> bytes:
        if not compression:
            return raw
        algo = compression.upper()
        if algo == 'GZIP':
            try:
                return gzip.decompress(raw)
            except OSError:
                # Some Amazon documents are raw deflate streams under "GZIP"
                return zlib.decompress(raw, -zlib.MAX_WBITS)
        return raw

    def download_orders_report(self, document_id: str) -> list:
        """Download + parse the All Orders TSV. Returns list of dict rows."""
        meta = self.get_report_document_meta(document_id)
        url  = meta['url']
        comp = meta.get('compressionAlgorithm') or ''

        r = requests.get(url, timeout=60)
        r.raise_for_status()
        body = self._decompress_if_needed(r.content, comp)
        text = body.decode('utf-8-sig', errors='replace')
        reader = csv.DictReader(io.StringIO(text), delimiter='\t')
        return list(reader)

    def fetch_orders_report_sync(
        self,
        date_range: str = 'today',
        start_date: str = None,
        end_date:   str = None,
        max_wait_seconds: int = 25,
        progress_cb=None,
    ) -> dict:
        """
        Synchronously fetch the FlatFileAllOrdersReport for the date range.
        Cached aggressively (TTL = 10 min). If a report can't be produced
        within max_wait_seconds, returns {'rows': None, 'status': '...'}
        so the caller can fall back to the live Orders API.
        """
        start_local, end_local, tz_name = self._resolve_local_dates(
            date_range, start_date=start_date, end_date=end_date,
            marketplace=self.config.marketplace,
        )
        # Reports API uses UTC-aware ISO timestamps; the orderMetrics window
        # converter already gives us those in the marketplace's local TZ.
        start_iso, end_iso = self._local_range_to_utc_interval(start_local, end_local, tz_name)

        cache_key = (self.config.marketplace, start_iso, end_iso)

        # Fresh cache hit
        cached = _REPORT_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _REPORT_TTL_SECONDS:
            return {'rows': cached[1], 'status': 'CACHED', 'age_seconds': int(time.time() - cached[0])}

        # Re-use any in-flight report (don't keep submitting duplicates)
        report_id = _REPORT_INFLIGHT.get(cache_key)
        if not report_id:
            try:
                report_id = self.request_orders_report(start_iso, end_iso)
                _REPORT_INFLIGHT[cache_key] = report_id
            except Exception as exc:
                logger.error('createReport failed: %s', exc)
                return {'rows': None, 'status': f'CREATE_FAILED: {exc}'}

        # Poll for completion
        deadline = time.time() + max_wait_seconds
        last_status = 'IN_QUEUE'
        last_progress = time.time()
        while time.time() < deadline:
            try:
                meta = self.get_report_status(report_id)
                last_status = meta.get('processingStatus', '')
                if progress_cb and (time.time() - last_progress) >= 30:
                    elapsed = int(time.time() - (deadline - max_wait_seconds))
                    progress_cb(f'  …{elapsed}s elapsed, status={last_status}, reportId={report_id}')
                    last_progress = time.time()
                if last_status == 'DONE':
                    doc_id = meta.get('reportDocumentId')
                    if not doc_id:
                        _REPORT_INFLIGHT.pop(cache_key, None)
                        return {'rows': None, 'status': 'DONE_NO_DOCUMENT'}
                    rows = self.download_orders_report(doc_id)
                    _REPORT_CACHE[cache_key] = (time.time(), rows)
                    _REPORT_INFLIGHT.pop(cache_key, None)
                    return {'rows': rows, 'status': 'FRESH', 'report_id': report_id}
                if last_status in ('CANCELLED', 'FATAL'):
                    _REPORT_INFLIGHT.pop(cache_key, None)
                    return {'rows': None, 'status': last_status}
            except Exception as exc:
                logger.warning('getReport poll error: %s', exc)
            time.sleep(2.5)

        # Still pending — return whatever stale cache we have, otherwise None
        if cached:
            return {'rows': cached[1], 'status': f'PENDING (using stale cache)',
                    'age_seconds': int(time.time() - cached[0])}
        return {'rows': None, 'status': f'PENDING ({last_status})', 'report_id': report_id}

    # ── REPORTS API: Brand-Analytics Search Query Performance ────────────────
    REPORT_TYPE_SQP = 'GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT'

    def request_sqp_report(
        self,
        period_start: str,
        period_end:   str,
        period_type:  str = 'WEEK',
        asin:         str = None,
    ) -> str:
        """
        Create a Brand-Analytics SQP report. Returns reportId.

        period_start / period_end are ISO YYYY-MM-DD strings.
        period_type ∈ {'WEEK','MONTH','QUARTER'} — passed through verbatim.
        asin=None  → brand-level report (all your ASINs aggregated per query).
        asin='B…'  → ASIN-level report (one report per ASIN per period).
        """
        report_options = {'reportPeriod': period_type}
        if asin:
            report_options['asin'] = asin
        body = {
            'reportType':     self.REPORT_TYPE_SQP,
            'marketplaceIds': [self.mp_id],
            'dataStartTime':  f'{period_start}T00:00:00Z',
            'dataEndTime':    f'{period_end}T23:59:59Z',
            'reportOptions':  report_options,
        }
        resp = requests.post(
            f'{self.endpoint}/reports/2021-06-30/reports',
            headers=self._headers(),
            json=body,
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(
                f'createReport(SQP) failed: {_extract_http_error_detail(resp)}'
            )
        return resp.json()['reportId']

    def download_sqp_report(self, document_id: str) -> dict:
        """
        Download + parse a completed SQP report document.
        SQP reports are JSON (not TSV), gzipped. Returns the parsed dict.
        """
        meta = self.get_report_document_meta(document_id)
        url  = meta['url']
        comp = meta.get('compressionAlgorithm') or ''

        r = requests.get(url, timeout=90)
        r.raise_for_status()
        body = self._decompress_if_needed(r.content, comp)
        return json.loads(body.decode('utf-8-sig', errors='replace'))

    def fetch_sqp_report_sync(
        self,
        period_start: str,
        period_end:   str,
        period_type:  str = 'WEEK',
        asin:         str = None,
        max_wait_seconds: int = 300,
        progress_cb=None,
    ) -> dict:
        """
        End-to-end: create report → poll → download → parse.
        Returns {'data': parsed_json, 'status': '...', 'report_id': '...'}
        If the report doesn't finish within max_wait_seconds, returns
        {'data': None, 'status': 'PENDING (...)', 'report_id': '...'}
        so the caller can persist the reportId and re-poll later.
        """
        try:
            report_id = self.request_sqp_report(period_start, period_end, period_type, asin)
        except Exception as exc:
            return {'data': None, 'status': f'CREATE_FAILED: {exc}', 'report_id': None}

        deadline = time.time() + max_wait_seconds
        last_status   = 'IN_QUEUE'
        last_progress = time.time()
        while time.time() < deadline:
            try:
                meta = self.get_report_status(report_id)
                last_status = meta.get('processingStatus', '')
                if progress_cb and (time.time() - last_progress) >= 30:
                    elapsed = int(time.time() - (deadline - max_wait_seconds))
                    progress_cb(f'  …{elapsed}s elapsed, status={last_status}, reportId={report_id}')
                    last_progress = time.time()
                if last_status == 'DONE':
                    doc_id = meta.get('reportDocumentId')
                    if not doc_id:
                        return {'data': None, 'status': 'DONE_NO_DOCUMENT', 'report_id': report_id}
                    data = self.download_sqp_report(doc_id)
                    return {'data': data, 'status': 'FRESH', 'report_id': report_id}
                if last_status in ('CANCELLED', 'FATAL'):
                    return {'data': None, 'status': last_status, 'report_id': report_id}
            except Exception as exc:
                logger.warning('getReport(SQP) poll error: %s', exc)
            time.sleep(3)
        return {'data': None, 'status': f'PENDING ({last_status})', 'report_id': report_id}

    @staticmethod
    def _marketplace_tz(marketplace: str = None) -> str:
        tz_name = settings.TIME_ZONE
        if marketplace:
            tz_name = settings.AMAZON_MARKETPLACES.get(marketplace, {}).get('timezone', settings.TIME_ZONE)
        return tz_name

    @classmethod
    def _resolve_local_dates(cls, date_range: str, start_date: str = None, end_date: str = None, marketplace: str = None):
        tz_name = cls._marketplace_tz(marketplace)
        today = datetime.now(tz=ZoneInfo(tz_name)).date()
        if date_range == 'custom' and start_date and end_date:
            s = datetime.strptime(start_date, '%Y-%m-%d').date()
            e = datetime.strptime(end_date, '%Y-%m-%d').date()
            return s, e, tz_name
        if date_range == 'today':
            return today, today, tz_name
        elif date_range == 'yesterday':
            d = today - timedelta(days=1)
            return d, d, tz_name
        elif date_range == 'mtd':
            return today.replace(day=1), today, tz_name
        elif date_range == '7d':
            return today - timedelta(days=7), today, tz_name
        elif date_range == '30d':
            return today - timedelta(days=30), today, tz_name
        return today, today, tz_name

    @staticmethod
    def _iso_z(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

    @classmethod
    def _local_range_to_utc_interval(cls, start_local: date, end_local: date, tz_name: str):
        tzinfo = ZoneInfo(tz_name)
        start_dt = datetime.combine(start_local, dtime(0, 0, 0), tzinfo=tzinfo)
        end_dt = datetime.combine(end_local, dtime(23, 59, 59), tzinfo=tzinfo)
        return cls._iso_z(start_dt), cls._iso_z(end_dt)

    @classmethod
    def _local_range_to_utc_created_after_before(cls, start_local: date, end_local: date, tz_name: str):
        # Orders API requires CreatedBefore <= now-2min (Amazon delay window).
        tzinfo = ZoneInfo(tz_name)
        start_dt = datetime.combine(start_local, dtime(0, 0, 0), tzinfo=tzinfo)
        end_dt_eod = datetime.combine(end_local, dtime(23, 59, 59), tzinfo=tzinfo)

        now_local = datetime.now(tz=tzinfo)
        cutoff = now_local - timedelta(minutes=2)
        end_dt = min(end_dt_eod, cutoff)
        if end_dt < start_dt:
            end_dt = cutoff
        return cls._iso_z(start_dt), cls._iso_z(end_dt)


class AdsAPIClient:
    """
    Amazon Advertising API client.
    Fetches campaign-level performance metrics.
    """
    ADS_ENDPOINT = 'https://advertising-api.amazon.com'

    def __init__(self, config):
        self.config     = config
        self.profile_id = config.ads_profile_id

    def _headers(self) -> dict:
        # Ads API uses separate OAuth credentials
        token = self._get_ads_token()
        return {
            'Authorization':    f'Bearer {token}',
            'Amazon-Advertising-API-ClientId': self.config.ads_client_id,
            'Amazon-Advertising-API-Scope':    self.profile_id,
            'Content-Type': 'application/json',
        }

    def _get_ads_token(self) -> str:
        resp = requests.post(
            'https://api.amazon.com/auth/o2/token',
            data={
                'grant_type':    'refresh_token',
                'refresh_token': self.config.ads_refresh_token,
                'client_id':     self.config.ads_client_id,
                'client_secret': self.config.ads_client_secret,
            },
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f'Ads token request failed: {_extract_http_error_detail(resp)}')
        return resp.json()['access_token']

    def get_campaign_summary(self, date_range: str = 'today',
                             existing_report_id: str = None) -> dict:
        """
        Fetch SP campaign metrics via the Advertising API v3 Reporting API (async).

        Reports typically take 15-25 minutes to complete on large accounts.
        Flow:
          • First call  → submits report, polls 30 s, returns {'status':'pending','report_id':...}
          • Later calls → pass existing_report_id to check/download without re-submitting

        The dashboard should store the report_id and poll via check_report_status().
        """
        headers = self._headers()

        # ── If caller already has a report in-flight, just check it ──────────
        if existing_report_id:
            return self._check_and_download(existing_report_id, headers)

        # ── Submit a new report ───────────────────────────────────────────────
        start, end, _tz = SPAPIClient._resolve_local_dates(date_range, marketplace=self.config.marketplace)
        start_str = str(start)
        end_str   = str(end)

        create_resp = requests.post(
            f'{self.ADS_ENDPOINT}/reporting/reports',
            headers=headers,
            json={
                'name': f'SP Campaigns {end_str}',
                'startDate': start_str,
                'endDate':   end_str,
                'configuration': {
                    'adProduct':    'SPONSORED_PRODUCTS',
                    'groupBy':      ['campaign'],
                    'columns':      ['campaignId', 'campaignName', 'impressions', 'clicks',
                                     'cost', 'purchases7d', 'sales7d', 'costPerClick',
                                     'clickThroughRate'],
                    'reportTypeId': 'spCampaigns',
                    'timeUnit':     'SUMMARY',
                    'format':       'GZIP_JSON',
                },
            },
            timeout=20,
        )
        if not create_resp.ok:
            # 425 = Amazon deduplication — re-use the existing report
            if create_resp.status_code == 425:
                report_id = create_resp.json().get('detail', '').split(': ')[-1].strip()
            else:
                raise RuntimeError(f'Ads report create failed: {_extract_http_error_detail(create_resp)}')
        else:
            report_id = create_resp.json()['reportId']

        # ── Quick poll (30 s) — download immediately if it's already done ────
        for _ in range(6):
            time.sleep(5)
            result = self._check_and_download(report_id, headers)
            if result['status'] in ('ok', 'error'):
                return result

        # ── Not ready yet — return pending so the caller can poll later ───────
        logger.info('Ads report submitted, processing (report_id=%s). '
                    'Reports on large accounts take 15-25 min.', report_id)
        return {'status': 'pending', 'report_id': report_id, 'campaigns': [], 'date': end_str}

    def check_report_status(self, report_id: str) -> dict:
        """Poll an in-flight report and download it once COMPLETED."""
        return self._check_and_download(report_id, self._headers())

    def _check_and_download(self, report_id: str, headers: dict) -> dict:
        """Internal: check one report and download if COMPLETED."""
        status_resp = requests.get(
            f'{self.ADS_ENDPOINT}/reporting/reports/{report_id}',
            headers=headers,
            timeout=15,
        )
        if not status_resp.ok:
            raise RuntimeError(f'Ads report status failed: {_extract_http_error_detail(status_resp)}')
        data  = status_resp.json()
        state = data.get('status', '')

        if state == 'COMPLETED':
            dl = requests.get(data['url'], timeout=60)
            dl.raise_for_status()
            rows = json.loads(gzip.decompress(dl.content).decode('utf-8'))
            return {'status': 'ok', 'campaigns': rows, 'report_id': report_id}
        if state in ('FAILED', 'CANCELLED'):
            return {'status': 'error', 'report_id': report_id,
                    'error': data.get('failureReason', state), 'campaigns': []}
        return {'status': 'pending', 'report_id': report_id, 'campaigns': []}

    def get_all_campaigns_summary(self, date_range: str = 'today',
                                   existing_sp_id: str = None,
                                   existing_sb_id: str = None,
                                   existing_sd_id: str = None) -> dict:
        """
        Fetch SP + SB + SD campaign totals in a single combined call.
        Submits new reports (or polls existing ones) for all three ad types,
        then combines them into one result.

        SP uses 7d attribution; SB and SD use 14d attribution.
        Returns:
          { status, sp_report_id, sb_report_id, sd_report_id,
            total_spend, sp_spend, sb_spend, sd_spend,
            campaigns (tagged with _adType), date }
        status = 'ok'      if at least one type returned data
                 'pending'  if no type has returned yet
        """
        headers = self._headers()
        start, end, _tz = SPAPIClient._resolve_local_dates(
            date_range, marketplace=self.config.marketplace)
        start_str, end_str = str(start), str(end)

        SP_COLS = ['campaignId', 'campaignName', 'impressions', 'clicks', 'cost',
                   'purchases7d', 'sales7d', 'clickThroughRate']
        SB_COLS = ['campaignId', 'campaignName', 'impressions', 'clicks', 'cost',
                   'purchasesClicks', 'sales']
        SD_COLS = ['campaignId', 'campaignName', 'impressions', 'clicks', 'cost',
                   'purchases', 'sales']

        def _submit_new(ad_product, report_type_id, columns):
            resp = requests.post(
                f'{self.ADS_ENDPOINT}/reporting/reports',
                headers=headers,
                json={
                    'name': f'{report_type_id} {end_str}',
                    'startDate': start_str,
                    'endDate':   end_str,
                    'configuration': {
                        'adProduct':    ad_product,
                        'groupBy':      ['campaign'],
                        'columns':      columns,
                        'reportTypeId': report_type_id,
                        'timeUnit':     'SUMMARY',
                        'format':       'GZIP_JSON',
                    },
                },
                timeout=20,
            )
            if resp.status_code == 425:
                return resp.json().get('detail', '').split(': ')[-1].strip()
            resp.raise_for_status()
            return resp.json()['reportId']

        def _submit_with_retry(ad_product, report_type_id, columns, pre_delay=0):
            """Submit a report with retry on 429 rate-limit."""
            if pre_delay:
                time.sleep(pre_delay)
            for attempt in range(3):
                try:
                    rid = _submit_new(ad_product, report_type_id, columns)
                    return rid
                except Exception as e:
                    # Check if 429 by inspecting exception message
                    if '429' in str(e) and attempt < 2:
                        wait = 10 * (attempt + 1)
                        logger.warning('%s 429 rate-limit — retrying in %ds', report_type_id, wait)
                        time.sleep(wait)
                        continue
                    raise
            return None  # unreachable

        # Submit any reports that haven't been submitted yet
        sp_id = existing_sp_id
        sb_id = existing_sb_id
        sd_id = existing_sd_id

        if not sp_id:
            try:
                sp_id = _submit_with_retry('SPONSORED_PRODUCTS', 'spCampaigns', SP_COLS)
            except Exception as e:
                logger.error('SP campaign report submit failed: %s', e)
        if not sb_id:
            try:
                # 3s delay to avoid 429 after SP submission
                sb_id = _submit_with_retry('SPONSORED_BRANDS', 'sbCampaigns', SB_COLS,
                                           pre_delay=3)
            except Exception as e:
                logger.warning('SB campaign report submit failed (no SB or rate-limit): %s', e)
        if not sd_id:
            try:
                # 3s delay to avoid 429 after SB submission
                sd_id = _submit_with_retry('SPONSORED_DISPLAY', 'sdCampaigns', SD_COLS,
                                           pre_delay=3)
            except Exception as e:
                logger.warning('SD campaign report submit failed (no SD or rate-limit): %s', e)

        # Track per-type results
        sp_res = {'status': 'pending', 'report_id': sp_id, 'campaigns': []}
        sb_res = {'status': 'pending', 'report_id': sb_id, 'campaigns': []}
        sd_res = {'status': 'pending', 'report_id': sd_id, 'campaigns': []}

        # Combined poll (up to 2 min) — exits early once all complete (~60-90s typical)
        for _ in range(24):
            time.sleep(5)
            try:
                if sp_id and sp_res['status'] == 'pending':
                    sp_res = self._check_and_download(sp_id, headers)
                    sp_res.setdefault('report_id', sp_id)
            except Exception as e:
                logger.warning('SP poll error: %s', e)
            try:
                if sb_id and sb_res['status'] == 'pending':
                    sb_res = self._check_and_download(sb_id, headers)
                    sb_res.setdefault('report_id', sb_id)
            except Exception as e:
                logger.warning('SB poll error: %s', e)
            try:
                if sd_id and sd_res['status'] == 'pending':
                    sd_res = self._check_and_download(sd_id, headers)
                    sd_res.setdefault('report_id', sd_id)
            except Exception as e:
                logger.warning('SD poll error: %s', e)
            # Exit early once all active reports are done
            active = [r for r in [sp_res, sb_res, sd_res] if r.get('report_id')]
            if all(r['status'] != 'pending' for r in active):
                break

        sp_ok = sp_res.get('status') == 'ok'
        sb_ok = sb_res.get('status') == 'ok'
        sd_ok = sd_res.get('status') == 'ok'

        # Tag each row with its ad type so callers can distinguish
        for c in sp_res.get('campaigns', []): c['_adType'] = 'sp'
        for c in sb_res.get('campaigns', []): c['_adType'] = 'sb'
        for c in sd_res.get('campaigns', []): c['_adType'] = 'sd'

        sp_spend = round(sum(float(c.get('cost') or 0) for c in sp_res.get('campaigns', [])), 2)
        sb_spend = round(sum(float(c.get('cost') or 0) for c in sb_res.get('campaigns', [])), 2)
        sd_spend = round(sum(float(c.get('cost') or 0) for c in sd_res.get('campaigns', [])), 2)
        total_spend = round(sp_spend + sb_spend + sd_spend, 2)

        all_campaigns = (
            sp_res.get('campaigns', []) +
            sb_res.get('campaigns', []) +
            sd_res.get('campaigns', [])
        )

        any_ok = sp_ok or sb_ok or sd_ok
        status = 'ok' if any_ok else ('pending' if (sp_id or sb_id or sd_id) else 'error')

        logger.info('All-campaigns summary: SP=%.2f SB=%.2f SD=%.2f total=%.2f status=%s',
                    sp_spend, sb_spend, sd_spend, total_spend, status)

        return {
            'status':       status,
            'sp_report_id': sp_id,
            'sb_report_id': sb_id,
            'sd_report_id': sd_id,
            'report_id':    sp_id,          # backward compat
            'campaigns':    all_campaigns,
            'total_spend':  total_spend,
            'sp_spend':     sp_spend,
            'sb_spend':     sb_spend,
            'sd_spend':     sd_spend,
            'date':         end_str,
        }

    def get_advertised_product_summary(self, date_range: str = 'today',
                                       existing_report_id: str = None) -> dict:
        """
        Fetch per-ASIN/SKU SP spend using the spAdvertisedProduct report.
        Returns {'status': 'ok'|'pending'|'error', 'report_id': ..., 'products': [...]}
        Each row has: advertisedAsin, advertisedSku, impressions, clicks, cost,
                      purchases7d, sales7d, unitsSoldClicks7d.
        """
        headers = self._headers()

        if existing_report_id:
            raw = self._check_and_download(existing_report_id, headers)
            return {**raw, 'products': raw.pop('campaigns', [])}

        start, end, _tz = SPAPIClient._resolve_local_dates(date_range, marketplace=self.config.marketplace)

        create_resp = requests.post(
            f'{self.ADS_ENDPOINT}/reporting/reports',
            headers=headers,
            json={
                'name': f'SP AdvertisedProduct {end}',
                'startDate': str(start),
                'endDate':   str(end),
                'configuration': {
                    'adProduct':    'SPONSORED_PRODUCTS',
                    'groupBy':      ['advertiser'],
                    'columns':      ['advertisedAsin', 'advertisedSku',
                                     'impressions', 'clicks', 'cost',
                                     'purchases7d', 'sales7d', 'unitsSoldClicks7d'],
                    'reportTypeId': 'spAdvertisedProduct',
                    'timeUnit':     'SUMMARY',
                    'format':       'GZIP_JSON',
                },
            },
            timeout=20,
        )
        if not create_resp.ok:
            if create_resp.status_code == 425:
                report_id = create_resp.json().get('detail', '').split(': ')[-1].strip()
            else:
                raise RuntimeError(f'Ads product report create failed: {_extract_http_error_detail(create_resp)}')
        else:
            report_id = create_resp.json()['reportId']

        # Quick poll 30 s
        for _ in range(6):
            time.sleep(5)
            raw = self._check_and_download(report_id, headers)
            if raw['status'] in ('ok', 'error'):
                return {**raw, 'products': raw.pop('campaigns', [])}

        logger.info('SP AdvertisedProduct report pending (report_id=%s)', report_id)
        return {'status': 'pending', 'report_id': report_id, 'products': []}
