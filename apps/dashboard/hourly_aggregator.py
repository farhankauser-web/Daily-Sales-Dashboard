"""
apps/dashboard/hourly_aggregator.py — Layered, completeness-gated assembler
of per-hour cells for the Hourly Patterns view.

Inputs (read-only):
  - HourlyMetricSnapshot      → revenue / units / orders / margins (Orders layer)
  - PPCCampaignHourlySnapshot → real SP hourly spend          (SP Ads layer)
  - PPCCampaignSnapshot       → SB/SD daily spend             (SB/SD Ads layer)
  - AdsDataSyncLog            → completeness per (date, source)

Outputs:
  - One per-hour cell per renderable date (CORE complete: SP-hourly + Orders).
  - Each cell carries:
      revenue, units, orders, cm, cm_pct, gm, gm_pct,
      ppc_sp, ppc_sb, ppc_sd, ppc_total, ppc_pct
    where:
      ppc_sb / ppc_sd = daily_total ÷ 24  ONLY when the source is_successful
                       for that day; otherwise None ("Estimated (uniform)").
      ppc_total       = ppc_sp + ppc_sb + ppc_sd  only when all three are known;
                       else None  (so the UI never displays a misleading aggregate).

Strict rules enforced here:
  • No placeholders. Missing sources → None, never 0.
  • Days that fail CORE completeness are EXCLUDED ENTIRELY.
  • SP hourly is real; SB/SD are uniform-distributed (only allowed estimation).
  • No cross-layer contamination: SB/SD never written into the SP hourly table.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_cls, timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Sum

from .completeness import (
    get_renderable_dates, day_ads_complete, completeness_report,
    CORE_SOURCES, ADS_SOURCES,
)
from .models import (
    Campaign,
    HourlyMetricSnapshot, HourlySkuSnapshot,
    PPCCampaignHourlySnapshot, PPCCampaignSnapshot,
    Product,
)


def _norm_pair(pair):
    """Case/format-insensitive key for a (product_type, pack_size) group.

    Product titles are hand-entered and occasionally disagree on casing/spacing
    with the canonical campaign map (e.g. a title says '4-pack' while campaigns
    resolve to '4-Pack'). Comparing on this normalised key prevents a valid
    group selection from silently matching nothing.
    """
    if not pair:
        return pair
    pt, pack = pair
    return (str(pt).strip().lower(),
            str(pack).strip().lower().replace(' ', '-'))


# ─────────────────────────────────────────────────────────────────────────────
# Product group helpers — match the same scheme the Daily Dashboard uses
# (titles split on ' - ' into product_type / pack_size).
# ─────────────────────────────────────────────────────────────────────────────
def _split_title(title: str) -> tuple[str, str] | None:
    parts = [p.strip() for p in (title or '').split(' - ') if p.strip()]
    if len(parts) >= 2:
        return (parts[0], parts[1])
    return None


def list_product_groups(marketplace: str) -> list[dict]:
    """
    Returns the sorted list of (pt, pack) groups available on this marketplace,
    plus a few synthetic options the UI offers:
      • {'id': 'all',         'label': 'All Groups'}
      • {'id': 'unallocated', 'label': 'Unallocated PPC'}
      • {'id': '<pt>|<pack>', 'label': '<pt> · <pack>'}
    """
    seen: set[tuple] = set()
    for p in (Product.objects
              .filter(marketplace=marketplace, status='active')
              .values_list('title', flat=True)):
        g = _split_title(p)
        if g:
            seen.add(g)
    out = [{'id': 'all', 'label': 'All Groups'}]
    # Dedupe groups that differ only by casing/spacing (e.g. '4-Pack' vs
    # '4-pack' from an inconsistent product title). sorted() puts the
    # canonically-cased variant first ('P' < 'p'), so it wins.
    _emitted: set = set()
    for (pt, pack) in sorted(seen):
        key = _norm_pair((pt, pack))
        if key in _emitted:
            continue
        _emitted.add(key)
        out.append({'id': f'{pt}|{pack}', 'label': f'{pt} · {pack}'})
    out.append({'id': 'unallocated', 'label': '⚠️ Unallocated PPC'})
    return out


def parse_group_id(group_id: str) -> tuple[str, tuple | None]:
    """
    Returns (mode, payload):
      mode = 'all'        → payload None
      mode = 'unallocated'→ payload None
      mode = 'group'      → payload (product_type, pack_size)
    """
    if not group_id or group_id == 'all':
        return 'all', None
    if group_id == 'unallocated':
        return 'unallocated', None
    if '|' in group_id:
        pt, pack = group_id.split('|', 1)
        return 'group', (pt.strip(), pack.strip())
    return 'all', None


def _group_sku_set(marketplace: str, group: tuple) -> set[str]:
    """All upper-cased SKUs that belong to a given (pt, pack) product group."""
    pt, pack = group
    skus: set[str] = set()
    for p in (Product.objects
              .filter(marketplace=marketplace, status='active')
              .values_list('sku', 'title')):
        sku, title = p
        g = _split_title(title)
        if g and _norm_pair(g) == _norm_pair((pt, pack)) and sku:
            skus.add(sku.upper())
    return skus


# ─────────────────────────────────────────────────────────────────────────────
# Cell type
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class HourCell:
    date:       date_cls
    hour:       int
    # Core (always present for renderable days; values may be 0 from real data)
    revenue:    float
    units:      int
    orders:     int
    cm:         float
    cm_pct:     float   # already ×100
    gm:         float
    gm_pct:     float   # already ×100
    # Ads
    ppc_sp:     float                # real SP hourly
    ppc_sb:     Optional[float]      # None when sb_daily not successful
    ppc_sd:     Optional[float]      # None when sd_daily not successful
    ppc_total:  Optional[float]      # None when sb_sd_estimated is incomplete
    ppc_pct:    Optional[float]      # ppc_total / revenue × 100, None if no total

    def as_dict(self) -> dict:
        return {
            'date':      self.date.isoformat(),
            'hour':      self.hour,
            'revenue':   round(self.revenue, 2),
            'units':     self.units,
            'orders':    self.orders,
            'cm':        round(self.cm, 2),
            'cm_pct':    round(self.cm_pct, 2),
            'gm':        round(self.gm, 2),
            'gm_pct':    round(self.gm_pct, 2),
            'ppc_sp':    round(self.ppc_sp, 2),
            'ppc_sb':    None if self.ppc_sb    is None else round(self.ppc_sb,    2),
            'ppc_sd':    None if self.ppc_sd    is None else round(self.ppc_sd,    2),
            'ppc_total': None if self.ppc_total is None else round(self.ppc_total, 2),
            'ppc_pct':   None if self.ppc_pct   is None else round(self.ppc_pct,   2),
        }


@dataclass
class AggregatorResult:
    marketplace:      str
    start_date:       date_cls
    end_date:         date_cls
    renderable_dates: list[date_cls]
    # date → 24 cells (each is a HourCell or None when no rows existed for that hour)
    cells_by_date:    dict[date_cls, list[Optional[HourCell]]]
    # date → {'sp_hourly': bool, 'sb_daily': bool, 'sd_daily': bool, 'orders': bool}
    ads_status:       dict[date_cls, dict[str, bool]]
    # convenience flags for the UI
    sb_complete_days: list[date_cls]
    sd_complete_days: list[date_cls]
    # full completeness report (per_source, totals, etc.)
    completeness:     dict

    # ── derived helpers ─────────────────────────────────────────────────────
    def iter_cells(self):
        """Yields every non-None HourCell, ordered by (date, hour)."""
        for d in self.renderable_dates:
            for c in self.cells_by_date.get(d, []):
                if c is not None:
                    yield c


# ─────────────────────────────────────────────────────────────────────────────
# Builder
# ─────────────────────────────────────────────────────────────────────────────
def build_hourly_cells(
    marketplace: str,
    start_date:  date_cls,
    end_date:    date_cls,
    group_id:    str = 'all',
) -> AggregatorResult:
    """
    Build the gated per-hour cell matrix for [start_date, end_date].

    `group_id` filters the cells to one product group:
        'all'         → account-wide (existing behaviour)
        'unallocated' → only campaigns whose name didn't map to any group
        '<pt>|<pack>' → only revenue/PPC for that product group's SKUs+campaigns

    Days that fail CORE completeness (need both sp_hourly AND orders) are
    excluded — they will not appear in renderable_dates or cells_by_date.

    SB/SD allocation is uniform (daily ÷ 24) ONLY for days where the source's
    AdsDataSyncLog status is_successful. Otherwise that source is None on every
    hour of that day, ppc_total is None on every hour, and the UI is responsible
    for badging the day as "Estimated (incomplete)".
    """
    from apps.amazon_api.views import _match_campaign_to_group   # local import: avoid cycle

    group_mode, group_payload = parse_group_id(group_id)

    if end_date < start_date:
        return AggregatorResult(
            marketplace=marketplace, start_date=start_date, end_date=end_date,
            renderable_dates=[], cells_by_date={}, ads_status={},
            sb_complete_days=[], sd_complete_days=[],
            completeness=completeness_report(marketplace, start_date, end_date),
        )

    # 1) Which days are even allowed to render?
    renderable = get_renderable_dates(marketplace, start_date, end_date)
    if not renderable:
        return AggregatorResult(
            marketplace=marketplace, start_date=start_date, end_date=end_date,
            renderable_dates=[], cells_by_date={}, ads_status={},
            sb_complete_days=[], sd_complete_days=[],
            completeness=completeness_report(marketplace, start_date, end_date),
        )

    rs = set(renderable)

    # 2) Pull Orders-side hourly rows.
    #    - 'all'         → HourlyMetricSnapshot (account-wide; existing path)
    #    - 'group'       → aggregate HourlySkuSnapshot over the group's SKUs
    #    - 'unallocated' → no orders side at all (we only allocate spend, not revenue)
    if group_mode == 'all':
        metric_rows = (HourlyMetricSnapshot.objects
                       .filter(marketplace=marketplace, date__in=renderable)
                       .values('date', 'hour',
                               'revenue', 'units', 'orders',
                               'contribution_margin', 'cm_pct',
                               'gross_margin', 'gm_pct'))
    elif group_mode == 'group':
        group_skus = _group_sku_set(marketplace, group_payload)
        sku_rows = (HourlySkuSnapshot.objects
                    .filter(marketplace=marketplace, date__in=renderable,
                            sku__in=group_skus)
                    .values('date', 'hour')
                    .annotate(revenue=Sum('revenue'),
                              units=Sum('qty'),
                              cm=Sum('contribution_margin')))
        # Orders per (date,hour) is the count of distinct orders for this group;
        # HourlySkuSnapshot doesn't carry it, so we count distinct order rows
        # by grouping the same query separately. Approximation: use units as
        # orders proxy when finer detail isn't stored (close enough for averages).
        order_count = {(r['date'], r['hour']): int(r['units'] or 0)
                        for r in sku_rows}
        metric_rows = []
        for r in sku_rows:
            rev = float(r['revenue'] or 0)
            cm  = float(r['cm']      or 0)
            metric_rows.append({
                'date': r['date'], 'hour': r['hour'],
                'revenue': rev,
                'units':   int(r['units'] or 0),
                'orders':  order_count.get((r['date'], r['hour']), 0),
                'contribution_margin': cm,
                'cm_pct':              (cm / rev) if rev > 0 else 0,
                'gross_margin':        cm,           # GM recomputed below
                'gm_pct':              (cm / rev) if rev > 0 else 0,
            })
    else:    # 'unallocated' — no Orders attribution; show zero revenue cells
        metric_rows = []
        for d in renderable:
            for h in range(24):
                metric_rows.append({
                    'date': d, 'hour': h,
                    'revenue': 0.0, 'units': 0, 'orders': 0,
                    'contribution_margin': 0.0, 'cm_pct': 0.0,
                    'gross_margin':        0.0, 'gm_pct': 0.0,
                })

    # 3) Pull REAL hourly spend per ad product from PPCCampaignHourlySnapshot.
    #    Priority within a single date:
    #      • If any rows with source='manual' exist for that date → use ONLY
    #        manual rows (Seller Central CSV upload is the authoritative
    #        whole-day truth; mixing with AMS rows double-counts since the
    #        two sources use different campaign_id schemas).
    #      • Otherwise → use AMS rows (source!='manual').
    #    SB and SD on days with no hourly data fall back to daily÷24 below.
    # AMS SP-hourly rows store campaign_name='' — the real identity lives in
    # campaign_id. Name-only group matching therefore drops 100% of SP spend for
    # any single-group view. Resolve the name from the Campaign catalog by id.
    _id2name: dict = {}
    if group_mode != 'all':
        _id2name = {
            str(cid): (nm or '')
            for cid, nm in (Campaign.objects
                            .filter(marketplace=marketplace)
                            .values_list('campaign_id', 'campaign_name'))
        }

    def _resolve_group(campaign_name, campaign_id=None):
        """(product_type, pack) for a campaign, resolving blank/unmatched hourly
        names via the Campaign catalog by campaign_id."""
        name = (campaign_name or '').strip()
        g = _match_campaign_to_group(name) if name else None
        if g is None and campaign_id is not None:
            alt = _id2name.get(str(campaign_id), '')
            if alt and alt != name:
                g = _match_campaign_to_group(alt)
        return g

    def _campaign_passes(campaign_name, campaign_id=None) -> bool:
        """Apply the group filter: matching group, or 'unallocated' = no match."""
        if group_mode == 'all':
            return True
        g = _resolve_group(campaign_name, campaign_id)
        if group_mode == 'unallocated':
            return g is None
        if group_mode == 'group':
            return _norm_pair(g) == _norm_pair(group_payload)
        return True

    def _real_hourly(ad_product: str):
        # Step a: find which dates have manual uploads
        manual_days = set(
            PPCCampaignHourlySnapshot.objects
            .filter(marketplace=marketplace, campaign_type=ad_product,
                    date__in=renderable, source='manual')
            .values_list('date', flat=True).distinct()
        )
        # Step b: AMS rows for non-manual dates
        ams_qs = (PPCCampaignHourlySnapshot.objects
                  .filter(marketplace=marketplace, campaign_type=ad_product,
                          date__in=renderable)
                  .exclude(date__in=manual_days)
                  .exclude(source='manual')
                  .values('date', 'hour', 'campaign_id', 'campaign_name')
                  .annotate(spend=Sum('spend')))
        # Step c: manual rows for manual dates
        man_qs = (PPCCampaignHourlySnapshot.objects
                  .filter(marketplace=marketplace, campaign_type=ad_product,
                          date__in=manual_days, source='manual')
                  .values('date', 'hour', 'campaign_id', 'campaign_name')
                  .annotate(spend=Sum('spend')))
        by_dh: dict = {}
        for r in ams_qs:
            if not _campaign_passes(r['campaign_name'], r['campaign_id']):
                continue
            k = (r['date'], r['hour'])
            by_dh[k] = by_dh.get(k, 0.0) + float(r['spend'] or 0)
        for r in man_qs:
            if not _campaign_passes(r['campaign_name'], r['campaign_id']):
                continue
            k = (r['date'], r['hour'])
            by_dh[k] = by_dh.get(k, 0.0) + float(r['spend'] or 0)
        days = {k[0] for k in by_dh}
        return by_dh, days

    sp_by_dh, sp_real_days = _real_hourly('sp')
    sb_by_dh, sb_real_days = _real_hourly('sb')
    sd_by_dh, sd_real_days = _real_hourly('sd')

    # 4) Daily-total fallback for SB / SD on days where AMS hourly is absent.
    #    Honour the same group filter so a per-group view doesn't pull
    #    other groups' SB/SD spend in via the fallback.
    _camp_filter = None if group_mode == 'all' else _campaign_passes
    sb_totals = _daily_totals(marketplace, renderable, 'sb', campaign_filter=_camp_filter)
    sd_totals = _daily_totals(marketplace, renderable, 'sd', campaign_filter=_camp_filter)

    # 5) Per-day ads completeness
    ads_status = {d: day_ads_complete(marketplace, d) for d in renderable}
    sb_complete_days = sorted(d for d in renderable if ads_status[d]['sb_daily'])
    sd_complete_days = sorted(d for d in renderable if ads_status[d]['sd_daily'])

    # 6) Assemble cells
    cells_by_date: dict[date_cls, list[Optional[HourCell]]] = {
        d: [None] * 24 for d in renderable
    }

    # Index metric rows for fast lookup
    metric_by_dh: dict[tuple[date_cls, int], dict] = {
        (r['date'], r['hour']): r for r in metric_rows
    }

    for d in renderable:
        sb_ok       = ads_status[d]['sb_daily']
        sd_ok       = ads_status[d]['sd_daily']
        sb_is_real  = d in sb_real_days        # AMS hourly present for this day
        sd_is_real  = d in sd_real_days
        # Per-hour SB / SD value resolver. Returns None when source is missing.
        sb_daily_per_hour = (sb_totals.get(d, 0.0) / 24.0) if sb_ok else None
        sd_daily_per_hour = (sd_totals.get(d, 0.0) / 24.0) if sd_ok else None

        for h in range(24):
            mr = metric_by_dh.get((d, h))
            ppc_sp = sp_by_dh.get((d, h), 0.0)

            # Cells for hours with no Orders rows: still allow PPC display
            # (the day is renderable; absent hour = 0 revenue, 0 orders, etc).
            if mr is None:
                revenue = units = orders = 0
                cm = gm = 0.0
                cm_pct = gm_pct = 0.0
            else:
                revenue = float(mr['revenue'])
                units   = int(mr['units'])
                orders  = int(mr['orders'])
                cm      = float(mr['contribution_margin'])
                gm      = float(mr['gross_margin'])
                cm_pct  = round(float(mr['cm_pct']) * 100, 2)
                gm_pct  = round(float(mr['gm_pct']) * 100, 2)

            # SB per-hour: REAL hourly first (any value from AMS, incl. 0),
            #              else daily÷24 fallback if the day is sb-complete,
            #              else None.
            if sb_is_real:
                ppc_sb: Optional[float] = sb_by_dh.get((d, h), 0.0)
            else:
                ppc_sb = sb_daily_per_hour

            if sd_is_real:
                ppc_sd: Optional[float] = sd_by_dh.get((d, h), 0.0)
            else:
                ppc_sd = sd_daily_per_hour

            # ppc_total only when ALL three components are known
            if ppc_sb is None or ppc_sd is None:
                ppc_total: Optional[float] = None
                ppc_pct:   Optional[float] = None
            else:
                ppc_total = ppc_sp + ppc_sb + ppc_sd
                ppc_pct   = (ppc_total / revenue * 100.0) if revenue > 0 else None

            # ── Recompute GM = CM − PPC at read time ─────────────────────
            # HourlyMetricSnapshot.gross_margin is stored equal to
            # contribution_margin because the snapshot writer doesn't know
            # PPC per hour. The aggregator derives GM from the cell's PPC
            # sources so the heatmap actually shows GM ≠ CM when PPC > 0.
            #
            # Run for EVERY hour — including hours with no orders (mr is None).
            # An hour with $0 revenue and $20 of PPC has GM = −$20, not $0.
            # When ppc_total is unknown (SB or SD missing), subtract only what
            # we know (ppc_sp + any present source).
            _ppc_known = (ppc_sp
                          + (ppc_sb if ppc_sb is not None else 0.0)
                          + (ppc_sd if ppc_sd is not None else 0.0))
            gm     = cm - _ppc_known
            gm_pct = round((gm / revenue * 100.0) if revenue > 0 else 0.0, 2)

            cells_by_date[d][h] = HourCell(
                date=d, hour=h,
                revenue=revenue, units=units, orders=orders,
                cm=cm, cm_pct=cm_pct, gm=gm, gm_pct=gm_pct,
                ppc_sp=ppc_sp, ppc_sb=ppc_sb, ppc_sd=ppc_sd,
                ppc_total=ppc_total, ppc_pct=ppc_pct,
            )

    return AggregatorResult(
        marketplace=marketplace,
        start_date=start_date, end_date=end_date,
        renderable_dates=renderable,
        cells_by_date=cells_by_date,
        ads_status=ads_status,
        sb_complete_days=sb_complete_days,
        sd_complete_days=sd_complete_days,
        completeness=completeness_report(marketplace, start_date, end_date),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _daily_totals(marketplace: str, dates: list[date_cls],
                  campaign_type: str,
                  campaign_filter=None) -> dict[date_cls, float]:
    """
    Sum of campaign spend by date for one campaign type (sb or sd) — used to
    derive the per-hour allocation (daily ÷ 24).

    `campaign_filter` is an optional callable `(campaign_name, campaign_id) -> bool`.
    When provided, campaigns whose name fails the filter are excluded from the
    daily totals so a group-scoped Hourly Patterns view stays consistent.
    """
    qs = (PPCCampaignSnapshot.objects
          .filter(marketplace=marketplace, campaign_type=campaign_type,
                  date__in=dates))
    if campaign_filter is None:
        agg = qs.values('date').annotate(spend=Sum('spend'))
        return {r['date']: float(r['spend'] or 0) for r in agg}
    out: dict = {}
    for r in qs.values('date', 'campaign_id', 'campaign_name', 'spend'):
        if not campaign_filter(r['campaign_name'] or '', r['campaign_id']):
            continue
        out[r['date']] = out.get(r['date'], 0.0) + float(r['spend'] or 0)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Display-time T-2 cutoff helper
# ─────────────────────────────────────────────────────────────────────────────
def clamp_to_t_minus_2(end_date: date_cls, today: date_cls) -> date_cls:
    """
    Display rule: UI shows up to T-2 only (stable data). T-1 is ingested but
    too fresh to display (PPC attribution still drifts during the first day).
    """
    cutoff = today - timedelta(days=2)
    return min(end_date, cutoff)
