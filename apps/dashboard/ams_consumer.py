"""
apps/dashboard/ams_consumer.py — pure parsers for AMS S3 records.

AMS publishes one event per (campaign, hour) for each ad product. Firehose
writes them into S3 as NDJSON-or-concatenated-JSON. Each record is the bare
AMS payload (no SNS envelope on the Firehose path).

This module is import-pure (no DB writes, no boto3 calls) so it can be
unit-tested without AWS. The management command in
`apps/dashboard/management/commands/ingest_ams_s3.py` orchestrates I/O.

Datasets we currently fold into PPCCampaignHourlySnapshot:

  ┌────────────────┬──────────────────────────────────────────────────────────┐
  │ Dataset        │ Fields we use                                            │
  ├────────────────┼──────────────────────────────────────────────────────────┤
  │ sp-traffic     │ impressions, clicks, cost                                │
  │ sp-conversion  │ purchases1d, sales1d, units1d   (1d attribution)         │
  │ sb-traffic     │ impressions, clicks, cost                                │
  │ sb-conversion  │ attributed_conversions_14d,                              │
  │                │ attributed_sales_14d,                                    │
  │                │ attributed_units_ordered_14d    (14d attribution)        │
  │ sd-traffic     │ impressions, clicks, cost                                │
  │ sd-conversion  │ attributed_conversions_14d,                              │
  │                │ attributed_sales_14d,                                    │
  │                │ attributed_units_ordered_14d    (14d attribution)        │
  │ budget-usage   │ routed separately, NOT folded into hourly snapshot       │
  └────────────────┴──────────────────────────────────────────────────────────┘

Note on attribution windows:
  SP gives us 1-day attribution → settles within 24 hours.
  SB / SD give us only 14-day attribution → values may revise upward for up to
  60 days post-click as conversions roll in. We accept this drift; upserts in
  the consumer always overwrite the (campaign, hour) row with the latest seen.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


# SNS topic-account → dataset (used as fallback when payload doesn't say).
# These are documented per-dataset in Amazon's AMS dataset reference.
_SNS_ACCOUNT_TO_DATASET = {
    # NA (us-east-1)
    '906013806264': 'sp-traffic',
    '802324068763': 'sp-conversion',
    '055588217351': 'budget-usage',
    '709476672186': 'sb-traffic',
    '154357381721': 'sb-conversion',
    '370941301809': 'sd-traffic',
    '877712924581': 'sd-conversion',
    # EU (eu-west-1) — sp only filled in; SB/SD will be added as needed
    '668473351658': 'sp-traffic',
    '562877083794': 'sp-conversion',
    '675750596317': 'budget-usage',
    # FE (us-west-2)
    '074266271188': 'sp-traffic',
    '622939981599': 'sp-conversion',
    '100899330244': 'budget-usage',
}

# Map dataset_id → (ad_product, kind). ad_product is 'sp'/'sb'/'sd'; kind is
# 'traffic' (impressions/clicks/cost) or 'conversion' (orders/sales/units).
DATASET_INFO = {
    'sp-traffic':    ('sp', 'traffic'),
    'sp-conversion': ('sp', 'conversion'),
    'sb-traffic':    ('sb', 'traffic'),
    'sb-conversion': ('sb', 'conversion'),
    'sd-traffic':    ('sd', 'traffic'),
    'sd-conversion': ('sd', 'conversion'),
    'budget-usage':  ('budget', 'usage'),
}


@dataclass
class HourlyBucket:
    """
    Aggregated cell ready to upsert into PPCCampaignHourlySnapshot.

    One bucket per (marketplace, date, hour, campaign_id, campaign_type).
    Amazon campaign IDs are globally unique across SP/SB/SD, but we still
    record campaign_type so the read side can split metrics by ad product.

    Traffic-side fields (sp-traffic / sb-traffic / sd-traffic):
      spend, impressions, clicks
    Conversion-side fields (sp-conversion / sb-conversion / sd-conversion):
      orders_7d, sales_7d, units_7d
      (SP uses 1d attribution; SB/SD use 14d — field name stays *_7d for
       backward compatibility with the existing schema.)
    """
    marketplace:    str
    date:           str   # ISO YYYY-MM-DD in marketplace local TZ
    hour:           int
    campaign_id:    str
    campaign_type:  str = 'sp'         # 'sp' | 'sb' | 'sd'
    campaign_name:  str = ''
    # traffic
    spend:          Decimal = field(default_factory=lambda: Decimal('0'))
    impressions:    int     = 0
    clicks:         int     = 0
    # conversion
    orders_7d:      int     = 0
    sales_7d:       Decimal = field(default_factory=lambda: Decimal('0'))
    units_7d:       int     = 0
    # sources observed
    saw_traffic:    bool = False
    saw_conversion: bool = False

    def key(self):
        return (self.marketplace, self.date, self.hour, self.campaign_id)


# ─────────────────────────────────────────────────────────────────────────────
# SNS envelope + dataset identification
# ─────────────────────────────────────────────────────────────────────────────
def iter_json_objects(text: str):
    """
    Yield JSON objects from a string that contains zero-or-more JSON values
    *concatenated* with no delimiter (which is what AMS-via-Firehose actually
    writes — `{...}{...}{...}` on one line). Tolerates whitespace and stray
    newlines between objects, and skips garbage between failed decodes.
    """
    decoder = json.JSONDecoder()
    idx, length = 0, len(text)
    while idx < length:
        # Skip whitespace
        while idx < length and text[idx] in ' \t\r\n,':
            idx += 1
        if idx >= length:
            return
        try:
            obj, end = decoder.raw_decode(text, idx)
            idx = end
            yield obj
        except json.JSONDecodeError:
            # advance one char and try to resync (rare path; mostly partial trailing object)
            idx += 1


def parse_envelope(raw_obj) -> tuple[dict | None, str | None]:
    """
    Normalise one raw record into (payload_dict, dataset_id_hint).

    AMS-via-Firehose records can arrive as:
      1) Bare AMS event JSON (current case — has `dataset_id` field directly)
      2) SNS envelope {"Type":"Notification","Message":"…","TopicArn":"…"} — legacy
      3) {"Type":"SubscriptionConfirmation",...} — ignore

    `raw_obj` is already parsed (a dict). For legacy compatibility we still
    accept a string and parse it.
    """
    if isinstance(raw_obj, str):
        try:
            outer = json.loads(raw_obj)
        except json.JSONDecodeError:
            return None, None
    elif isinstance(raw_obj, dict):
        outer = raw_obj
    else:
        return None, None

    msg_type = outer.get('Type') or outer.get('type')
    if msg_type == 'SubscriptionConfirmation':
        return None, None

    if 'Message' in outer and isinstance(outer['Message'], str):
        # Legacy SNS envelope path
        try:
            payload = json.loads(outer['Message'])
        except json.JSONDecodeError:
            return None, None
        ds_hint = _dataset_from_topic_arn(outer.get('TopicArn', ''))
        return payload, ds_hint

    # Bare payload — AMS may include dataset_id directly
    ds_hint = outer.get('dataset_id')
    return outer, ds_hint


def _dataset_from_topic_arn(arn: str) -> str | None:
    """`arn:aws:sns:us-east-1:906013806264:xxx` → 'sp-traffic'"""
    try:
        account = arn.split(':')[4]
    except (IndexError, AttributeError):
        return None
    return _SNS_ACCOUNT_TO_DATASET.get(account)


def infer_dataset(payload: dict, hint: str | None) -> str | None:
    """
    Decide which dataset a payload belongs to.

    Priority:
      1. Hint from SNS topic ARN or (preferably) the payload's `dataset_id`
         field — both are authoritative and always present in modern AMS
         payloads on the Firehose path.
      2. Fall back to field-shape sniffing for legacy / unusual payloads.
    """
    if hint and hint in DATASET_INFO:
        return hint

    # `dataset_id` is in the payload itself on the Firehose path
    ds = payload.get('dataset_id')
    if ds and ds in DATASET_INFO:
        return ds

    # ── Shape-based fallback ───────────────────────────────────────────
    if 'percentage_of_budget_used' in payload \
       or 'campaign_budget_amount' in payload:
        return 'budget-usage'
    if 'purchases1d' in payload or 'sales1d' in payload:
        return 'sp-conversion'
    # SB / SD conversion shapes use *_14d fields; we can't tell SB from SD by
    # shape alone (same schema). Without a hint we leave it ambiguous.
    if 'attributed_sales_14d' in payload:
        return None
    if 'cost' in payload and 'impressions' in payload:
        # Could be sp/sb/sd traffic — can't tell without a hint.
        return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Per-payload extraction
# ─────────────────────────────────────────────────────────────────────────────
def time_window_to_local(payload: dict, tz_name: str) -> tuple[str, int] | None:
    """
    Convert the payload's `time_window_start` (UTC ISO) to (local_date, local_hour).
    AMS bucket boundaries are minute-aligned; we floor to the hour.
    """
    tw = payload.get('time_window_start') or payload.get('startTime')
    if not tw:
        return None
    try:
        # Accept both "2026-06-10T15:00:00Z" and "2026-06-10T15:00:00+00:00"
        ts = tw.replace('Z', '+00:00')
        dt_utc = datetime.fromisoformat(ts)
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None
    dt_local = dt_utc.astimezone(ZoneInfo(tz_name))
    return dt_local.date().isoformat(), dt_local.hour


def _num(payload: dict, cast, *keys):
    """
    First key present with a non-null value, cast to `cast` (int or Decimal).

    Lets one call site accept several spellings of the same metric — see the
    note in fold_into_bucket about AMS conversion field naming.
    """
    for k in keys:
        v = payload.get(k)
        if v is not None:
            try:
                return cast(str(v)) if cast is Decimal else cast(v)
            except (TypeError, ValueError, ArithmeticError):
                continue
    return cast(0)


def fold_into_bucket(
    buckets: dict,
    marketplace: str,
    tz_name: str,
    payload: dict,
    dataset: str,
) -> bool:
    """
    Mutates `buckets` in place. Returns True if the record was applied.

    Bucket key includes campaign_type so SP/SB/SD campaign IDs (which Amazon
    guarantees globally unique) still get distinct rows in the snapshot table
    — this future-proofs us against any rare ID collisions.

    Field mapping per ad product:
      ┌──────────────────┬──────────────────────────────────────────────┐
      │ traffic events   │ all 3 use: impressions, clicks, cost         │
      ├──────────────────┼──────────────────────────────────────────────┤
      │ sp-conversion    │ purchases1d / sales1d / units1d   (1d attr.) │
      │ sb-conversion    │ attributed_conversions_14d /                 │
      │                  │ attributed_sales_14d /                       │
      │                  │ attributed_units_ordered_14d      (14d attr.)│
      │ sd-conversion    │ same field names as sb-conversion (14d attr.)│
      └──────────────────┴──────────────────────────────────────────────┘
    """
    info = DATASET_INFO.get(dataset)
    if not info or info[0] == 'budget':
        return False    # budget-usage is routed separately

    ad_product, kind = info     # e.g. ('sb', 'traffic')

    bucket_date_hour = time_window_to_local(payload, tz_name)
    if not bucket_date_hour:
        return False
    date_str, hour = bucket_date_hour

    campaign_id = str(payload.get('campaign_id') or '').strip()
    if not campaign_id:
        return False
    campaign_name = (payload.get('campaign_name') or '')[:256]

    # campaign_type is part of the bucket key so SP/SB/SD campaigns each get
    # their own row even if (improbably) Amazon ever reused IDs across products.
    key = (marketplace, date_str, hour, campaign_id, ad_product)
    bucket = buckets.get(key)
    if bucket is None:
        bucket = HourlyBucket(
            marketplace=marketplace, date=date_str, hour=hour,
            campaign_id=campaign_id, campaign_type=ad_product,
            campaign_name=campaign_name,
        )
        buckets[key] = bucket
    elif not bucket.campaign_name and campaign_name:
        bucket.campaign_name = campaign_name

    if kind == 'traffic':
        bucket.impressions += int(payload.get('impressions') or 0)
        bucket.clicks      += int(payload.get('clicks')      or 0)
        bucket.spend       += Decimal(str(payload.get('cost') or 0))
        bucket.saw_traffic = True
        return True

    # kind == 'conversion'
    #
    # Field naming is the fragile part here. AMS payloads are snake_case
    # (`time_window_start`, `campaign_id`, `idempotency_id`), and the traffic
    # metrics we read — impressions / clicks / cost — are single words, so they
    # match under any convention. The conversion metrics carry an attribution
    # window suffix, where the spellings genuinely differ between Amazon's
    # report columns (`purchases1d`) and the stream (`purchases_1d`), and a
    # miss silently yields 0 — which is how spend populated while sales,
    # orders, ACoS, ROAS and CVR all stayed empty. Accept every known spelling
    # rather than betting on one.
    # Window choice: the destination columns are orders_7d / sales_7d /
    # units_7d, and 7-day is Amazon's default attribution for Sponsored
    # Products (what Seller Central reports). The old code read the 1-day
    # window into those 7-day columns — understating conversions even once
    # the key names are right, since a click today can convert days later.
    if ad_product == 'sp':
        bucket.orders_7d += _num(payload, int, 'purchases_7d',
                                 'attributed_conversions_7d')
        bucket.sales_7d  += _num(payload, Decimal, 'sales_7d',
                                 'attributed_sales_7d')
        bucket.units_7d  += _num(payload, int, 'units_sold_7d',
                                 'attributed_units_ordered_7d')
    else:    # sb / sd → 14-day attribution columns
        bucket.orders_7d += _num(payload, int, 'attributed_conversions_14d',
                                 'purchases_14d', 'conversions_14d')
        bucket.sales_7d  += _num(payload, Decimal, 'attributed_sales_14d',
                                 'sales_14d')
        bucket.units_7d  += _num(payload, int, 'attributed_units_ordered_14d',
                                 'units_sold_14d', 'units_14d')
    bucket.saw_conversion = True
    return True
