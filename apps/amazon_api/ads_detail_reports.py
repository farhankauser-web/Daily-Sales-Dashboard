"""
ads_detail_reports — Phase 1 Ads API v3 detail-report configs + normalizers.

Houses the per-report-kind configuration that drives `AdsAPIClient.submit_detail_report`
plus row normalizers that translate Amazon's varying column names (purchases7d
vs purchases14d vs purchases; advertisedAsin vs purchasedAsin vs promotedAsin)
into the field shapes our snapshot tables expect.

Report kinds:
    sp_adgroup, sp_targeting, sp_search_term, sp_advertised_product, sp_placement
    sb_adgroup, sb_targeting, sb_search_term, sb_advertised_product, sb_placement
    sd_adgroup, sd_targeting, sd_advertised_product
    (sd_search_term and sd_placement do not exist — SD has no search-term or
     placement reports per Amazon Ads API v3 spec.)

All reports use timeUnit=DAILY and format=GZIP_JSON.
"""
from __future__ import annotations

import hashlib
from typing import Any


# ── Report configs ──────────────────────────────────────────────────────────
# adProduct + reportTypeId + groupBy + columns required by Ads API v3 /reporting/reports.

REPORT_CONFIGS: dict[str, dict] = {
    # ── SPONSORED PRODUCTS ────────────────────────────────────────────────────
    # NOTE: There is no standalone `spAdGroups` reportTypeId in the Ads API v3.
    # Ad-group rollup is computed by aggregating spAdvertisedProduct (or
    # spTargeting) on the read side. Dropped from Phase 1 ingestion.
    #
    # SP column names that DIFFER from sb/sd:
    #   targeting  ← (NOT targetingExpression)
    #   keyword    ← (NOT keywordText)   keyword text for SP keyword reports
    #   keywordType← (NOT targetingType) keyword/PAT type for SP
    # The error message Amazon returned spells the allowed list verbatim.
    'sp_targeting': dict(
        adProduct='SPONSORED_PRODUCTS',
        reportTypeId='spTargeting',
        groupBy=['targeting'],
        columns=[
            'campaignId', 'adGroupId', 'keywordId',
            'targeting',       # expression (e.g. "asin=B0XYZ" or "category=...")
            'keywordType',     # BROAD | PHRASE | EXACT | TARGETING_EXPRESSION | ...
            'matchType',
            'keyword',         # readable keyword text
            'impressions', 'clicks', 'cost',
            'purchases7d', 'sales7d', 'unitsSoldClicks7d',
            'clickThroughRate', 'costPerClick',
            'acosClicks7d', 'roasClicks7d',
        ],
    ),
    'sp_search_term': dict(
        adProduct='SPONSORED_PRODUCTS',
        reportTypeId='spSearchTerm',
        groupBy=['searchTerm'],
        columns=[
            'campaignId', 'adGroupId', 'keywordId',
            'keyword',          # ← not 'keywordText'
            'matchType', 'searchTerm',
            'impressions', 'clicks', 'cost',
            'purchases7d', 'sales7d', 'unitsSoldClicks7d',
            'clickThroughRate', 'costPerClick',
            'acosClicks7d', 'roasClicks7d',
        ],
    ),
    'sp_advertised_product': dict(
        adProduct='SPONSORED_PRODUCTS',
        reportTypeId='spAdvertisedProduct',
        groupBy=['advertiser'],
        columns=[
            'campaignId', 'adGroupId',
            'advertisedAsin', 'advertisedSku',
            'impressions', 'clicks', 'cost',
            'purchases7d', 'sales7d', 'unitsSoldClicks7d',
        ],
    ),
    'sp_placement': dict(
        adProduct='SPONSORED_PRODUCTS',
        reportTypeId='spCampaigns',
        groupBy=['campaign', 'campaignPlacement'],
        columns=[
            'campaignId', 'campaignName', 'placementClassification',
            'impressions', 'clicks', 'cost',
            'purchases7d', 'sales7d',
        ],
    ),

    # ── SPONSORED BRANDS ──────────────────────────────────────────────────────
    # sb_adgroup dropped — no sbAdGroups reportTypeId. Aggregate at read time.
    'sb_targeting': dict(
        adProduct='SPONSORED_BRANDS',
        reportTypeId='sbTargeting',
        groupBy=['targeting'],
        columns=[
            'campaignId', 'adGroupId', 'keywordId',
            'keywordText', 'matchType',
            'impressions', 'clicks', 'cost',
            'purchases', 'sales',
        ],
    ),
    'sb_search_term': dict(
        adProduct='SPONSORED_BRANDS',
        reportTypeId='sbSearchTerm',
        groupBy=['searchTerm'],
        columns=[
            'campaignId', 'adGroupId', 'keywordId',
            'keywordText', 'matchType', 'searchTerm',
            'impressions', 'clicks', 'cost',
            'purchases', 'sales',
        ],
    ),
    'sb_advertised_product': dict(
        adProduct='SPONSORED_BRANDS',
        reportTypeId='sbPurchasedProduct',
        groupBy=['purchasedAsin'],
        # SB purchased-product report has NO impressions/clicks/cost — those
        # are only meaningful at campaign or ad-group level. Attribution metrics
        # use 14-day SB window: sales14d / unitsSold14d / orders14d.
        # No SKU equivalent either — SB only returns purchasedAsin.
        columns=[
            'campaignId', 'adGroupId',
            'purchasedAsin', 'productName',
            'sales14d', 'unitsSold14d', 'orders14d',
        ],
    ),
    # sb_placement DROPPED — Amazon's sbCampaigns reportTypeId does NOT accept
    # groupBy=campaignPlacement (only `campaign`). SB has no placement
    # breakdown report. (Sponsored Brands placement performance is available
    # in the Ads Console UI but not via the v3 reporting API.)

    # ── SPONSORED DISPLAY ─────────────────────────────────────────────────────
    # No search-term, no placement, no standalone ad-group report.
    'sd_targeting': dict(
        adProduct='SPONSORED_DISPLAY',
        reportTypeId='sdTargeting',
        groupBy=['targeting'],
        columns=[
            'campaignId', 'adGroupId',
            'targetingExpression', 'targetingId',
            'impressions', 'clicks', 'cost',
            'purchases', 'sales',
        ],
    ),
    'sd_advertised_product': dict(
        adProduct='SPONSORED_DISPLAY',
        reportTypeId='sdAdvertisedProduct',
        groupBy=['advertiser'],
        columns=[
            'campaignId', 'adGroupId',
            'promotedAsin', 'promotedSku',
            'impressions', 'clicks', 'cost',
            'purchases', 'sales', 'unitsSold',
        ],
    ),
}


# Which AdsDataSyncLog source-code each report kind maps to
REPORT_KIND_TO_SYNC_SOURCE: dict[str, str] = {
    kind: f'{kind}_daily' for kind in REPORT_CONFIGS
}


def split_kind(report_kind: str) -> tuple[str, str]:
    """'sp_search_term' → ('sp', 'search_term')"""
    parts = report_kind.split('_', 1)
    return parts[0], parts[1]


# ── Row normalizers ─────────────────────────────────────────────────────────

def _safe_int(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _safe_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _normalize_placement(raw: str) -> str:
    """
    Amazon Ads API v3 returns human-readable placement strings via
    `placementClassification`. Confirmed values from a live USA SP report:
        'Top of Search on-Amazon'      → top_of_search
        'Detail Page on-Amazon'        → product_pages
        'Other on-Amazon'              → other_on_amazon
                                          ^ Important: Amazon's v3 placement
                                          report does NOT break out Rest of
                                          Search separately. ROS is rolled
                                          into 'Other on-Amazon' along with
                                          cart, post-purchase, etc.
        'Off Amazon'                   → off_amazon (partner / affiliate sites)

    Older constant-style values (PLACEMENT_TOP, etc.) and the legacy
    "Rest of Search" string (which Amazon used to surface but no longer does)
    are kept as defensive fallbacks in case the API revives them.
    """
    s = (raw or '').upper()
    if not s:
        return 'other_on_amazon'
    if 'TOP OF SEARCH' in s or 'TOP_OF_SEARCH' in s \
            or 'PLACEMENTTOP' in s or 'PLACEMENT_TOP' in s:
        return 'top_of_search'
    if 'DETAIL PAGE' in s or 'PRODUCT PAGE' in s \
            or 'PRODUCT_PAGE' in s or 'DETAIL_PAGE' in s or 'PLACEMENT_PRODUCT' in s:
        return 'product_pages'
    if 'OFF AMAZON' in s or 'OFF-AMAZON' in s or 'OFF_AMAZON' in s:
        return 'off_amazon'
    # Legacy Rest-of-Search support — Amazon may revive it; map to its own bucket
    # so historical/future data stays meaningful if it ever appears.
    if 'REST OF SEARCH' in s or 'REST_OF_SEARCH' in s \
            or 'PLACEMENTREST' in s or 'PLACEMENT_REST' in s:
        return 'rest_of_search'
    # Default: 'Other on-Amazon' (includes rest-of-search, cart, post-purchase).
    return 'other_on_amazon'


def _infer_target_type(raw_row: dict, ad_type: str) -> str:
    """Heuristic target_type classification.

    Amazon's type hint column varies:
      SP : `keywordType`            (BROAD/PHRASE/EXACT/TARGETING_EXPRESSION/etc.)
      SB : `expressionType`         (KEYWORD / PRODUCT / AUTO)
      SD : `expressionType`         (similar)
    The expression column also varies (`targeting`, `targetingText`).
    """
    type_hint = ((raw_row.get('keywordType') or
                  raw_row.get('expressionType') or '').upper())
    if 'AUTO' in type_hint:
        return 'auto'
    if 'PRODUCT' in type_hint:
        return 'product_asin'
    if 'CATEGORY' in type_hint:
        return 'product_category'
    if 'AUDIENCE' in type_hint:
        return 'audience'
    # Specific keyword match types (BROAD/PHRASE/EXACT) → keyword
    if any(m in type_hint for m in ('BROAD', 'PHRASE', 'EXACT')):
        return 'keyword'
    if raw_row.get('keyword') or raw_row.get('keywordText') or raw_row.get('matchType'):
        return 'keyword'

    expr = ((raw_row.get('targeting') or
             raw_row.get('targetingText') or '')).lower()
    if 'asin=' in expr:
        return 'product_asin'
    if 'category=' in expr:
        return 'product_category'
    if 'audience=' in expr or ad_type == 'sd':
        return 'audience'
    if 'views' in expr or 'purchases' in expr:
        return 'contextual'
    return 'other'


def normalize_row(report_kind: str, raw_row: dict[str, Any],
                  marketplace: str, date_str: str) -> dict[str, Any]:
    """
    Translate one Amazon report row into a dict matching our snapshot table fields.

    Returns a dict with field names matching the corresponding model. Caller
    builds the Django model instance from this dict directly.
    """
    ad_type, subtype = split_kind(report_kind)

    # Attribution-window-aware key picks — Amazon uses different column names
    # depending on adProduct AND report type:
    #     SP all reports         → purchases7d, sales7d, unitsSoldClicks7d
    #     SB targeting / search  → purchases, sales, unitsSold
    #     SB purchasedProduct    → orders14d, sales14d, unitsSold14d  (NB: this report uses _14d suffix)
    #     SD all reports         → purchases, sales, unitsSold
    if ad_type == 'sp':
        orders_key, sales_key, units_key = 'purchases7d', 'sales7d', 'unitsSoldClicks7d'
    elif report_kind == 'sb_advertised_product':
        # sbPurchasedProduct uses 14d-suffixed columns
        orders_key, sales_key, units_key = 'orders14d', 'sales14d', 'unitsSold14d'
    else:
        orders_key, sales_key, units_key = 'purchases', 'sales', 'unitsSold'

    common: dict[str, Any] = {
        'marketplace':    marketplace,
        'date':           date_str,
        'source_ad_type': ad_type,
        'campaign_id':    str(raw_row.get('campaignId') or '')[:64],
        'impressions':    _safe_int(raw_row.get('impressions')),
        'clicks':         _safe_int(raw_row.get('clicks')),
        'spend':          round(_safe_float(raw_row.get('cost')), 4),
        'orders_7d':      _safe_int(raw_row.get(orders_key)),
        'sales_7d':       round(_safe_float(raw_row.get(sales_key)), 4),
        'units_7d':       _safe_int(raw_row.get(units_key)),
    }

    # Derived ratio columns — guard div-zero
    impr   = common['impressions']
    clicks = common['clicks']
    spend  = common['spend']
    sales  = common['sales_7d']
    orders = common['orders_7d']

    common['ctr']  = round(clicks / impr,    6) if impr   else 0.0
    common['cvr']  = round(orders / clicks,  6) if clicks else 0.0
    common['cpc']  = round(spend  / clicks,  4) if clicks else 0.0
    common['acos'] = round(spend  / sales,   6) if sales  else 0.0
    common['roas'] = round(sales  / spend,   4) if spend  else 0.0

    # ── Subtype-specific fields ─────────────────────────────────────────────
    if subtype == 'targeting':
        # Amazon's column names DIFFER per ad product:
        #   SP : `targeting` (expression), `keyword` (text), `keywordType`
        #   SB : `keywordText` (text), `expressionType` (PAT type)
        #   SD : `targetingText` / `expressionType`
        common['ad_group_id'] = str(raw_row.get('adGroupId') or '')[:64]
        common['target_id']   = str(raw_row.get('keywordId') or
                                    raw_row.get('targetingId') or '')[:64]
        common['target_type'] = _infer_target_type(raw_row, ad_type)
        common['expression']  = ((raw_row.get('keyword') or          # SP
                                  raw_row.get('keywordText') or      # SB
                                  raw_row.get('targeting') or        # SP expression
                                  raw_row.get('targetingText') or    # SD
                                  '')[:512])
        common['match_type']  = (raw_row.get('matchType') or '').lower()[:12]

    elif subtype == 'search_term':
        common['ad_group_id'] = str(raw_row.get('adGroupId') or '')[:64]
        common['target_id']   = str(raw_row.get('keywordId') or '')[:64]
        common['match_type']  = (raw_row.get('matchType') or '').lower()[:12]
        st = (raw_row.get('searchTerm') or '')[:512]
        common['search_term']      = st
        common['search_term_hash'] = hashlib.sha1(st.lower().encode('utf-8')).hexdigest()
        # Drop ratio columns — search-term rows store raw numbers; ratios are
        # recomputed at read time over the chosen rollup level.
        for k in ('ctr', 'cvr', 'cpc', 'acos', 'roas'):
            common.setdefault(k, common[k])  # keep for raw display, optional

    elif subtype == 'advertised_product':
        # ASIN key varies per ad product
        asin_key = ({'sp': 'advertisedAsin',
                     'sb': 'purchasedAsin',
                     'sd': 'promotedAsin'}).get(ad_type, 'advertisedAsin')
        sku_key  = ({'sp': 'advertisedSku',
                     'sb': '',                # SB purchasedProduct has no SKU
                     'sd': 'promotedSku'}).get(ad_type, '')

        common['ad_group_id']    = str(raw_row.get('adGroupId') or '')[:64]
        common['asin']           = (raw_row.get(asin_key) or '')[:16]
        common['advertised_sku'] = (raw_row.get(sku_key, '') if sku_key else '')[:64]

        # advertised_product rows don't need acos/roas/ctr/cvr/cpc — strip them
        for k in ('ctr', 'cvr', 'cpc', 'acos', 'roas'):
            common.pop(k, None)

    elif subtype == 'placement':
        common['placement'] = _normalize_placement(
            raw_row.get('placementClassification') or '')
        # No keyword/target context; drop ratios we computed (ACOS/ROAS still useful)
        for k in ('ctr', 'cvr', 'cpc'):
            common.pop(k, None)

    return common


def report_kind_for_ad_type_and_subtype(ad_type: str, subtype: str) -> str | None:
    """Inverse helper: ('sp', 'search_term') → 'sp_search_term'."""
    kind = f'{ad_type}_{subtype}'
    return kind if kind in REPORT_CONFIGS else None


def all_report_kinds_for(ad_type: str) -> list[str]:
    return [k for k in REPORT_CONFIGS if k.startswith(f'{ad_type}_')]
