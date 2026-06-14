"""
apps/dashboard/ppc_allocator.py — SKU-level PPC allocation engine.

Implements the spec (§1–§10): 2-pass allocation, reconciliation, EMA smoothing,
T+3 lock. Outputs are persisted in `SkuPpcAllocation`.

Pure-Python engine — no boto3, no Django request cycle. Callable from the
management command, cron, and tests. The only Django dependency is the ORM.

Conventions:
    All weights are normalized to [0, 1] within their parent set.
    Currency math uses Decimal where it touches `spend`; intermediate signals
    use float for speed (precision recovered at reconciliation).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# §0 — Constants
# ─────────────────────────────────────────────────────────────────────────────
EPS         = 1e-9                  # divide-by-zero floor for floats
EPS_DOLLAR  = Decimal('0.01')       # reconciliation tolerance
LOCK_AFTER_DAYS = 3                 # T+3 lock per §5

PASS1_WEIGHTS = {'t7': 0.7, 't30': 0.3}        # §1.2 SB/SD blend
PASS2_WEIGHTS = {'t7': 0.65, 't30': 0.25, 'price': 0.10}   # §2.1 blend
EMA_ALPHA = 0.7                                 # §5 — today's weight in EMA

# SKUs starting with any of these prefixes are excluded from PPC allocation
# entirely. Used for Amazon Renewed / Warehouse listings (`AMZN.*`) which we
# don't pay PPC against — their share of any ASIN's spend gets re-routed to
# the normal sibling SKU(s).
EXCLUDED_SKU_PREFIXES: tuple[str, ...] = ('AMZN.',)


def is_excluded_sku(sku: str) -> bool:
    s = (sku or '').upper()
    return any(s.startswith(p) for p in EXCLUDED_SKU_PREFIXES)

# Confidence lookup per §7
CONF_PER_SOURCE = {
    'sp_advertised_product': 1.00,
    'sb_revenue_share':      0.80,
    'sd_revenue_share':      0.80,
    'group_revenue_share':   0.65,
    'sp_provisional':        0.50,
    'cold_start_equal':      0.30,
    'cold_start_catalog':    0.40,
    'reconciled':            0.00,    # penalty subtracted
    'unallocated':           0.00,
}


# ─────────────────────────────────────────────────────────────────────────────
# Data holders
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _Signals:
    rev_t7_sku:  dict[str, float]              = field(default_factory=dict)
    rev_t30_sku: dict[str, float]              = field(default_factory=dict)
    rev_t7_asin: dict[str, float]              = field(default_factory=dict)
    rev_t30_asin: dict[str, float]             = field(default_factory=dict)
    price:       dict[str, float]              = field(default_factory=dict)
    skus_of_asin: dict[str, list[str]]         = field(default_factory=dict)
    asin_of_sku:  dict[str, str]               = field(default_factory=dict)
    group_of_camp: dict[str, tuple] = field(default_factory=dict)
    asins_of_group: dict[tuple, list[str]] = field(default_factory=dict)


@dataclass
class _CampaignSpend:
    campaign_id:   str
    campaign_type: str           # 'sp' | 'sb' | 'sd'
    name:          str
    spend:         Decimal


@dataclass
class _AllocRow:
    """One persisted SkuPpcAllocation row, pre-write."""
    marketplace:        str
    date:               date_cls
    sku:                str
    asin:               str
    campaign_id:        str
    campaign_type:      str
    campaign_spend:     Decimal
    asin_weight:        float
    sku_weight:         float
    sku_ppc_spend:      Decimal
    rev_t7_sku:         float
    rev_t30_sku:        float
    rev_t7_asin:        float
    rev_t30_asin:       float
    attribution_source: str
    confidence_score:   float
    settlement_state:   str


# ─────────────────────────────────────────────────────────────────────────────
# §9 — Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def compute_for_day(marketplace: str, d: date_cls, *,
                    dry_run: bool = False) -> dict:
    """
    Run Steps 1–8 for one (marketplace, date).

    Returns a summary dict for logging.
    """
    today = date_cls.today()
    is_locked = (today - d).days >= LOCK_AFTER_DAYS
    state_target = 'locked' if is_locked else ('settling' if d < today else 'provisional')

    # STEP 1 — inputs
    signals       = _load_signals(marketplace, d)
    campaigns     = _load_campaign_spend(marketplace, d)
    sp_asin_spend = _load_sp_asin_spend(marketplace, d)

    # STEP 2-4 — Pass 1 + Pass 2 + multiply, per campaign
    raw_rows: list[_AllocRow] = []
    for c in campaigns:
        if c.spend <= 0:
            continue
        rows = _allocate_one_campaign(
            marketplace, d, c, signals, sp_asin_spend,
            settlement_state=state_target,
        )
        raw_rows.extend(rows)

    # STEP 5 — EMA smoothing on provisional / settling rows (run first so
    #          reconciliation can fix any drift the EMA introduces)
    rows = _ema_smooth(raw_rows, marketplace, d, state_target)

    # STEP 6 — reconcile per (date, campaign): Σ SKU spend = Campaign spend
    rows = _reconcile_per_campaign(rows, campaigns)

    # STEP 7 — persist
    if not dry_run:
        n_written = _persist(rows, marketplace, d, state_target)
    else:
        n_written = 0

    return {
        'date': d.isoformat(),
        'campaigns_with_spend': sum(1 for c in campaigns if c.spend > 0),
        'rows_computed': len(rows),
        'rows_written':  n_written,
        'state_target':  state_target,
        'sum_alloc':     float(sum(r.sku_ppc_spend for r in rows)),
        'sum_campaign':  float(sum(c.spend for c in campaigns if c.spend > 0)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Input loaders
# ─────────────────────────────────────────────────────────────────────────────
def _load_signals(mp: str, d: date_cls) -> _Signals:
    """
    Build the cached signal dicts for one day. Reads DailySkuSnapshot for
    revenue history; Product catalog for ASIN↔SKU mapping and price.

    Product group `(product_type, pack_size)` is derived the same way the
    existing dashboard does it: split `Product.title` on ' - '.
    """
    from .models import DailySkuSnapshot, Product

    sig = _Signals()

    def _group_from_title(title: str) -> tuple | None:
        parts = [p.strip() for p in (title or '').split(' - ') if p.strip()]
        if len(parts) >= 2:
            return (parts[0], parts[1])
        return None

    # SKU/ASIN map + catalog price + per-group ASIN lookup.
    # Excluded SKUs (AMZN.*) are skipped entirely so their share of any ASIN's
    # PPC spend redistributes to their normal sibling SKU(s) automatically via
    # Pass 2 normalization. An ASIN that ONLY has excluded SKUs ends up with
    # no entries in sig.skus_of_asin → Pass 1 also skips it (see _pass1_*).
    prods = list(
        Product.objects
        .filter(marketplace=mp, status='active')
        .values('asin', 'sku', 'list_price', 'sale_price', 'title')
    )
    for p in prods:
        asin = (p['asin'] or '').upper()
        sku  = (p['sku']  or '').upper()
        if not asin or not sku:
            continue
        if is_excluded_sku(sku):
            continue       # AMZN.* — see EXCLUDED_SKU_PREFIXES
        sig.asin_of_sku[sku] = asin
        sig.skus_of_asin.setdefault(asin, []).append(sku)
        sig.price[sku] = float(p['sale_price'] or p['list_price'] or 0)
        grp = _group_from_title(p['title'])
        if grp:
            grp_asins = sig.asins_of_group.setdefault(grp, [])
            if asin not in grp_asins:
                grp_asins.append(asin)

    # Revenue T7 / T30 per SKU
    d7_start  = d - timedelta(days=7)
    d30_start = d - timedelta(days=30)

    qs7 = (DailySkuSnapshot.objects
           .filter(marketplace=mp, date__gte=d7_start, date__lt=d)
           .values('sku').annotate(rev=Sum('revenue')))
    for r in qs7:
        sig.rev_t7_sku[(r['sku'] or '').upper()] = float(r['rev'] or 0)

    qs30 = (DailySkuSnapshot.objects
            .filter(marketplace=mp, date__gte=d30_start, date__lt=d)
            .values('sku').annotate(rev=Sum('revenue')))
    for r in qs30:
        sig.rev_t30_sku[(r['sku'] or '').upper()] = float(r['rev'] or 0)

    # Roll up ASIN revenue from SKU revenue (asin is the parent product)
    for sku, asin in sig.asin_of_sku.items():
        sig.rev_t7_asin[asin]  = sig.rev_t7_asin.get(asin, 0.0)  + sig.rev_t7_sku.get(sku, 0.0)
        sig.rev_t30_asin[asin] = sig.rev_t30_asin.get(asin, 0.0) + sig.rev_t30_sku.get(sku, 0.0)

    return sig


def _load_campaign_spend(mp: str, d: date_cls) -> list[_CampaignSpend]:
    """
    Hybrid spend source per (date, campaign):

      • Past days (d < today):
          Use PPCCampaignSnapshot (daily, from backfill_ppc) ALWAYS when
          present. It's the same number Amazon's Ads UI shows for that day —
          settled, validated, attribution-finalized. AMS late-arriving events
          (revisions, new attributions) would inflate the total above what
          Amazon reports, so we don't sum them on top.
          Fall back to AMS only when there's no daily snapshot at all.

      • Today (d == today):
          backfill_ppc hasn't run for today yet, so daily is partial / stale.
          Pick the LARGER of AMS vs whatever daily exists — gives us the most
          complete real-time picture of the in-progress day.

    AMS events don't include `campaign_name` — only the ID. Names get enriched
    from any historical PPCCampaignSnapshot row so the campaign-name prefix
    matcher can do its job.
    """
    from .models import PPCCampaignHourlySnapshot, PPCCampaignSnapshot

    is_today = (d >= date_cls.today())

    # ── PRIORITY 0 ────────────────────────────────────────────────────────
    # If the user uploaded a Seller Central hourly CSV for this date, treat
    # that as the authoritative truth and IGNORE everything else (AMS rows
    # for the same date AND the Ads-API daily snapshot). Manual upload is
    # whole-day data the user just downloaded from Amazon's UI; mixing it
    # with the other sources double-counts because manual rows are keyed by
    # slugified campaign names while daily snapshots use Amazon's numeric IDs
    # (zero overlap, all rows survive both filters → 2× total).
    manual_present = (
        PPCCampaignHourlySnapshot.objects
        .filter(marketplace=mp, date=d, source='manual').exists()
    )
    if manual_present and not is_today:
        out_by_id: dict[tuple, _CampaignSpend] = {}
        for r in (PPCCampaignHourlySnapshot.objects
                  .filter(marketplace=mp, date=d, source='manual')
                  .values('campaign_id', 'campaign_name', 'campaign_type')
                  .annotate(spend=Sum('spend'))):
            k = (str(r['campaign_id']), r['campaign_type'])
            out_by_id[k] = _CampaignSpend(
                campaign_id   = k[0],
                campaign_type = k[1],
                name          = r['campaign_name'] or '',
                spend         = Decimal(str(r['spend'] or 0)),
            )
        return list(out_by_id.values())

    # ── Otherwise: hybrid AMS + Daily snapshot ─────────────────────────────
    # Hourly (AMS) per (campaign_id, type) — excludes manual rows (already
    # handled by priority 0 above; for is_today we still let manual contribute
    # because the same day could be mid-stream).
    hourly_by_id: dict[tuple, dict] = {}
    for r in (PPCCampaignHourlySnapshot.objects
              .filter(marketplace=mp, date=d)
              .values('campaign_id', 'campaign_name', 'campaign_type')
              .annotate(spend=Sum('spend'))):
        k = (str(r['campaign_id']), r['campaign_type'])
        hourly_by_id[k] = {
            'name':  r['campaign_name'] or '',
            'spend': Decimal(str(r['spend'] or 0)),
        }

    # Daily per (campaign_id, type)
    daily_by_id: dict[tuple, dict] = {}
    for r in (PPCCampaignSnapshot.objects
              .filter(marketplace=mp, date=d)
              .values('campaign_id', 'campaign_name', 'campaign_type', 'spend')):
        k = (str(r['campaign_id']), r['campaign_type'])
        daily_by_id[k] = {
            'name':  r['campaign_name'] or '',
            'spend': Decimal(str(r['spend'] or 0)),
        }

    # Pick per (campaign, type) following the rules above.
    by_id: dict[tuple, _CampaignSpend] = {}
    for k in set(hourly_by_id) | set(daily_by_id):
        h = hourly_by_id.get(k, {'name': '', 'spend': Decimal('0')})
        e = daily_by_id.get(k,  {'name': '', 'spend': Decimal('0')})
        if is_today:
            # Real-time path: take the larger (most complete) value
            chosen = h if h['spend'] >= e['spend'] else e
        else:
            # Settled day: daily snapshot is authoritative when present;
            # AMS is only used to fill campaigns daily missed entirely.
            chosen = e if e['spend'] > 0 else h
        name = h['name'] or e['name']
        by_id[k] = _CampaignSpend(
            campaign_id   = k[0],
            campaign_type = k[1],
            name          = name,
            spend         = chosen['spend'],
        )

    # 4) Enrich missing campaign_name from any historical daily snapshot.
    missing_ids = [k[0] for k, v in by_id.items() if not v.name]
    if missing_ids:
        name_lookup: dict[str, str] = {}
        for r in (PPCCampaignSnapshot.objects
                  .filter(marketplace=mp, campaign_id__in=missing_ids)
                  .exclude(campaign_name='')
                  .order_by('-date')
                  .values('campaign_id', 'campaign_name')):
            cid = str(r['campaign_id'])
            if cid not in name_lookup:
                name_lookup[cid] = r['campaign_name']
        for k, v in by_id.items():
            if not v.name and v.campaign_id in name_lookup:
                v.name = name_lookup[v.campaign_id]

    return list(by_id.values())


def _load_sp_asin_spend(mp: str, d: date_cls) -> dict[tuple, float]:
    """
    `PPCProductSnapshot` per (campaign_id, asin) → spend on this date.
    Used by §1.1 (SP authoritative path).
    """
    from .models import PPCProductSnapshot
    out: dict[tuple, float] = {}
    for r in (PPCProductSnapshot.objects
              .filter(marketplace=mp, date=d, campaign_type='sp')
              .values('asin').annotate(spend=Sum('spend'))):
        out[(r['asin'] or '').upper()] = float(r['spend'] or 0)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2-4 — Pass 1 + Pass 2 + multiply, per campaign
# ─────────────────────────────────────────────────────────────────────────────
def _allocate_one_campaign(
    mp: str, d: date_cls,
    c: _CampaignSpend, sig: _Signals,
    sp_asin_spend: dict[tuple, float],
    *, settlement_state: str,
) -> list[_AllocRow]:
    """Run Pass 1 + Pass 2 for one campaign, return per-SKU rows."""
    from apps.amazon_api.views import _match_campaign_to_group

    asin_weights, source = _pass1(mp, d, c, sig, sp_asin_spend)
    if not asin_weights:
        return []   # unallocated

    rows: list[_AllocRow] = []
    for asin, asin_w in asin_weights.items():
        if asin_w <= 0:
            continue
        sku_weights, sku_source = _pass2(asin, sig, default_source=source)
        for sku, sku_w in sku_weights.items():
            if sku_w <= 0:
                continue
            spend = (c.spend
                     * Decimal(str(asin_w)).quantize(Decimal('0.000001'))
                     * Decimal(str(sku_w)).quantize(Decimal('0.000001')))
            final_source = sku_source if sku_source != source else source
            rows.append(_AllocRow(
                marketplace=mp, date=d, sku=sku, asin=asin,
                campaign_id=c.campaign_id, campaign_type=c.campaign_type,
                campaign_spend=c.spend,
                asin_weight=asin_w, sku_weight=sku_w,
                sku_ppc_spend=spend,
                rev_t7_sku=sig.rev_t7_sku.get(sku, 0.0),
                rev_t30_sku=sig.rev_t30_sku.get(sku, 0.0),
                rev_t7_asin=sig.rev_t7_asin.get(asin, 0.0),
                rev_t30_asin=sig.rev_t30_asin.get(asin, 0.0),
                attribution_source=final_source,
                confidence_score=_confidence(final_source, sig, sku, asin),
                settlement_state=settlement_state,
            ))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# §1 — PASS 1: Campaign → ASIN
# ─────────────────────────────────────────────────────────────────────────────
def _pass1(mp, d, c: _CampaignSpend, sig: _Signals,
           sp_asin_spend: dict[tuple, float]) -> tuple[dict[str, float], str]:
    """Returns (asin_weight_dict, attribution_source)."""
    from apps.amazon_api.views import _match_campaign_to_group

    if c.campaign_type == 'sp':
        weights, src = _pass1_sp(mp, d, c, sp_asin_spend, sig)
        if weights:
            return weights, src
        # §4.4 third-tier fallback — when neither authoritative
        # spAdvertisedProduct data nor a prior day's provisional weights
        # exist, fall through to the SB/SD-style group-revenue mix so we
        # still produce usable allocations.

    # SB / SD path (also SP fallback) — campaign name → mapped product group
    # → 0.7 × T7 + 0.3 × T30 revenue mix over the group's ASINs.
    group = _match_campaign_to_group(c.name)
    if group:
        asins = _asins_of_group(group, mp, sig)
        if asins:
            return _pass1_sbsd(asins, sig)
    return {}, 'unallocated'


def _pass1_sp(mp, d, c, sp_asin_spend, sig: _Signals) -> tuple[dict[str, float], str]:
    """§1.1 — authoritative when spAdvertisedProduct exists, provisional otherwise."""
    # We loaded sp_asin_spend as {asin: spend} for ALL SP. To get per-campaign,
    # we re-query (cheap; few campaigns).
    from .models import PPCProductSnapshot
    qs = (PPCProductSnapshot.objects
          .filter(marketplace=mp, date=d, campaign_type='sp')
          .values('asin').annotate(spend=Sum('spend')))
    # This is total per ASIN across all SP campaigns — to get ASIN_weight for
    # ONE campaign we'd need per-campaign per-ASIN. PPCProductSnapshot in this
    # codebase aggregates across campaigns; per-campaign per-ASIN isn't stored.
    # → Use day-level proportions as the best available approximation, which
    # equals the authoritative split for single-campaign single-ASIN cases and
    # is the closest defensible weighting otherwise.
    asin_totals = {(r['asin'] or '').upper(): float(r['spend'] or 0) for r in qs}
    # Drop ASINs whose only SKUs are excluded (AMZN.*). Their spend should
    # never reach a SKU row, so we keep them out of the weight basis entirely.
    asin_totals = {a: v for a, v in asin_totals.items()
                   if a in sig.skus_of_asin}
    total = sum(asin_totals.values())
    if total <= EPS:
        # Provisional path — §4.4: carry yesterday's weights
        return _pass1_sp_provisional(mp, d, c.campaign_id, sig)
    weights = {a: v / total for a, v in asin_totals.items() if v > 0}
    return weights, 'sp_advertised_product'


def _pass1_sp_provisional(mp, d, campaign_id, sig: _Signals) -> tuple[dict[str, float], str]:
    """§4.4 — carry yesterday's ASIN weights when today's data hasn't arrived."""
    from .models import SkuPpcAllocation
    prev = (SkuPpcAllocation.objects
            .filter(marketplace=mp, date=d - timedelta(days=1),
                    campaign_id=campaign_id, campaign_type='sp')
            .values('asin').annotate(w=Sum('asin_weight')))
    weights = {(r['asin'] or '').upper(): float(r['w'] or 0) for r in prev if r['w']}
    total = sum(weights.values())
    if total <= EPS:
        return {}, 'unallocated'
    return ({a: w / total for a, w in weights.items()}, 'sp_provisional')


def _pass1_sbsd(asins: list[str], sig: _Signals) -> tuple[dict[str, float], str]:
    """§1.2 — 0.7×T7 + 0.3×T30 revenue mix over the target ASIN set."""
    if not asins:
        return {}, 'unallocated'

    t7_total  = sum(sig.rev_t7_asin.get(a, 0.0)  for a in asins)
    t30_total = sum(sig.rev_t30_asin.get(a, 0.0) for a in asins)

    # §4.2 — cold-start equal split if no history
    if t7_total <= EPS and t30_total <= EPS:
        n = len(asins)
        return ({a: 1.0 / n for a in asins}, 'cold_start_equal')

    den7  = max(EPS, t7_total)
    den30 = max(EPS, t30_total)

    out = {}
    for a in asins:
        out[a] = (
            PASS1_WEIGHTS['t7']  * sig.rev_t7_asin.get(a,  0.0) / den7  +
            PASS1_WEIGHTS['t30'] * sig.rev_t30_asin.get(a, 0.0) / den30
        )
    # Normalize (cleanup floating-point drift)
    s = sum(out.values())
    if s <= EPS:
        n = len(asins)
        return ({a: 1.0 / n for a in asins}, 'cold_start_equal')
    return ({a: v / s for a, v in out.items()}, 'group_revenue_share')


def _asins_of_group(group: tuple, mp: str, sig: _Signals) -> list[str]:
    """All active ASINs in the mapped product group — pre-computed in _load_signals."""
    return sig.asins_of_group.get(group, [])


# ─────────────────────────────────────────────────────────────────────────────
# §2 — PASS 2: ASIN → SKU
# ─────────────────────────────────────────────────────────────────────────────
def _pass2(asin: str, sig: _Signals,
           default_source: str) -> tuple[dict[str, float], str]:
    skus = sig.skus_of_asin.get(asin, [])
    if not skus:
        # Edge: ASIN has no mapped SKUs — single-SKU fallback (the ASIN
        # itself acts as the SKU)
        return ({asin: 1.0}, default_source)

    den_t7  = max(EPS, sum(sig.rev_t7_sku.get(s, 0.0)  for s in skus))
    den_t30 = max(EPS, sum(sig.rev_t30_sku.get(s, 0.0) for s in skus))
    den_pr  = max(EPS, sum(sig.price.get(s, 0.0)       for s in skus))

    out = {}
    any_history = False
    for s in skus:
        r7  = sig.rev_t7_sku.get(s, 0.0)
        r30 = sig.rev_t30_sku.get(s, 0.0)
        pr  = sig.price.get(s, 0.0)
        if r7 > 0 or r30 > 0:
            any_history = True
            raw = (PASS2_WEIGHTS['t7']    * (r7  / den_t7)
                 + PASS2_WEIGHTS['t30']   * (r30 / den_t30)
                 + PASS2_WEIGHTS['price'] * (pr  / den_pr))
        else:
            # §4.1 cold-start SKU — catalog only
            raw = PASS2_WEIGHTS['price'] * (pr / den_pr)
        out[s] = raw

    total = sum(out.values())
    if total <= EPS:
        # Catalog floor only — distribute by price among siblings
        if den_pr > EPS:
            return ({sku: sig.price.get(sku, 0.0) / den_pr for sku in skus},
                    'cold_start_catalog')
        # No prices either — true equal split
        n = len(skus)
        return ({sku: 1.0 / n for sku in skus}, 'cold_start_equal')

    normed = {sku: out[sku] / total for sku in out}
    src = default_source if any_history else 'cold_start_catalog'
    return normed, src


# ─────────────────────────────────────────────────────────────────────────────
# §4.6 — Reconciliation
# ─────────────────────────────────────────────────────────────────────────────
def _reconcile_per_campaign(rows: list[_AllocRow],
                            campaigns: list[_CampaignSpend]) -> list[_AllocRow]:
    spend_by_camp = {c.campaign_id: c.spend for c in campaigns}
    by_camp: dict[str, list[_AllocRow]] = defaultdict(list)
    for r in rows:
        by_camp[r.campaign_id].append(r)

    out: list[_AllocRow] = []
    for cid, camp_rows in by_camp.items():
        target = spend_by_camp.get(cid, Decimal('0'))
        recon  = sum((r.sku_ppc_spend for r in camp_rows), Decimal('0'))
        diff   = target - recon
        if abs(diff) <= EPS_DOLLAR:
            out.extend(camp_rows)
            continue
        if recon <= 0:
            out.extend(camp_rows)
            continue
        # Distribute residual proportionally
        scale = target / recon
        for r in camp_rows:
            r.sku_ppc_spend = (r.sku_ppc_spend * scale).quantize(Decimal('0.0001'))
            if abs(diff) > EPS_DOLLAR * 10:    # only flag non-trivial residual
                r.attribution_source = 'reconciled'
                r.confidence_score   = max(0.0, r.confidence_score - 0.10)
        out.extend(camp_rows)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §5 — EMA smoothing
# ─────────────────────────────────────────────────────────────────────────────
def _ema_smooth(rows: list[_AllocRow], mp: str, d: date_cls,
                state_target: str) -> list[_AllocRow]:
    """Blend today's value with yesterday's published value for unlocked days."""
    if state_target == 'locked':
        return rows

    from .models import SkuPpcAllocation
    prev_map = {}
    prev = SkuPpcAllocation.objects.filter(
        marketplace=mp, date=d).values(
        'sku', 'asin', 'campaign_id', 'sku_ppc_spend')
    # Note: previous SAVE of today (re-running same day) — keep the previously
    # published value as the "yesterday" anchor.
    for p in prev:
        prev_map[(p['sku'], p['asin'], p['campaign_id'])] = Decimal(str(p['sku_ppc_spend']))

    if not prev_map:
        return rows

    for r in rows:
        prior = prev_map.get((r.sku, r.asin, r.campaign_id))
        if prior is None:
            continue
        r.sku_ppc_spend = (Decimal(str(EMA_ALPHA)) * r.sku_ppc_spend +
                           Decimal(str(1 - EMA_ALPHA)) * prior
                           ).quantize(Decimal('0.0001'))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# §8 — Confidence
# ─────────────────────────────────────────────────────────────────────────────
def _confidence(source: str, sig: _Signals, sku: str, asin: str) -> float:
    p1 = CONF_PER_SOURCE.get(source, 0.5)
    # §8 Pass-2 confidence — how strong this SKU's history is vs siblings
    siblings = sig.skus_of_asin.get(asin, [sku])
    rev = sig.rev_t7_sku.get(sku, 0.0) + sig.rev_t30_sku.get(sku, 0.0) / 3
    mean_sib = sum(
        sig.rev_t7_sku.get(s, 0.0) + sig.rev_t30_sku.get(s, 0.0) / 3
        for s in siblings) / max(1, len(siblings))
    p2 = min(1.0, rev / max(EPS, mean_sib))
    # §8 — reconciliation residual is zero before §4.6 has run; we credit
    # the 0.1 term at full strength here. The reconciliation step later
    # debits 0.10 on any rows it touched (handled in _reconcile_per_campaign).
    p3 = 1.0
    final = 0.6 * p1 + 0.3 * p2 + 0.1 * p3
    return round(max(0.0, min(1.0, final)), 2)


# ─────────────────────────────────────────────────────────────────────────────
# §6 — Persist
# ─────────────────────────────────────────────────────────────────────────────
def _persist(rows: list[_AllocRow], mp: str, d: date_cls,
             state_target: str) -> int:
    from .models import SkuPpcAllocation
    if not rows:
        # Wipe any prior rows so stale data doesn't linger
        SkuPpcAllocation.objects.filter(marketplace=mp, date=d).delete()
        return 0

    now = timezone.now()
    objs = [
        SkuPpcAllocation(
            marketplace=r.marketplace, date=r.date,
            sku=r.sku, asin=r.asin,
            campaign_id=r.campaign_id, campaign_type=r.campaign_type,
            campaign_spend=r.campaign_spend,
            asin_weight=Decimal(str(r.asin_weight)).quantize(Decimal('0.000001')),
            sku_weight=Decimal(str(r.sku_weight)).quantize(Decimal('0.000001')),
            sku_ppc_spend=r.sku_ppc_spend,
            revenue_t7_sku=Decimal(str(r.rev_t7_sku)).quantize(Decimal('0.01')),
            revenue_t30_sku=Decimal(str(r.rev_t30_sku)).quantize(Decimal('0.01')),
            revenue_t7_asin=Decimal(str(r.rev_t7_asin)).quantize(Decimal('0.01')),
            revenue_t30_asin=Decimal(str(r.rev_t30_asin)).quantize(Decimal('0.01')),
            attribution_source=r.attribution_source,
            confidence_score=Decimal(str(r.confidence_score)).quantize(Decimal('0.01')),
            settlement_state=state_target,
            locked_at=(now if state_target == 'locked' else None),
        )
        for r in rows
    ]

    with transaction.atomic():
        # Replace existing rows for this day to ensure idempotency
        SkuPpcAllocation.objects.filter(marketplace=mp, date=d).delete()
        SkuPpcAllocation.objects.bulk_create(objs, batch_size=1000)

    return len(objs)
