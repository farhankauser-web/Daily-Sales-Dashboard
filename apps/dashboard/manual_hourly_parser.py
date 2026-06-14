"""
apps/dashboard/manual_hourly_parser.py — Parse Seller Central hourly CSVs.

Amazon's GUI lets advertisers download "hourly" SP / SB / SD reports up to
14 days at a time, for up to ~30 days back. These are the only path to fill
hourly history for days before AMS was active.

The CSV layout varies a little across reports (column names, decimal style,
encoding) so this parser:
  • Auto-detects UTF-8 / UTF-16 / CP1252
  • Maps column names case-insensitively against several known aliases
  • Reports back what was found vs missing
  • Coerces "Date" + "Hour" → (date, 0..23) in the report's TZ (assumed to
    match marketplace local TZ, which is how the GUI emits them)
  • Aggregates by (date, hour, campaign_id|campaign_name)

Pure functions — no DB writes. The view / management command does that.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta
from decimal import Decimal
from typing import Iterable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Column-name aliases (case-insensitive, ignoring spaces/punctuation)
# Each metric maps to a tuple of acceptable raw header strings.
# ─────────────────────────────────────────────────────────────────────────────
_ALIASES = {
    'date':          ('date', 'day', 'report date', 'reporting date',
                      'start date', 'date/time', 'datetime', 'date and time',
                      'reporting period', 'period start'),
    'hour':          ('hour', 'hour of day', 'time', 'hour (24h)', 'hour of the day',
                      'start time', 'hour of day (utc)'),
    'campaign_id':   ('campaign id', 'campaignid', 'campaign id (number)'),
    'campaign_name': ('campaign name', 'campaign', 'name'),
    'impressions':   ('impressions', 'impr.', 'impr'),
    'clicks':        ('clicks',),
    'spend':         ('spend', 'cost', 'cost (usd)', 'spend (usd)',
                       'cost (us$)', 'spend (us$)', 'total spend'),
    'orders':        ('orders', 'purchases', '7 day total orders (#)',
                       '14 day total orders (#)',
                       '7 day total orders', '14 day total orders'),
    'sales':         ('sales', 'revenue', '7 day total sales',
                       '7 day total sales ($)', '7 day total sales (usd)',
                       '14 day total sales', '14 day total sales ($)',
                       '14 day total sales (usd)'),
    'units':         ('units', 'units ordered', '7 day total units (#)',
                       '14 day total units (#)',
                       '7 day total units', '14 day total units'),
}

# Match "2026-06-10", "06/10/2026", "10 Jun 2026", "Jun 10, 2026"
_DATE_PATTERNS = (
    '%Y-%m-%d',
    '%Y/%m/%d',
    '%m/%d/%Y',
    '%m-%d-%Y',
    '%d/%m/%Y',
    '%d-%m-%Y',
    '%d %b %Y',
    '%b %d, %Y',
    '%B %d, %Y',
)

_HOUR_RX = re.compile(r'(\d{1,2})')


@dataclass
class ParsedRow:
    date:          date_cls
    hour:          int
    campaign_id:   str
    campaign_name: str
    impressions:   int     = 0
    clicks:        int     = 0
    spend:         Decimal = field(default_factory=lambda: Decimal('0'))
    orders:        int     = 0
    sales:         Decimal = field(default_factory=lambda: Decimal('0'))
    units:         int     = 0


@dataclass
class ParseResult:
    rows:           list[ParsedRow] = field(default_factory=list)
    columns_found:  dict[str, str]  = field(default_factory=dict)  # metric → raw header
    columns_missing: list[str]      = field(default_factory=list)
    rows_in_file:   int             = 0
    rows_skipped:   int             = 0
    date_min:       date_cls | None = None
    date_max:       date_cls | None = None
    errors:         list[str]       = field(default_factory=list)

    @property
    def days_covered(self) -> int:
        if not (self.date_min and self.date_max):
            return 0
        return (self.date_max - self.date_min).days + 1


# ─────────────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────────────
def parse_hourly_csv(file_bytes: bytes, filename: str = '') -> ParseResult:
    """
    Parse a Seller-Central hourly export. Accepts bytes; auto-decodes.
    Returns ParseResult with rows + diagnostics.
    """
    result = ParseResult()

    text = _decode(file_bytes)
    if not text:
        result.errors.append('Could not decode file (tried utf-8, utf-16, cp1252).')
        return result

    reader = csv.reader(io.StringIO(text))
    rows_iter = iter(reader)

    # Amazon sometimes emits a 1-line preamble before the real header
    # ("Sponsored Products Performance Report - 06/10/2026"). We skip lines
    # until we find one that contains a known column alias.
    header_row = _find_header_row(rows_iter)
    if header_row is None:
        result.errors.append('No recognisable header row found in CSV.')
        return result

    cols = _map_columns(header_row)
    result.columns_found   = {m: cols[m]['raw'] for m in cols}
    result.columns_missing = [m for m in ('date', 'campaign_name', 'spend')
                              if m not in cols]
    if 'date' not in cols or ('campaign_name' not in cols and 'campaign_id' not in cols):
        result.errors.append(
            f'Required columns missing. Need Date + (Campaign Name or Campaign ID). '
            f'Found: {list(cols.keys())}')
        return result

    # Buckets keyed by (date, hour, campaign_id|campaign_name)
    buckets: dict[tuple, ParsedRow] = {}

    for raw_row in rows_iter:
        result.rows_in_file += 1
        if not raw_row or all(not c.strip() for c in raw_row):
            continue
        try:
            r = _parse_row(raw_row, cols)
        except Exception as e:
            result.rows_skipped += 1
            continue
        if r is None:
            result.rows_skipped += 1
            continue

        key = (r.date, r.hour,
               r.campaign_id or r.campaign_name or '?')
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = r
        else:
            existing.impressions += r.impressions
            existing.clicks      += r.clicks
            existing.spend       += r.spend
            existing.orders      += r.orders
            existing.sales       += r.sales
            existing.units       += r.units

    result.rows = list(buckets.values())

    if result.rows:
        ds = [r.date for r in result.rows]
        result.date_min = min(ds)
        result.date_max = max(ds)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Decoding + header detection
# ─────────────────────────────────────────────────────────────────────────────
def _decode(data: bytes) -> str | None:
    """Try utf-8 → utf-16 → cp1252. Amazon mixes all three."""
    for enc in ('utf-8-sig', 'utf-8', 'utf-16', 'cp1252', 'latin-1'):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _find_header_row(rows_iter) -> list[str] | None:
    """Skip preamble lines; return the first row that contains a known column."""
    seen = 0
    for row in rows_iter:
        seen += 1
        if seen > 10:    # safety guard: header must be in the first 10 lines
            return None
        if _row_has_known_column(row):
            return row
    return None


def _row_has_known_column(row) -> bool:
    norm = [_normalize_header(c) for c in row]
    for aliases in _ALIASES.values():
        if any(_normalize_header(a) in norm for a in aliases):
            return True
    return False


def _normalize_header(s: str) -> str:
    if not s:
        return ''
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _map_columns(header_row) -> dict:
    """
    Given the header row, return {metric_name: {'idx': int, 'raw': original}}
    for each metric we recognise.
    """
    norm = [_normalize_header(c) for c in header_row]
    out  = {}
    for metric, aliases in _ALIASES.items():
        for a in aliases:
            n = _normalize_header(a)
            if n in norm:
                idx = norm.index(n)
                out[metric] = {'idx': idx, 'raw': header_row[idx]}
                break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Per-row parsing
# ─────────────────────────────────────────────────────────────────────────────
def _parse_row(raw_row, cols) -> ParsedRow | None:
    def _get(metric):
        info = cols.get(metric)
        if info is None or info['idx'] >= len(raw_row):
            return ''
        return raw_row[info['idx']].strip()

    date_raw = _get('date')
    d = _parse_date(date_raw)
    if d is None:
        return None

    hour_raw = _get('hour')
    hour = _parse_hour(hour_raw)
    if hour is None:
        # If the file has no separate hour column but the date includes hour
        # (e.g. "2026-06-10 14:00"), parse from there:
        hour = _hour_from_date_string(date_raw)
    if hour is None:
        # Reject rows without an hour — this is an hourly report after all
        return None

    cid_raw  = _get('campaign_id')
    name_raw = _get('campaign_name')
    if not name_raw and not cid_raw:
        return None

    return ParsedRow(
        date          = d,
        hour          = hour,
        campaign_id   = cid_raw or _slug(name_raw)[:64],
        campaign_name = name_raw[:256],
        impressions   = _to_int(_get('impressions')),
        clicks        = _to_int(_get('clicks')),
        spend         = _to_decimal(_get('spend')),
        orders        = _to_int(_get('orders')),
        sales         = _to_decimal(_get('sales')),
        units         = _to_int(_get('units')),
    )


def _parse_date(s: str) -> date_cls | None:
    s = (s or '').strip()
    if not s:
        return None
    # ISO with optional time
    if re.match(r'^\d{4}-\d{2}-\d{2}', s):
        try:
            return date_cls.fromisoformat(s[:10])
        except ValueError:
            pass
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(s[:25], fmt).date()
        except ValueError:
            continue
    return None


def _parse_hour(s: str) -> int | None:
    s = (s or '').strip()
    if not s:
        return None
    m = _HOUR_RX.search(s)
    if not m:
        return None
    try:
        h = int(m.group(1))
    except ValueError:
        return None
    if 0 <= h <= 23:
        return h
    return None


def _hour_from_date_string(s: str) -> int | None:
    """If the date column is actually 'YYYY-MM-DD HH:00', extract HH."""
    m = re.search(r'\d{4}-\d{2}-\d{2}[T ](\d{1,2})', s or '')
    if m:
        try:
            h = int(m.group(1))
            if 0 <= h <= 23:
                return h
        except ValueError:
            pass
    return None


def _to_int(s: str) -> int:
    s = (s or '').replace(',', '').replace('$', '').replace('%', '').strip()
    if not s or s in ('-', '—', 'N/A'):
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _to_decimal(s: str) -> Decimal:
    s = (s or '').replace(',', '').replace('$', '').replace('%', '').strip()
    if not s or s in ('-', '—', 'N/A'):
        return Decimal('0')
    try:
        return Decimal(s)
    except Exception:
        return Decimal('0')


def _slug(s: str) -> str:
    return re.sub(r'\W+', '-', s).strip('-')
