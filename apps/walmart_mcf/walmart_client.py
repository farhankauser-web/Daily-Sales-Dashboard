"""
Walmart Marketplace API v3 client (US).

Auth: client-credentials OAuth. Tokens live 15 minutes; cached in-process
and refreshed 60s early. Every request carries WM_SEC.ACCESS_TOKEN,
WM_QOS.CORRELATION_ID and WM_SVC.NAME headers and is written to APILog.
"""
from __future__ import annotations

import base64
import time as _time

import requests
from django.conf import settings

from .core import (FatalAPIError, log_api, new_correlation_id,
                   request_with_retry)

_TIMEOUT = 30


class WalmartClient:
    _token_cache: dict[str, tuple[str, float]] = {}   # client_id → (token, expires_at)

    def __init__(self):
        self.base = settings.WALMART_API_BASE.rstrip('/')
        # DB config (API Configuration page) wins; env vars are the fallback.
        from .models import WalmartAPIConfig
        cfg = WalmartAPIConfig.objects.filter(is_active=True).first()
        if cfg and cfg.client_id and cfg.client_secret:
            self.client_id, self.client_secret = cfg.client_id, cfg.client_secret
        else:
            self.client_id = settings.WALMART_CLIENT_ID
            self.client_secret = settings.WALMART_CLIENT_SECRET
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                'Walmart credentials missing — add them on the API '
                'Configuration page (or set WALMART_CLIENT_ID/SECRET).')

    # ── auth ────────────────────────────────────────────────────────────────

    def _access_token(self) -> str:
        now = _time.time()
        cached = self._token_cache.get(self.client_id)
        if cached and now < cached[1] - 60:
            return cached[0]

        cid = new_correlation_id()
        basic = base64.b64encode(
            f'{self.client_id}:{self.client_secret}'.encode()).decode()
        started = _time.time()
        resp = requests.post(
            f'{self.base}/v3/token',
            data={'grant_type': 'client_credentials'},
            headers={
                'Authorization': f'Basic {basic}',
                'WM_QOS.CORRELATION_ID': cid,
                'WM_SVC.NAME': settings.WALMART_SVC_NAME,
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            timeout=_TIMEOUT,
        )
        log_api('walmart', '/v3/token', 'POST', None,
                {'status': resp.status_code}, resp.status_code,
                int((_time.time() - started) * 1000), cid)
        if not resp.ok:
            raise FatalAPIError(f'Walmart token request failed '
                                f'({resp.status_code})',
                                status_code=resp.status_code,
                                body=resp.text or '')
        data = resp.json()
        token = data['access_token']
        expires_in = int(data.get('expires_in', 900))
        self._token_cache[self.client_id] = (token, now + expires_in)
        return token

    def _headers(self, cid: str) -> dict:
        return {
            'WM_SEC.ACCESS_TOKEN': self._access_token(),
            'WM_QOS.CORRELATION_ID': cid,
            'WM_SVC.NAME': settings.WALMART_SVC_NAME,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }

    # ── request core ────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, params: dict | None = None,
                 json_body: dict | None = None) -> dict:
        cid = new_correlation_id()
        url = f'{self.base}{path}'
        started = _time.time()

        def _do():
            return requests.request(method, url, params=params,
                                    json=json_body,
                                    headers=self._headers(cid),
                                    timeout=_TIMEOUT)
        try:
            resp = request_with_retry(_do, endpoint=path)
        except FatalAPIError as exc:
            log_api('walmart', path, method, json_body, exc.body,
                    exc.status_code, int((_time.time() - started) * 1000), cid)
            raise
        body = {}
        if resp.text:
            try:
                body = resp.json()
            except ValueError:
                body = {'raw': resp.text[:500]}
        log_api('walmart', path, method, json_body, body, resp.status_code,
                int((_time.time() - started) * 1000), cid)
        return body

    # ── operations ──────────────────────────────────────────────────────────

    def get_released_orders(self, created_after_iso: str,
                            limit: int = 200) -> list[dict]:
        """
        Orders released for fulfillment (paid, ready to pick/pack/ship).
        Follows nextCursor pagination; returns raw Walmart order dicts.
        """
        orders: list[dict] = []
        params: dict = {'createdStartDate': created_after_iso, 'limit': limit}
        path = '/v3/orders/released'
        for _ in range(50):                      # hard page cap
            body = self._request('GET', path, params=params)
            lst = (body.get('list') or {})
            elements = (lst.get('elements') or {}).get('order') or []
            orders.extend(elements)
            cursor = (lst.get('meta') or {}).get('nextCursor')
            if not cursor:
                break
            params = {'nextCursor': cursor}
        return orders

    def get_all_orders(self, created_after_iso: str, status: str = None,
                       limit: int = 100) -> list[dict]:
        """
        All orders (any lifecycle stage), optionally filtered by line status
        (Created | Acknowledged | Shipped | Delivered | Cancelled).
        """
        orders: list[dict] = []
        params: dict = {'createdStartDate': created_after_iso, 'limit': limit}
        if status:
            params['status'] = status
        path = '/v3/orders'
        for _ in range(50):
            body = self._request('GET', path, params=params)
            lst = (body.get('list') or {})
            orders.extend((lst.get('elements') or {}).get('order') or [])
            cursor = (lst.get('meta') or {}).get('nextCursor')
            if not cursor:
                break
            params = {'nextCursor': cursor}
        return orders

    def get_order(self, purchase_order_id: str) -> dict:
        body = self._request('GET', f'/v3/orders/{purchase_order_id}')
        return (body.get('order') or body)

    def acknowledge(self, purchase_order_id: str) -> dict:
        return self._request('POST', f'/v3/orders/{purchase_order_id}/acknowledge')

    def update_inventory(self, sku: str, quantity: int) -> dict:
        """Set the Walmart listing's on-hand quantity for one SKU."""
        return self._request('PUT', '/v3/inventory',
                             params={'sku': sku},
                             json_body={'sku': sku,
                                        'quantity': {'unit': 'EACH',
                                                     'amount': int(quantity)}})

    def update_shipping(self, purchase_order_id: str,
                        order_lines: list[dict]) -> dict:
        """
        order_lines: [{line_number, quantity, ship_datetime_ms, carrier,
                       method_code, tracking_number, tracking_url}]
        """
        payload = {
            'orderShipment': {
                'orderLines': {
                    'orderLine': [
                        {
                            'lineNumber': ln['line_number'],
                            'orderLineStatuses': {
                                'orderLineStatus': [{
                                    'status': 'Shipped',
                                    'statusQuantity': {
                                        'unitOfMeasurement': 'EACH',
                                        'amount': str(ln['quantity']),
                                    },
                                    'trackingInfo': {
                                        'shipDateTime': ln['ship_datetime_ms'],
                                        'carrierName': {'carrier': ln['carrier']},
                                        'methodCode': ln['method_code'],
                                        'trackingNumber': ln['tracking_number'],
                                        **({'trackingURL': ln['tracking_url']}
                                           if ln.get('tracking_url') else {}),
                                    },
                                }],
                            },
                        }
                        for ln in order_lines
                    ],
                },
            },
        }
        return self._request('POST',
                             f'/v3/orders/{purchase_order_id}/shipping',
                             json_body=payload)
