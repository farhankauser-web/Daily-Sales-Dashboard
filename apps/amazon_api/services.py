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

    def _post(self, path: str, json_body: dict = None, timeout: int = 30) -> dict:
        resp = requests.post(
            f'{self.endpoint}{path}',
            headers=self._headers(),
            json=json_body,
            timeout=timeout,
        )
        resp.raise_for_status()
        try:
            return resp.json() if resp.text else {}
        except ValueError:
            return {}

    def test_connection(self) -> dict:
        """Hit the Marketplace Participations endpoint as a health check."""
        return self._get('/sellers/v1/marketplaceParticipations')

    # ── MCF (Fulfillment Outbound 2020-07-01) — Walmart automation ─────────

    def get_mcf_features(self) -> list[dict]:
        """MCF features (BLANK_BOX, BLOCK_AMZL, …) + seller enrollment."""
        resp = self._get('/fba/outbound/2020-07-01/features',
                         params={'marketplaceId': self.mp_id})
        return (resp.get('payload', resp) or {}).get('features', [])

    def get_mcf_feature_sku(self, feature_name: str, seller_sku: str) -> dict:
        """Per-SKU eligible/sellable quantity for a feature (e.g. BLANK_BOX)."""
        resp = self._get(
            f'/fba/outbound/2020-07-01/features/inventory/{feature_name}/{seller_sku}',
            params={'marketplaceId': self.mp_id})
        return resp.get('payload', resp) or {}

    def get_mcf_fulfillment_preview(self, address: dict, items: list[dict],
                                     speeds: list[str] = None,
                                     feature_constraints: list[dict] = None) -> list[dict]:
        """Preview fulfillment options (validates address/features/speed)."""
        body = {
            'marketplaceId': self.mp_id,
            'address': address,
            'items': items,
            'shippingSpeedCategories': speeds or ['Standard'],
        }
        if feature_constraints:
            body['featureConstraints'] = feature_constraints
        resp = self._post('/fba/outbound/2020-07-01/fulfillmentOrders/preview', body)
        return (resp.get('payload', resp) or {}).get('fulfillmentPreviews', [])

    def create_mcf_order(self, seller_fulfillment_order_id: str,
                          displayable_order_id: str,
                          displayable_order_date_iso: str,
                          shipping_speed: str,
                          destination_address: dict,
                          items: list[dict],
                          feature_constraints: list[dict] = None,
                          displayable_comment: str = 'Thank you for your order!') -> dict:
        """
        createFulfillmentOrder. Amazon rejects duplicate
        sellerFulfillmentOrderIds, which is our server-side idempotency net.
        """
        body = {
            'marketplaceId': self.mp_id,
            'sellerFulfillmentOrderId': seller_fulfillment_order_id,
            'displayableOrderId': displayable_order_id,
            'displayableOrderDate': displayable_order_date_iso,
            'displayableOrderComment': displayable_comment[:1000],
            'shippingSpeedCategory': shipping_speed,
            'fulfillmentAction': 'Ship',
            'destinationAddress': destination_address,
            'items': items,
        }
        if feature_constraints:
            body['featureConstraints'] = feature_constraints
        return self._post('/fba/outbound/2020-07-01/fulfillmentOrders', body)

    def get_package_tracking(self, package_number: int) -> dict:
        resp = self._get('/fba/outbound/2020-07-01/tracking',
                         params={'packageNumber': package_number})
        return resp.get('payload', resp) or {}

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

    def _get_throttled(self, path, params, tries=6, pause=2.2):
        """GET with 429 back-off — FBA Inventory summaries has a low rate
        limit (~2 req/s) so paginating hits it without pacing."""
        import requests as _rq
        for attempt in range(tries):
            try:
                return self._get(path, params=params)
            except _rq.exceptions.HTTPError as exc:
                code = getattr(exc.response, 'status_code', None)
                if code == 429 and attempt < tries - 1:
                    time.sleep(pause * (attempt + 1))   # linear back-off
                    continue
                raise

    def get_fba_inventory_summaries_all(self) -> list[dict]:
        """Every FBA inventory summary (paginated). Each item carries
        sellerSku, totalQuantity and inventoryDetails (fulfillable /
        inbound working+shipped+receiving / reserved)."""
        out, token = [], None
        for _ in range(200):                      # hard stop
            params = {'marketplaceIds': self.mp_id, 'details': True,
                      'granularityType': 'Marketplace',
                      'granularityId': self.mp_id}
            if token:
                params['nextToken'] = token
            resp = self._get_throttled('/fba/inventory/v1/summaries', params)
            payload = resp.get('payload', resp) or {}
            out.extend(payload.get('inventorySummaries', []))
            token = (resp.get('pagination') or {}).get('nextToken')
            if not token:
                break
            time.sleep(0.6)                       # pace pages under the limit
        return out

    def get_awd_inventory_all(self) -> list[dict]:
        """Amazon Warehousing & Distribution inventory (paginated).
        Items carry sku, totalOnhandQuantity, totalInboundQuantity."""
        out, token = [], None
        for _ in range(200):
            params = {'details': 'SHOW', 'maxResults': 200}
            if token:
                params['nextToken'] = token
            resp = self._get_throttled('/awd/2024-05-09/inventory', params)
            out.extend(resp.get('inventory', []))
            token = resp.get('nextToken')
            if not token:
                break
            time.sleep(0.6)
        return out

    # ── AWD inbound shipments ────────────────────────────────────────────
    # These are what turn a container into a receipt. `/awd/.../inventory`
    # only gives per-SKU aggregates, so it cannot say WHICH container landed
    # when two share a SKU. An inbound shipment is per-container (our ops
    # record one STAR-… id per container), and carries expected vs received
    # quantities — the exact numbers needed for the shipped-vs-received delta.
    def get_awd_inbound_shipments(self, updated_after: str = None,
                                  status: str = None,
                                  max_results: int = 100) -> list[dict]:
        """
        AWD inbound shipments, newest first (paginated).

        updated_after : ISO-8601, e.g. '2026-07-01T00:00:00Z'
        status        : Amazon's shipmentStatus filter, when you want one state
        """
        out, token = [], None
        for _ in range(100):
            params = {'maxResults': max_results, 'sortBy': 'UPDATED_AT',
                      'sortOrder': 'DESCENDING'}
            if updated_after:
                params['updatedAfter'] = updated_after
            if status:
                params['shipmentStatus'] = status
            if token:
                params['nextToken'] = token
            resp = self._get_throttled('/awd/2024-05-09/inboundShipments', params)
            out.extend(resp.get('shipments') or resp.get('inboundShipments') or [])
            token = resp.get('nextToken')
            if not token:
                break
            time.sleep(0.6)
        return out

    def get_awd_inbound_shipment(self, shipment_id: str,
                                 sku_quantities: str = 'SHOW') -> dict:
        """
        One AWD inbound shipment by id (the `STAR-…` on the container).

        sku_quantities='SHOW' asks Amazon to include the per-SKU breakdown —
        without it you get status only and no received counts. Returned raw so
        callers can decide how to read it; see probe_awd_shipment for a dump of
        the real field names before relying on any of them.
        """
        return self._get_throttled(
            f'/awd/2024-05-09/inboundShipments/{shipment_id}',
            {'skuQuantities': sku_quantities})

    # ── FBA inbound shipments (containers sent straight to an FC) ─────────
    # The AWD calls above only answer for STAR-… ids. A container consigned to
    # a fulfilment centre carries an FBA… id and lives in a different API, so
    # without these an FC container never produces a receipt at all: its units
    # stay "in transit" forever and it never reaches the Receiving stage.
    #
    # One difference that matters more than any other: Fulfillment Inbound v0
    # reports EACHES. AWD reports CASES. Do not reuse the AWD case-conversion
    # here — it would multiply these figures by the case pack.
    def get_fba_inbound_shipment_items(self, shipment_id: str) -> list[dict]:
        """
        Per-SKU shipped vs received for one FBA inbound shipment (paginated).

        Each item carries SellerSKU, QuantityShipped and QuantityReceived, all
        in eaches. QuantityReceived climbs over days as the FC counts cartons
        in, which is exactly the signal the Receiving stage watches for.
        """
        out, token = [], None
        for _ in range(100):
            params = {'MarketplaceId': self.mp_id}
            if token:
                params['NextToken'] = token
            resp = self._get_throttled(
                f'/fba/inbound/v0/shipments/{shipment_id}/items', params)
            payload = resp.get('payload') or resp
            out.extend(payload.get('ItemData') or [])
            token = (payload.get('NextToken') or '').strip() or None
            if not token:
                break
            time.sleep(0.6)
        return out

    def get_fba_inbound_shipment(self, shipment_id: str) -> dict:
        """Header for one FBA inbound shipment — ShipmentStatus lives here.

        Statuses run WORKING → SHIPPED → RECEIVING → CLOSED, with CANCELLED /
        DELETED / ERROR as dead ends. Returns {} when Amazon knows no such
        shipment, so a mistyped id reads as 'not found' rather than an error.
        """
        resp = self._get_throttled(
            '/fba/inbound/v0/shipments',
            {'MarketplaceId': self.mp_id, 'ShipmentIdList': shipment_id,
             'QueryType': 'SHIPMENT'})
        payload = resp.get('payload') or resp
        rows = payload.get('ShipmentData') or []
        return rows[0] if rows else {}

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

    # ── REPORTS API: Payments Date-Range Transaction report ──────────────────
    REPORT_TYPE_DATE_RANGE_TXN = 'GET_DATE_RANGE_FINANCIAL_TRANSACTION_DATA'

    def fetch_date_range_transaction_report(self, start_local, end_local) -> bytes:
        """
        Download the Payments 'Date Range Reports → Transaction' CSV covering
        [start_local, end_local] — the same report as the manual Seller
        Central monthly unified download (deferred transactions included), so
        it ties to the books, unlike the posted-date Finances API.

        Amazon blocks createReport for this type ("report type 1202 is not
        allowed"): it can only be GENERATED in Seller Central. But generated
        reports ARE listable + downloadable here — and ops already generates
        them — so we find the newest one that covers the range. Raises
        LookupError when none exists yet (caller should say: generate it in
        Seller Central, then retry).
        """
        tz_name = self._marketplace_tz(self.config.marketplace)
        start_iso, end_iso = self._local_range_to_utc_interval(
            start_local, end_local, tz_name)
        want_start = datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
        want_end   = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
        tol = timedelta(hours=2)

        j = self._get('/reports/2021-06-30/reports', params={
            'reportTypes': self.REPORT_TYPE_DATE_RANGE_TXN,
            'processingStatuses': 'DONE', 'pageSize': 100})
        for rep in j.get('reports', []):        # newest first
            mps = rep.get('marketplaceIds') or []
            if mps and self.mp_id not in mps:
                continue
            try:
                r_start = datetime.fromisoformat(rep['dataStartTime'])
                r_end   = datetime.fromisoformat(rep['dataEndTime'])
            except (KeyError, ValueError):
                continue
            # BOTH ends must match the requested range (± tol). Coverage alone
            # is not enough: a quarterly report also "covers" the month but
            # would book three months of transactions into one.
            if abs(r_start - want_start) <= tol and abs(r_end - want_end) <= tol:
                doc = self.get_report_document_meta(rep['reportDocumentId'])
                r = requests.get(doc['url'], timeout=120)
                r.raise_for_status()
                return self._decompress_if_needed(
                    r.content, doc.get('compressionAlgorithm') or '')

        # Long shot: try to generate it (Amazon may enable this some day).
        body = {'reportType': self.REPORT_TYPE_DATE_RANGE_TXN,
                'marketplaceIds': [self.mp_id],
                'dataStartTime': start_iso, 'dataEndTime': end_iso}
        resp = requests.post(
            f'{self.endpoint}/reports/2021-06-30/reports',
            headers=self._headers(), json=body, timeout=20)
        if resp.ok:
            report_id = resp.json()['reportId']
            deadline = time.time() + 420
            while time.time() < deadline:
                meta = self.get_report_status(report_id)
                st = meta.get('processingStatus', '')
                if st == 'DONE':
                    doc = self.get_report_document_meta(meta['reportDocumentId'])
                    r = requests.get(doc['url'], timeout=120)
                    r.raise_for_status()
                    return self._decompress_if_needed(
                        r.content, doc.get('compressionAlgorithm') or '')
                if st in ('CANCELLED', 'FATAL'):
                    break
                time.sleep(15)
        raise LookupError(
            f'No Date-Range Transaction report covering '
            f'{start_local}–{end_local} exists yet. Generate it in Seller '
            f'Central (Payments → Reports Repository / Date Range Reports → '
            f'Transaction, custom range = the month), wait for it to finish, '
            f'then click Sync again.')

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

    # Sentinel exception so the ingest layer can react to quota exhaustion
    # without parsing error-string substrings.
    class BAQuotaExceeded(RuntimeError):
        pass

    # ── REPORTS API: Brand-Analytics generic submit/poll ────────────────────
    def submit_ba_report(
        self,
        report_type: str,
        period_start: str,
        period_end:   str,
        period_type:  str = 'WEEK',
        asin:         str = None,
    ) -> str:
        """
        Submit any Brand Analytics report (SQP / Item Comparison / Market
        Basket). Returns reportId.

        Amazon's BA reports require:
          • reportPeriod ∈ {WEEK,MONTH,QUARTER} — passed as reportOptions
          • asin — required for SQP / Item Comparison / Market Basket; the
            brand-aggregate variant has been retired
          • dataStartTime must be a Sunday for WEEK reports (Sun-Sat windows)

        Quota: Amazon documents 0.0167 req/sec (≈ 1/min) with burst 15 for BA
        createReport endpoints. We retry once on 429 after a 65s sleep — that
        gives the bucket time to refill. If we hit 429 a second time we raise
        BAQuotaExceeded so the caller can mark the slot 'pending' and resume
        on the next cron sweep rather than failing the whole run.
        """
        report_options = {'reportPeriod': period_type}
        if asin:
            report_options['asin'] = asin
        body = {
            'reportType':     report_type,
            'marketplaceIds': [self.mp_id],
            'dataStartTime':  f'{period_start}T00:00:00Z',
            'dataEndTime':    f'{period_end}T23:59:59Z',
            'reportOptions':  report_options,
        }

        for attempt in (1, 2):
            resp = requests.post(
                f'{self.endpoint}/reports/2021-06-30/reports',
                headers=self._headers(),
                json=body,
                timeout=20,
            )
            if resp.status_code != 429:
                break
            if attempt == 1:
                time.sleep(65)   # one bucket refill at 1/min
                continue

        if resp.status_code == 429:
            raise SPAPIClient.BAQuotaExceeded(
                f'createReport({report_type}) quota exceeded after retry — '
                f'try again later'
            )
        if not resp.ok:
            raise RuntimeError(
                f'createReport({report_type}) failed: {_extract_http_error_detail(resp)}'
            )
        return resp.json()['reportId']

    def download_ba_report(self, document_id: str) -> dict:
        """Download + decompress + parse any Brand Analytics report (JSON)."""
        meta = self.get_report_document_meta(document_id)
        url  = meta['url']
        comp = meta.get('compressionAlgorithm') or ''
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        body = self._decompress_if_needed(r.content, comp)
        return json.loads(body.decode('utf-8-sig', errors='replace'))

    def download_ba_report_or_error(self, document_id: str) -> tuple[str, dict]:
        """
        Returns ('ok', parsed_payload) on success or ('error', {'message': ...})
        when Amazon's document is actually an error payload. (FATAL reports
        still write a documentId; the document body holds errorDetails.)
        """
        try:
            data = self.download_ba_report(document_id)
        except Exception as e:
            return 'error', {'message': f'download failed: {e}'}
        if isinstance(data, dict) and data.get('errorDetails'):
            return 'error', {'message': data['errorDetails']}
        return 'ok', data

    # ── REPORTS API: Brand-Analytics Search Query Performance ────────────────
    REPORT_TYPE_SETTLEMENT = 'GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2'

    def list_settlement_reports(
        self,
        created_since:  str,
        created_until:  str = None,
        page_size:      int = 50,
    ) -> list[dict]:
        """
        List DONE settlement reports created within a window.

        Settlement reports are AUTO-generated by Amazon on the ~14-day pay
        cycle — we don't submit a createReport. We just list them, dedupe
        against what we've already ingested, and download the new ones.

        Returns a list of {reportId, dataStartTime, dataEndTime,
        processingStatus, reportDocumentId} dicts.
        """
        params = {
            'reportTypes':         self.REPORT_TYPE_SETTLEMENT,
            'processingStatuses':  'DONE',
            'marketplaceIds':      self.mp_id,
            'createdSince':        created_since,
            'pageSize':            page_size,
        }
        if created_until:
            params['createdUntil'] = created_until

        out: list[dict] = []
        next_token: str | None = None
        for _ in range(10):  # hard cap to avoid runaway pagination
            page_params = dict(params)
            if next_token:
                # When paginating, only the nextToken param is allowed.
                page_params = {'nextToken': next_token}
            resp = requests.get(
                f'{self.endpoint}/reports/2021-06-30/reports',
                headers=self._headers(),
                params=page_params,
                timeout=20,
            )
            if not resp.ok:
                raise RuntimeError(
                    f'listSettlementReports failed: '
                    f'{_extract_http_error_detail(resp)}')
            body = resp.json()
            out.extend(body.get('reports') or [])
            next_token = body.get('nextToken')
            if not next_token:
                break
        return out

    def download_settlement_report(self, document_id: str) -> list[dict]:
        """
        Download + parse a settlement report flat file. Tab-delimited,
        documented at:
          https://developer-docs.amazon.com/sp-api/docs/report-type-values#settlement-reports

        Returns list of dict rows (keys are the report's column headers).
        """
        last_exc = None
        for attempt in range(4):                      # resilient to flaky S3
            try:
                meta = self.get_report_document_meta(document_id)
                url  = meta['url']
                comp = meta.get('compressionAlgorithm') or ''
                r = requests.get(url, timeout=(10, 180))   # (connect, read)
                r.raise_for_status()
                body = self._decompress_if_needed(r.content, comp)
                text = body.decode('utf-8-sig', errors='replace')
                reader = csv.DictReader(io.StringIO(text), delimiter='\t')
                return list(reader)
            except (requests.exceptions.RequestException, OSError) as exc:
                last_exc = exc
                time.sleep(2 * (attempt + 1))         # 2s, 4s, 6s backoff
        raise RuntimeError(f'settlement download failed after retries: {last_exc}')

    @staticmethod
    def extract_fba_fee_rows(rows: list[dict]) -> list[dict]:
        """
        From a parsed settlement flat file, pull only the per-SKU per-unit
        FBA fulfillment fee charges and return one normalized dict per row:

            {sku, posted_date, units, fba_fee_total}

        The settlement report has many transaction-types (Order, Refund,
        StorageFee, Adjustment, …) and each transaction can produce multiple
        amount-description rows (Principal, Tax, Commission, etc). For FBA
        per-unit fee drift we want only:
          amount-description == 'FBAPerUnitFulfillmentFee'
          AND a non-empty sku
          AND quantity-purchased > 0  (skip refund-side rows; we want the
                                       original fee billed at order time)
        """
        out: list[dict] = []
        for r in rows:
            desc = (r.get('amount-description') or '').strip()
            if desc != 'FBAPerUnitFulfillmentFee':
                continue
            sku = (r.get('sku') or '').strip()
            if not sku:
                continue
            try:
                qty    = int(r.get('quantity-purchased') or 0)
                amount = float(r.get('amount') or 0)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            posted = (r.get('posted-date') or r.get('posted-date-time') or '').strip()
            # posted-date can be 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS+ZZ'
            posted_date = posted[:10] if posted else None
            if not posted_date:
                continue
            out.append({
                'sku':            sku,
                'posted_date':    posted_date,
                'units':          qty,
                'fba_fee_total':  abs(amount),  # fee is negative; we want magnitude
            })
        return out

    @staticmethod
    def _settlement_month(posted: str):
        """
        Normalize a settlement posted-date (which varies by report vintage:
        'YYYY-MM-DD[THH...]', 'DD.MM.YYYY', 'MM/DD/YYYY') to 'YYYY-MM', or None.
        """
        s = (posted or '').strip()
        if not s:
            return None
        head = s.split('T')[0].split(' ')[0]      # drop any time component
        # ISO: YYYY-MM-DD
        if len(head) >= 7 and head[:4].isdigit() and head[4] == '-':
            return head[:7]
        # Dotted European: DD.MM.YYYY
        if '.' in head:
            parts = head.split('.')
            if len(parts) == 3 and len(parts[2]) == 4:
                return f'{parts[2]}-{parts[1].zfill(2)}'
        # Slashed US: MM/DD/YYYY
        if '/' in head:
            parts = head.split('/')
            if len(parts) == 3 and len(parts[2]) == 4:
                return f'{parts[2]}-{parts[0].zfill(2)}'
        return None

    # Lines whose stored amount keeps its natural sign (money IN = positive).
    # Everything else is a cost: we store the net magnitude (abs of the signed
    # sum, so refund credits correctly reduce the fee) and the P&L line's sign
    # handles subtraction.
    _PNL_INCOME_KEYS = {'gross_sales', 'other_income'}

    @staticmethod
    def classify_settlement_row(txn_type: str, amount_type: str, desc: str):
        """
        Map one settlement row's (transaction-type, amount-type,
        amount-description) to a P&L line key, or None to skip.

        Disambiguates on amount-type where the description alone is ambiguous
        (e.g. 'Shipping' is revenue under ItemPrice but a discount under
        Promotion). Built from a real GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2.
        Signs are handled by the caller (net magnitude for costs).
        """
        tt = (txn_type or '').strip().lower()
        at = (amount_type or '').strip().lower()
        d  = (desc or '').strip().lower()
        dn = ''.join(d.split()).replace('-', '').replace('_', '')   # normalized

        # 1) Taxes are pass-through (collected & remitted / withheld) — exclude.
        if 'tax' in dn:                       # Tax, ShippingTax, GiftWrapTax,
            return None                        # MarketplaceFacilitatorTax-*
        if at == 'itemwithheldtax':
            return None

        # 2) GiftWrap is a pure pass-through (buyer pays, Amazon charges it
        #    straight back, net $0) → exclude every giftwrap head entirely.
        if dn.startswith('giftwrap'):
            return None

        # 3) Promotions / coupons (any amount-type 'promotion') → discount.
        if at == 'promotion':
            return 'promo'

        # 3b) Buyer-paid shipping & its chargeback are netted into Promotional
        #     Discounts (client convention) so they never inflate product
        #     revenue / ARPU. Net shipping kept by the seller offsets promo cost;
        #     refunded shipping increases it. Catches Order + Refund shipping
        #     and ShippingChargeback (Promotion|Shipping already caught above).
        if dn in ('shipping', 'shippingchargeback'):
            return 'promo'

        # 4) Refund of the sale principal → returns.
        if tt.startswith('refund') and at == 'itemprice' and dn == 'principal':
            return 'returns'

        # 5) Sale revenue — product only (principal / goodwill; + liquidations).
        if at == 'itemprice' and dn in ('principal', 'goodwill'):
            return 'gross_sales'

        # 6) Referral / closing commission family (orders charge, refunds credit).
        if dn in ('commission', 'refundcommission', 'variableclosingfee',
                  'fixedclosingfee'):
            return 'commission'

        # 7) Fulfilment (FBA) family — per-unit fee, pick&pack adj,
        #    customer-returns processing fee, multi-channel fulfilment (MCF).
        #    (Shipping/giftwrap chargebacks handled above.)
        if 'fulfillmentfee' in dn or 'pick&packfee' in dn or 'pickpackfee' in dn \
                or 'customerreturnsfee' in (at.replace(' ', '') + dn) \
                or 'returnsfee' in at.replace(' ', '') \
                or 'mcf' in at or 'multichannel' in at:
            return 'fba_fee'

        # 7) Inventory / logistics fees — split into detailed sub-heads so the
        #    Storage Cost section mirrors the client's template (AWD broken out).
        atn = at.replace(' ', '')
        if 'awdtransportation' in atn:
            return 'awd_transportation'
        if 'awdprocessing' in atn:
            return 'awd_processing'
        if 'awdstorage' in atn or 'awdstorage' in dn:
            return 'awd_storage'
        if 'awd' in atn:                          # any other AWD fee variant
            return 'awd_storage'
        if 'inboundtransportation' in atn or 'inboundtransportation' in dn:
            return 'inbound_transportation'
        if 'storage' in dn or 'storage' in atn:   # Storage Fee, renewal, long-term
            return 'storage_fee'
        if 'disposal' in dn or 'removal' in dn \
                or 'gradeandresell' in atn \
                or 'inboundplacement' in (dn + atn) \
                or 'liquidationsbrokeragefee' in dn:
            return 'other_logistics'

        # 8) Advertising / deals — Amazon-side marketing spend (and ad refunds,
        #    which net the cost down). Deal participation/performance fees are
        #    promotional spend → folded into the PPC line.
        if 'advertising' in at or 'advertis' in dn \
                or 'dealparticipationfee' in at.replace(' ', '') \
                or 'dealperformancebasedfee' in at.replace(' ', '') \
                or 'deal' in at and 'fee' in at \
                or 'refundforadvertiser' in at.replace(' ', ''):
            return 'ppc'

        # 9) Subscription (monthly Professional selling fee).
        if 'subscription' in dn:
            return 'subscription'

        # 9b) Strategic Account Services (SAS) / Premium Services / account
        #     management → booked to Amazon Account Management (OpEx).
        atn = at.replace(' ', '')
        if 'premiumservices' in atn or 'strategicaccount' in atn \
                or 'accountmanagement' in atn or atn == 'sas':
            return 'account_management'

        # 10) Reimbursements / restocking / fee adjustments → other income.
        if 'reimburs' in at or 'restockingfee' in dn or 'feeadjustment' in dn \
                or dn in ('reversalreimbursement', 'freereplacementrefunditems',
                          'compensatedclawback', 'warehouselost', 'warehousedamage',
                          'incorrectfeesitems', 'missingfrominbound'):
            return 'other_income'

        return None

    @classmethod
    def extract_pnl_lines(cls, rows: list[dict]) -> dict:
        """
        Aggregate a parsed settlement flat file into P&L line buckets, keyed by
        (posted_month 'YYYY-MM', line_key). Returns:

            {
              'lines':   { ('2026-06','gross_sales'): {'amount': X, 'units': N}, ... },
              'unmapped':{ '<amount-type|amount-description>': total_amount, ... },
            }

        Amounts: income keys (gross_sales, other_income) keep their natural
        sign; all cost/return/promo keys store the NET MAGNITUDE (abs of the
        signed running sum) so that refund credits reduce the fee correctly.
        Units: gross_sales←order principal qty, returns←refund principal qty.
        """
        from collections import defaultdict
        signed   = defaultdict(float)               # (month,key) → signed sum
        units    = defaultdict(int)                 # (month,key) → unit count
        unmapped = defaultdict(float)

        for r in rows:
            ttype = (r.get('transaction-type') or '')
            desc  = (r.get('amount-description') or '')
            atype = (r.get('amount-type') or '')
            try:
                amount = float(r.get('amount') or 0)
            except (TypeError, ValueError):
                continue
            if amount == 0:
                continue

            posted = (r.get('posted-date') or r.get('posted-date-time') or '')
            month  = cls._settlement_month(posted)
            if not month:
                continue
            try:
                qty = int(float(r.get('quantity-purchased') or 0))
            except (TypeError, ValueError):
                qty = 0

            key = cls.classify_settlement_row(ttype, atype, desc)
            if not key:
                if amount:
                    unmapped[f'{atype.strip()}|{desc.strip()}'] += amount
                continue

            signed[(month, key)] += amount

            dn = ''.join(desc.lower().split()).replace('-', '').replace('_', '')
            if key == 'gross_sales' and dn == 'principal' and qty > 0:
                units[(month, key)] += qty
            elif key == 'returns' and dn == 'principal':
                units[(month, key)] += abs(qty)

        lines = {}
        for (month, key), val in signed.items():
            amt = val if key in cls._PNL_INCOME_KEYS else abs(val)
            lines[(month, key)] = {'amount': round(amt, 2),
                                    'units': units.get((month, key), 0)}
        return {'lines': lines, 'unmapped': dict(unmapped)}

    # ── Multi-Channel Fulfillment (Fulfillment Outbound 2020-07-01) ──────────
    def list_mcf_orders(self, query_start_iso: str, max_pages: int = 80) -> list[dict]:
        """All MCF fulfillment orders updated since query_start_iso (UTC ISO)."""
        out, token, pages = [], None, 0
        while pages < max_pages:
            params = {'queryStartDate': query_start_iso}
            if token:
                params = {'nextToken': token}
            resp = self._get('/fba/outbound/2020-07-01/fulfillmentOrders',
                             params=params)
            payload = resp.get('payload', resp) or {}
            out.extend(payload.get('fulfillmentOrders') or [])
            token = payload.get('nextToken')
            pages += 1
            if not token:
                break
            time.sleep(0.6)   # rate limit: 2 req/s
        return out

    def list_financial_events(self, posted_after_iso: str, posted_before_iso: str,
                               max_pages: int = 400) -> dict:
        """
        All financial event lists for [posted_after, posted_before), merged
        across pages. Returns {eventListName: [events...]}. Programmatic
        equivalent of the transaction report (Finances API v0).
        """
        import requests
        from collections import defaultdict
        merged = defaultdict(list)
        token, pages = None, 0
        while pages < max_pages:
            if token:
                params = {'NextToken': token}
            else:
                params = {'PostedAfter': posted_after_iso,
                          'PostedBefore': posted_before_iso,
                          'MaxResultsPerPage': 100}
            # Finances API throttles aggressively (~0.5 req/s, burst 30).
            # Retry 429s with exponential backoff so a full-month pull
            # (hundreds of pages) doesn't abort mid-way.
            resp = None
            for attempt in range(6):
                try:
                    resp = self._get('/finances/v0/financialEvents', params=params)
                    break
                except requests.exceptions.HTTPError as e:
                    status = getattr(e.response, 'status_code', None)
                    if status == 429 and attempt < 5:
                        time.sleep(min(2 ** attempt, 30))   # 1,2,4,8,16,30
                        continue
                    raise
            payload = resp.get('payload', resp) or {}
            fes = payload.get('FinancialEvents', {}) or {}
            for k, v in fes.items():
                if isinstance(v, list) and v:
                    merged[k].extend(v)
            token = payload.get('NextToken')
            pages += 1
            if not token:
                break
            time.sleep(0.9)          # finances rate limit ~0.5 req/s burst
        return dict(merged)

    def get_mcf_order(self, seller_fulfillment_order_id: str) -> dict:
        """Detail incl. shipments + package tracking numbers."""
        resp = self._get(
            f'/fba/outbound/2020-07-01/fulfillmentOrders/{seller_fulfillment_order_id}')
        return resp.get('payload', resp) or {}

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
    ADS_ENDPOINT = 'https://advertising-api.amazon.com'          # NA default
    _REGION_ENDPOINTS = {
        'na': 'https://advertising-api.amazon.com',
        'eu': 'https://advertising-api-eu.amazon.com',    # EU + UK + AE + SA
        'fe': 'https://advertising-api-fe.amazon.com',
    }
    _MARKETPLACE_REGION = {'usa': 'na', 'ca': 'na', 'mx': 'na', 'br': 'na',
                           'uk': 'eu', 'de': 'eu', 'fr': 'eu', 'it': 'eu',
                           'es': 'eu', 'ae': 'eu', 'sa': 'eu', 'jp': 'fe'}

    def __init__(self, config):
        self.config     = config
        self.profile_id = config.ads_profile_id
        region = self._MARKETPLACE_REGION.get(config.marketplace, 'na')
        # per-instance override; every method reads self.ADS_ENDPOINT
        self.ADS_ENDPOINT = self._REGION_ENDPOINTS[region]

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

    # ------------------------------------------------------------------ DETAIL REPORTS
    def submit_detail_report(
        self,
        report_kind: str,
        start_date,
        end_date=None,
        existing_report_id: str | None = None,
        short_poll_seconds: int = 15,
    ) -> dict:
        """
        Generic submitter for Phase 1 detail reports (search-term, targeting,
        advertised-product, placement, ad-group across SP/SB/SD).

        See `apps.amazon_api.ads_detail_reports.REPORT_CONFIGS` for the full list
        of supported `report_kind` values.

        Returns:
            {
              'status':      'ok' | 'pending' | 'error',
              'report_id':   str,
              'rows':        list[dict],   # only when status='ok'
              'error':       str,          # only when status='error'
              'report_kind': str,
              'start':       'YYYY-MM-DD',
              'end':         'YYYY-MM-DD',
            }

        Mirrors the resume semantics of `get_campaign_summary`: pass an
        `existing_report_id` to poll/download instead of submitting again.
        """
        from apps.amazon_api.ads_detail_reports import REPORT_CONFIGS

        if report_kind not in REPORT_CONFIGS:
            raise ValueError(
                f'Unknown report_kind {report_kind!r}; valid: {sorted(REPORT_CONFIGS)}'
            )
        cfg = REPORT_CONFIGS[report_kind]
        end_date = end_date or start_date
        headers  = self._headers()

        # Resume mode — poll an in-flight report rather than re-submitting.
        if existing_report_id:
            result = self._check_and_download(existing_report_id, headers)
            return self._wrap_detail_result(result, report_kind, start_date, end_date)

        # ── Submit a new report ─────────────────────────────────────────────
        body = {
            'name': f'{report_kind} {end_date}',
            'startDate': str(start_date),
            'endDate':   str(end_date),
            'configuration': {
                'adProduct':    cfg['adProduct'],
                'reportTypeId': cfg['reportTypeId'],
                'groupBy':      cfg['groupBy'],
                'columns':      cfg['columns'],
                'timeUnit':     'DAILY',
                'format':       'GZIP_JSON',
            },
        }

        # Single retry on 429 throttling. Amazon's burst limit on the
        # /reporting/reports endpoint is roughly 25/sec but trips well below
        # that under sustained load; a 5-second backoff is enough in practice.
        for attempt in (1, 2):
            create_resp = requests.post(
                f'{self.ADS_ENDPOINT}/reporting/reports',
                headers=headers,
                json=body,
                timeout=20,
            )
            if create_resp.status_code != 429:
                break
            if attempt == 1:
                time.sleep(5)
                continue

        if not create_resp.ok:
            # 425 Too Early — Amazon dedup: the same report (same window + config)
            # was already submitted recently. Extract the existing report_id and
            # resume polling against it rather than failing.
            if create_resp.status_code == 425:
                detail = (create_resp.json() or {}).get('detail', '') or ''
                report_id = detail.split(':')[-1].strip()
                if not report_id:
                    raise RuntimeError(
                        f'Submit {report_kind} 425 but no report_id in detail: {detail!r}'
                    )
            else:
                raise RuntimeError(
                    f'Submit {report_kind} failed: {_extract_http_error_detail(create_resp)}'
                )
        else:
            report_id = create_resp.json().get('reportId')
            if not report_id:
                raise RuntimeError(
                    f'Submit {report_kind} returned no reportId: {create_resp.json()!r}'
                )

        # ── Short poll — return pending if not done within short_poll_seconds ──
        elapsed = 0
        while elapsed < short_poll_seconds:
            time.sleep(5)
            elapsed += 5
            result = self._check_and_download(report_id, headers)
            if result['status'] in ('ok', 'error'):
                return self._wrap_detail_result(result, report_kind, start_date, end_date)

        return {
            'status':      'pending',
            'report_id':   report_id,
            'rows':        [],
            'report_kind': report_kind,
            'start':       str(start_date),
            'end':         str(end_date),
        }

    @staticmethod
    def _wrap_detail_result(result: dict, report_kind, start_date, end_date) -> dict:
        """Internal: map the legacy {'campaigns': ...} key to {'rows': ...}
        and stamp report_kind / start / end so the ingest layer has full context."""
        rows = result.pop('campaigns', None)
        if rows is not None:
            result['rows'] = rows
        result['report_kind'] = report_kind
        result['start']       = str(start_date)
        result['end']         = str(end_date)
        return result

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

    # ------------------------------------------------------------------ SP HOURLY
    def submit_sp_hourly_campaigns_report(
        self,
        start_date,
        end_date=None,
        existing_report_id: str = None,
    ) -> dict:
        """
        Submit (or resume) a Sponsored Products HOURLY campaigns report.

        Amazon Ads API v3 supports timeUnit=HOURLY for SP campaigns. The report
        returns one row per (campaignId, hour-of-day). Data is retained for 30
        days even though the GUI only exposes 14 — so this works for T-1 / T-2
        catch-up.

        Args:
            start_date:           date (inclusive) — usually yesterday
            end_date:             date (inclusive) — defaults to start_date
            existing_report_id:   poll/download instead of submitting

        Returns:
            {
              'status':    'ok' | 'pending' | 'error',
              'report_id': str,
              'rows':      list[dict],  # only when status='ok'
              'error':     str,         # only when status='error'
              'start':     'YYYY-MM-DD',
              'end':       'YYYY-MM-DD',
            }

        Notes:
          • SP hourly returns rows keyed by `date` (the hour boundary). Some
            accounts also expose a `hour` column directly. The caller (ingest
            command) is responsible for normalising both shapes into (date,hour).
          • This method ONLY submits and short-polls (30 s). If still PENDING,
            it returns status='pending' with the report_id so the caller can
            store it and poll again later.
          • SB and SD do NOT support timeUnit=HOURLY. Do not call this with
            adProduct=SPONSORED_BRANDS or SPONSORED_DISPLAY.
        """
        headers = self._headers()

        # Resume mode — caller already submitted, just download
        if existing_report_id:
            result = self._check_and_download(existing_report_id, headers)
            # _check_and_download returns 'campaigns' for the field name; rename
            return {
                **result,
                'rows': result.get('campaigns', []),
            }

        if end_date is None:
            end_date = start_date
        start_str = str(start_date)
        end_str   = str(end_date)

        # SP hourly columns. Amazon Ads API v3 restrictions for timeUnit=HOURLY:
        #   • `date` MUST be in columns (it carries the hour boundary).
        #   • 7-day / 14-day attribution columns are REJECTED (the window can't
        #     close inside one hour) — must use 1-day attribution variants:
        #     purchases1d / sales1d / unitsSoldClicks1d.
        #   • Anything else returns: "timeUnit is not supported for this report type."
        SP_HOURLY_COLS = [
            'date',
            'campaignId', 'campaignName',
            'impressions', 'clicks', 'cost',
            'purchases1d', 'sales1d', 'unitsSoldClicks1d',
        ]

        create_resp = requests.post(
            f'{self.ADS_ENDPOINT}/reporting/reports',
            headers=headers,
            json={
                'name': f'SP Campaigns HOURLY {start_str}_{end_str}',
                'startDate': start_str,
                'endDate':   end_str,
                'configuration': {
                    'adProduct':    'SPONSORED_PRODUCTS',
                    'groupBy':      ['campaign'],
                    'columns':      SP_HOURLY_COLS,
                    'reportTypeId': 'spCampaigns',
                    'timeUnit':     'HOURLY',
                    'format':       'GZIP_JSON',
                },
            },
            timeout=20,
        )

        if not create_resp.ok:
            # 425 = Amazon deduplication → re-use the in-flight reportId
            if create_resp.status_code == 425:
                report_id = create_resp.json().get('detail', '').split(': ')[-1].strip()
            else:
                detail = _extract_http_error_detail(create_resp)
                logger.error('SP hourly report submission failed: %s', detail)
                return {
                    'status': 'error', 'report_id': '', 'error': detail,
                    'rows': [], 'start': start_str, 'end': end_str,
                }
        else:
            report_id = create_resp.json()['reportId']

        # Short poll (30s) — return rows immediately if Amazon was fast
        for _ in range(6):
            time.sleep(5)
            try:
                result = self._check_and_download(report_id, headers)
            except Exception as e:
                logger.warning('SP hourly poll error (report_id=%s): %s', report_id, e)
                continue
            state = result.get('status')
            if state == 'ok':
                return {
                    'status':    'ok',
                    'report_id': report_id,
                    'rows':      result.get('campaigns', []),
                    'start':     start_str,
                    'end':       end_str,
                }
            if state == 'error':
                return {
                    'status':    'error',
                    'report_id': report_id,
                    'error':     result.get('error', 'unknown'),
                    'rows':      [],
                    'start':     start_str,
                    'end':       end_str,
                }

        # Still PENDING — caller stores report_id and resumes later
        logger.info('SP hourly report submitted, processing (report_id=%s).', report_id)
        return {
            'status':    'pending',
            'report_id': report_id,
            'rows':      [],
            'start':     start_str,
            'end':       end_str,
        }

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


# ── CREDENTIAL PROBE ──────────────────────────────────────────────────────────
# Live end-to-end test of an AmazonAPIConfig that returns *actionable* error
# diagnosis instead of a raw HTTP status. Used by /api-config/<pk>/test/ and
# auto-invoked after the config_form save.

# Ads-API regional endpoints (the AdsAPIClient hardcodes NA — this is for the
# probe only so we test against the right region for each marketplace).
_ADS_ENDPOINT_BY_MP = {
    'usa': 'https://advertising-api.amazon.com',
    'ca':  'https://advertising-api.amazon.com',
    'uk':  'https://advertising-api-eu.amazon.com',
    'de':  'https://advertising-api-eu.amazon.com',
    'ae':  'https://advertising-api-eu.amazon.com',
    'sa':  'https://advertising-api-eu.amazon.com',
}

# Seller Central URL per marketplace (used in the actionable hint shown to user)
_SELLER_CENTRAL_BY_MP = {
    'usa': 'sellercentral.amazon.com',
    'ca':  'sellercentral.amazon.ca',
    'uk':  'sellercentral.amazon.co.uk',
    'de':  'sellercentral.amazon.de',
    'ae':  'sellercentral.amazon.ae',
    'sa':  'sellercentral.amazon.sa',
}


def _probe_sp_api(cfg) -> dict:
    """Returns {'status', 'detail', 'hint'} for the SP-API leg of the probe."""
    if not cfg.has_sp_api_credentials():
        return {'status': 'skipped',
                'detail': 'No SP-API credentials saved.',
                'hint':   ''}

    # Step 1 — LWA token exchange
    try:
        token = LWATokenManager.get_access_token(cfg)
    except Exception as exc:
        return {
            'status': 'lwa_failed',
            'detail': str(exc),
            'hint':   ('LWA token exchange failed. Check lwa_client_id, '
                       'lwa_client_secret, and refresh_token — one is malformed, '
                       'revoked, or copied incorrectly.'),
        }

    # Step 2 — live SP-API call against this marketplace's regional endpoint
    mp_info  = settings.AMAZON_MARKETPLACES.get(cfg.marketplace, {})
    endpoint = mp_info.get('endpoint', 'https://sellingpartnerapi-na.amazon.com')
    expected_mp_id = cfg.marketplace_id or mp_info.get('id', '')
    sc_host  = _SELLER_CENTRAL_BY_MP.get(cfg.marketplace, 'sellercentral.amazon.com')

    try:
        r = requests.get(
            f'{endpoint}/sellers/v1/marketplaceParticipations',
            headers={'x-amz-access-token': token, 'Content-Type': 'application/json'},
            timeout=15,
        )
    except requests.RequestException as exc:
        return {'status': 'network_error', 'detail': str(exc),
                'hint':   'Could not reach Amazon. Check internet connection.'}

    if r.status_code == 200:
        try:
            body = r.json()
        except Exception:
            return {'status': 'parse_error', 'detail': r.text[:200], 'hint': ''}

        participations  = body.get('payload') or []
        authorized_ids  = {
            (p.get('marketplace') or {}).get('id')
            for p in participations
            if (p.get('marketplace') or {}).get('id')
        }
        authorized_names = sorted({
            (p.get('marketplace') or {}).get('name', '?')
            for p in participations
        })

        if expected_mp_id and expected_mp_id not in authorized_ids:
            return {
                'status': 'marketplace_mismatch',
                'detail': (f'refresh_token authorizes {", ".join(sorted(authorized_ids))} '
                           f'({", ".join(authorized_names)}) — but this config row is set '
                           f'to {expected_mp_id} ({cfg.marketplace.upper()}).'),
                'hint':   (f'Either fix the Marketplace ID on this row, or generate a new '
                           f'refresh_token by re-authorizing the developer app inside '
                           f'{sc_host} as the {cfg.marketplace.upper()} seller account.'),
            }
        return {
            'status': 'ok',
            'detail': f'Verified — {len(participations)} marketplace(s): {", ".join(authorized_names)}',
            'hint':   '',
        }

    if r.status_code == 403:
        return {
            'status': 'wrong_marketplace_token',
            'detail': f'HTTP 403 {r.headers.get("x-amzn-ErrorType", "AccessDenied")}: '
                      f'{r.text[:200]}',
            'hint':   (f'Amazon refused the call. The most common cause is that the '
                       f'refresh_token was issued for a different Seller Central region '
                       f'(e.g. USA token reused for UK). Log into {sc_host} as the '
                       f'{cfg.marketplace.upper()} seller, re-authorize the developer app '
                       f'(same Client ID), and paste only the new refresh_token here.'),
        }

    if r.status_code == 401:
        return {
            'status': 'token_invalid',
            'detail': f'HTTP 401: {r.text[:200]}',
            'hint':   'Access token was rejected — the refresh_token may have been revoked. '
                      'Re-authorize the developer app to get a fresh one.',
        }

    return {
        'status': 'error',
        'detail': f'HTTP {r.status_code}: {r.text[:300]}',
        'hint':   '',
    }


def _probe_ads_api(cfg) -> dict:
    """Returns {'status', 'detail', 'hint'} for the Ads-API leg."""
    if not cfg.has_ads_credentials():
        return {'status': 'skipped',
                'detail': 'No Ads API credentials saved.', 'hint': ''}

    # Step 1 — Ads LWA token
    try:
        ads = AdsAPIClient(cfg)
        token = ads._get_ads_token()
    except Exception as exc:
        return {
            'status': 'lwa_failed',
            'detail': str(exc),
            'hint':   'Ads LWA token exchange failed. Check ads_client_id, '
                      'ads_client_secret, and ads_refresh_token.',
        }

    # Step 2 — list profiles on the correct regional endpoint
    endpoint = _ADS_ENDPOINT_BY_MP.get(cfg.marketplace, 'https://advertising-api.amazon.com')
    try:
        r = requests.get(
            f'{endpoint}/v2/profiles',
            headers={
                'Authorization': f'Bearer {token}',
                'Amazon-Advertising-API-ClientId': cfg.ads_client_id,
                'Content-Type': 'application/json',
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        return {'status': 'network_error', 'detail': str(exc), 'hint': ''}

    if r.status_code != 200:
        return {
            'status': 'error',
            'detail': f'HTTP {r.status_code}: {r.text[:300]}',
            'hint':   ('Ads /v2/profiles rejected the token. Re-authorize the Ads API '
                       'developer app in Amazon Ads console for the correct region.'),
        }

    try:
        profiles = r.json() or []
    except Exception:
        return {'status': 'parse_error', 'detail': r.text[:200], 'hint': ''}

    profile_ids = {str(p.get('profileId')) for p in profiles if p.get('profileId')}
    cfg_profile = str(cfg.ads_profile_id or '').strip()

    if cfg_profile and cfg_profile not in profile_ids:
        country_codes = sorted({p.get('countryCode', '?') for p in profiles})
        return {
            'status': 'profile_mismatch',
            'detail': (f'ads_profile_id={cfg_profile} is NOT in this token\'s '
                       f'authorized profiles. Token grants access to: '
                       f'{", ".join(sorted(profile_ids)) or "(none)"} '
                       f'(countries: {", ".join(country_codes)}).'),
            'hint':   (f'Either set ads_profile_id to one of the values above, or '
                       f'authorize the Ads app for the {cfg.marketplace.upper()} '
                       f'account that owns the desired profile.'),
        }

    matched = next((p for p in profiles if str(p.get('profileId')) == cfg_profile), None)
    if matched:
        return {
            'status': 'ok',
            'detail': f'Verified — profile {cfg_profile} ({matched.get("countryCode")}) '
                      f'· {matched.get("accountInfo", {}).get("name", "")}',
            'hint':   '',
        }
    return {
        'status': 'ok',
        'detail': f'Verified — {len(profiles)} profile(s) available (set ads_profile_id to pick one)',
        'hint':   '' if profile_ids else 'No Ads profiles attached to this token.',
    }


def probe_marketplace_credentials(cfg) -> dict:
    """
    Full credential probe for an AmazonAPIConfig row.
    Returns:
        {
            'sp_api':  {'status', 'detail', 'hint'},
            'ads_api': {'status', 'detail', 'hint'},
            'overall': 'ok' | 'partial' | 'failed',
        }
    `overall` is 'ok' when every non-skipped leg passes,
                 'partial' when at least one passes and another fails,
                 'failed' when nothing passes.
    """
    sp  = _probe_sp_api(cfg)
    ads = _probe_ads_api(cfg)

    def _passed(leg): return leg['status'] in ('ok', 'skipped')
    sp_pass, ads_pass = _passed(sp), _passed(ads)
    if sp_pass and ads_pass:
        overall = 'ok'
    elif sp_pass or ads_pass:
        overall = 'partial'
    else:
        overall = 'failed'
    return {'sp_api': sp, 'ads_api': ads, 'overall': overall}
