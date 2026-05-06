"""
apps/amazon_api/services.py — SP-API + Ads API client wrappers
"""
import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


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
        resp.raise_for_status()
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

    def _get(self, path: str, params: dict = None) -> dict:
        resp = requests.get(
            f'{self.endpoint}{path}',
            headers=self._headers(),
            params=params,
            timeout=20,
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
        start, end = self._resolve_dates(
            date_range,
            start_date=start_date,
            end_date=end_date,
            marketplace=self.config.marketplace,
        )

        # Sales & Traffic (requires Selling Partner Insights role)
        resp = self._get(
            '/sales/v1/orderMetrics',
            params={
                'marketplaceIds': self.mp_id,
                'interval':       f'{start}T00:00:00Z--{end}T23:59:59Z',
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
        start, end = self._resolve_dates(
            date_range,
            start_date=start_date,
            end_date=end_date,
            marketplace=self.config.marketplace,
        )
        return self._get(
            '/orders/v0/orders',
            params={
                'MarketplaceIds':     self.mp_id,
                'CreatedAfter':       f'{start}T00:00:00Z',
                'CreatedBefore':      f'{end}T23:59:59Z',
                'OrderStatuses':      'Unshipped,PartiallyShipped,Shipped',
            }
        )

    @staticmethod
    def _resolve_dates(date_range: str, start_date: str = None, end_date: str = None, marketplace: str = None):
        tz_name = settings.TIME_ZONE
        if marketplace:
            tz_name = settings.AMAZON_MARKETPLACES.get(marketplace, {}).get('timezone', settings.TIME_ZONE)
        today = datetime.now(tz=ZoneInfo(tz_name)).date()
        if date_range == 'custom' and start_date and end_date:
            return start_date, end_date
        if date_range == 'today':
            return str(today), str(today)
        elif date_range == 'yesterday':
            d = today - timedelta(days=1)
            return str(d), str(d)
        elif date_range == 'mtd':
            return str(today.replace(day=1)), str(today)
        elif date_range == '7d':
            return str(today - timedelta(days=7)), str(today)
        elif date_range == '30d':
            return str(today - timedelta(days=30)), str(today)
        return str(today), str(today)


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
        resp.raise_for_status()
        return resp.json()['access_token']

    def get_campaign_summary(self, date_range: str = 'today') -> dict:
        start, end = SPAPIClient._resolve_dates(date_range, marketplace=self.config.marketplace)
        start_str  = start.replace('-', '')
        end_str    = end.replace('-', '')

        resp = requests.post(
            f'{self.ADS_ENDPOINT}/v2/sp/campaigns/report',
            headers=self._headers(),
            json={
                'reportDate': end_str,
                'metrics': 'impressions,clicks,spend,sales7d,orders7d,acos,roas',
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
