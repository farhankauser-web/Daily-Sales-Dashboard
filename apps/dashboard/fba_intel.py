"""
apps/dashboard/fba_intel.py — FBA Fee Intelligence (Phase A).

THE QUESTION THIS ANSWERS
    What is my FBA fee per unit, has it moved, how much did that movement cost
    or save in real money, which SKUs are responsible, and did the movement
    coincide with inventory cover falling?

WHAT IT DELIBERATELY DOES NOT ANSWER
    WHY Amazon charged it. Settlement data carries a single aggregate line
    (`FBAPerUnitFulfillmentFee`) with no base / fuel / low-inventory / SIPP /
    dimensional split — verified against a real UK settlement file. Any
    component attribution here would be invented, so it is not attempted.
    That is Phase B, and it needs SKU Economics.

SOURCES (all existing; nothing new is written)
    SkuFeeActual      fee_per_unit, fba_fee_total, units  — per SKU per day.
                      `units` are BILLED units: what Amazon actually charged.
    DailySkuSnapshot  qty — units SOLD. Context only; never drives cost maths.
    InventorySnapshot afn_fulfillable, days_cover — via Product FK.

COMPARISON METHODOLOGY (documented because it drives every money figure)
    A naive "fee today vs fee 30 days ago" compares two arbitrary days and is
    hostage to which SKUs happened to sell on each. Instead both periods use a
    UNITS-WEIGHTED average fee:

        weighted_fee = Σ(fba_fee_total) / Σ(units)

    over the window. That is the fee actually borne per unit across the whole
    period, and it is the same figure Amazon's own settlement arithmetic
    produces. Current window = [end-N+1 … end]; previous = the immediately
    preceding window of identical length, so seasonality is compared like for
    like.

        fee_delta   = current_weighted_fee − previous_weighted_fee
        impact      = fee_delta × current_billed_units

    impact > 0 → incremental COST; impact < 0 → SAVING. Billed units are used
    per the business rule: this measures Amazon's fee impact, not sales.

PHASE B COMPATIBILITY
    Every figure here derives from `_weighted(rows)`. When SKU Economics lands,
    component rows can be aggregated through the same shape and attached
    alongside — the page contract (position / drift / SKU rows) does not change.
    No empty component fields are created in anticipation.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

# A SKU needs at least this many billed units in a window before its weighted
# fee is treated as meaningful; below it, one odd unit swings the average.
MIN_UNITS_FOR_SIGNAL = 5
# Movement smaller than this per unit is rounding, not drift.
MATERIAL_FEE_DELTA = 0.01
# Correlation strength above which fee-vs-cover co-movement is called out.
CORRELATION_THRESHOLD = 0.5


@dataclass
class Period:
    start: date
    end: date
    days: int

    @property
    def label(self) -> str:
        return f'{self.start.isoformat()} → {self.end.isoformat()}'


def resolve_periods(days: int, anchor: date) -> tuple[Period, Period]:
    """Current window ending at `anchor`, plus the preceding equal window."""
    cur = Period(anchor - timedelta(days=days - 1), anchor, days)
    prev_end = cur.start - timedelta(days=1)
    return cur, Period(prev_end - timedelta(days=days - 1), prev_end, days)


def latest_data_date(marketplace: str) -> date | None:
    """Newest settlement day we hold. Windows anchor here, not to the calendar:
    settlement arrives in arrears, so anchoring to today yields empty windows."""
    from .models import SkuFeeActual
    qs = SkuFeeActual.objects.all()
    if marketplace:
        qs = qs.filter(marketplace=marketplace)
    return qs.order_by('-date').values_list('date', flat=True).first()


def _weighted(fee_total: float, units: int) -> float | None:
    """Units-weighted fee per unit. None when nothing was billed — that is
    'no data', which must never be rendered as $0.00."""
    return (fee_total / units) if units > 0 else None


@dataclass
class SkuRow:
    sku: str = ''
    asin: str = ''
    title: str = ''
    category: str = ''
    marketplace: str = ''
    current_fee: float | None = None
    previous_fee: float | None = None
    fee_delta: float | None = None
    fee_delta_pct: float | None = None
    drift_7d: float | None = None
    drift_14d: float | None = None
    drift_30d: float | None = None
    billed_units: int = 0
    units_sold: int = 0
    incremental_cost: float = 0.0
    savings: float = 0.0
    net_impact: float = 0.0
    current_inventory: int | None = None
    current_days_cover: float | None = None
    inventory_signal: str = 'insufficient_data'
    correlation: float | None = None
    data_days: int = 0
    series: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ('current_fee', 'previous_fee', 'fee_delta', 'drift_7d',
                  'drift_14d', 'drift_30d', 'current_days_cover', 'correlation',
                  'fee_delta_pct'):
            if d[k] is not None:
                d[k] = round(d[k], 4)
        for k in ('incremental_cost', 'savings', 'net_impact'):
            d[k] = round(d[k], 2)
        return d


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Correlation coefficient. Returns None when it would be meaningless
    (fewer than 3 paired points, or either series is flat)."""
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _inventory_signal(correlation: float | None, fee_delta: float | None,
                      points: int) -> str:
    """Correlation, stated as correlation — never as causation.

    A negative coefficient means fee rose as days-of-cover fell. That is
    co-movement worth investigating, NOT proof the low-inventory fee caused it;
    proving that needs the component data Phase B will bring.
    """
    if points < 3 or correlation is None or fee_delta is None:
        return 'insufficient_data'
    if fee_delta > MATERIAL_FEE_DELTA and correlation <= -CORRELATION_THRESHOLD:
        return 'fee_up_cover_down'
    if fee_delta < -MATERIAL_FEE_DELTA and correlation >= CORRELATION_THRESHOLD:
        return 'fee_down_cover_up'
    return 'no_clear_relationship'


def compute(marketplace: str = 'usa', days: int = 30,
            anchor: date | None = None, category: str = '',
            search: str = '') -> dict:
    """Everything the FBA Fee Intelligence page renders, in one pass."""
    from .models import (DailySkuSnapshot, InventorySnapshot, Product,
                         SkuFeeActual)

    anchor = anchor or latest_data_date(marketplace)
    if anchor is None:
        return {'marketplace': marketplace, 'days': days, 'has_data': False,
                'rows': [], 'position': {}, 'series': [], 'drivers': {},
                'note': 'No settlement fee data for this marketplace yet.'}

    cur, prev = resolve_periods(days, anchor)
    # Widest span we need: the previous window start through the anchor, plus
    # 30 days back so 30-day drift is available even on a 7-day view.
    span_start = min(prev.start, anchor - timedelta(days=60))

    fee_qs = SkuFeeActual.objects.filter(date__gte=span_start, date__lte=anchor)
    if marketplace:
        fee_qs = fee_qs.filter(marketplace=marketplace)

    # ── per SKU per day ─────────────────────────────────────────────────────
    daily = defaultdict(lambda: defaultdict(lambda: {'fee': 0.0, 'units': 0}))
    for r in fee_qs.values('sku', 'date', 'fba_fee_total', 'units'):
        b = daily[r['sku']][r['date']]
        b['fee'] += float(r['fba_fee_total'] or 0)
        b['units'] += int(r['units'] or 0)

    def _agg(sku_days: dict, p: Period) -> tuple[float, int]:
        fee = units = 0
        for d, v in sku_days.items():
            if p.start <= d <= p.end:
                fee += v['fee']
                units += v['units']
        return fee, units

    def _drift(sku_days: dict, n: int) -> float | None:
        c, p = resolve_periods(n, anchor)
        cf, cu = _agg(sku_days, c)
        pf, pu = _agg(sku_days, p)
        cw, pw = _weighted(cf, cu), _weighted(pf, pu)
        return (cw - pw) if (cw is not None and pw is not None) else None

    # ── product metadata + inventory ────────────────────────────────────────
    pmeta = {}
    pq = Product.objects.all()
    if marketplace:
        pq = pq.filter(marketplace=marketplace)
    for p in pq.values('id', 'sku', 'asin', 'title', 'category', 'marketplace'):
        if p['sku'] and p['sku'] not in pmeta:
            pmeta[p['sku']] = p

    inv_hist = defaultdict(dict)
    inv_now = {}
    pids = {p['id']: p['sku'] for p in pmeta.values()}
    if pids:
        for s in (InventorySnapshot.objects
                  .filter(product_id__in=list(pids), date__gte=span_start,
                          date__lte=anchor)
                  .values('product_id', 'date', 'afn_fulfillable', 'days_cover')
                  .order_by('date')):
            sku = pids.get(s['product_id'])
            if not sku:
                continue
            inv_hist[sku][s['date']] = (int(s['afn_fulfillable'] or 0),
                                        float(s['days_cover'] or 0))
            inv_now[sku] = (int(s['afn_fulfillable'] or 0),
                            float(s['days_cover'] or 0))

    sold = defaultdict(int)
    dq = DailySkuSnapshot.objects.filter(date__gte=cur.start, date__lte=cur.end)
    if marketplace:
        dq = dq.filter(marketplace=marketplace)
    from django.db.models import Sum as _Sum
    for r in dq.values('sku').annotate(q=_Sum('qty')):
        sold[r['sku']] = int(r['q'] or 0)

    # ── build rows ──────────────────────────────────────────────────────────
    rows: list[SkuRow] = []
    for sku, sku_days in daily.items():
        cf, cu = _agg(sku_days, cur)
        pf, pu = _agg(sku_days, prev)
        cw, pw = _weighted(cf, cu), _weighted(pf, pu)
        if cu <= 0 and pu <= 0:
            continue                      # never billed in either window

        meta = pmeta.get(sku, {})
        delta = (cw - pw) if (cw is not None and pw is not None) else None
        # Impact uses BILLED units in the current window (business rule).
        impact = (delta * cu) if delta is not None else 0.0

        pts = sorted(d for d in sku_days
                     if cur.start <= d <= cur.end and sku_days[d]['units'] > 0)
        fees = [sku_days[d]['fee'] / sku_days[d]['units'] for d in pts]
        covers = [inv_hist[sku][d][1] for d in pts if d in inv_hist.get(sku, {})]
        corr = _pearson(fees[:len(covers)], covers) if len(covers) >= 3 else None

        inv = inv_now.get(sku)
        rows.append(SkuRow(
            sku=sku, asin=meta.get('asin', '') or '',
            title=(meta.get('title') or '')[:80],
            category=meta.get('category', '') or '',
            marketplace=meta.get('marketplace', marketplace) or marketplace,
            current_fee=cw, previous_fee=pw, fee_delta=delta,
            fee_delta_pct=((delta / pw * 100) if (delta is not None and pw) else None),
            drift_7d=_drift(sku_days, 7), drift_14d=_drift(sku_days, 14),
            drift_30d=_drift(sku_days, 30),
            billed_units=cu, units_sold=sold.get(sku, 0),
            incremental_cost=impact if impact > 0 else 0.0,
            savings=(-impact) if impact < 0 else 0.0,
            net_impact=impact,
            current_inventory=inv[0] if inv else None,
            current_days_cover=inv[1] if inv else None,
            inventory_signal=_inventory_signal(corr, delta, len(covers)),
            correlation=corr, data_days=len(pts),
            series=[{'date': d.isoformat(),
                     'fee': round(sku_days[d]['fee'] / sku_days[d]['units'], 4),
                     'units': sku_days[d]['units'],
                     'days_cover': inv_hist.get(sku, {}).get(d, (None, None))[1]}
                    for d in pts],
        ))

    if category:
        rows = [r for r in rows if r.category == category]
    if search:
        s = search.lower()
        rows = [r for r in rows
                if s in r.sku.lower() or s in (r.asin or '').lower()
                or s in (r.title or '').lower()]
    rows.sort(key=lambda r: -r.net_impact)

    # ── account position ────────────────────────────────────────────────────
    tot_cf = sum(v['fee'] for sd in daily.values() for d, v in sd.items()
                 if cur.start <= d <= cur.end)
    tot_cu = sum(v['units'] for sd in daily.values() for d, v in sd.items()
                 if cur.start <= d <= cur.end)
    tot_pf = sum(v['fee'] for sd in daily.values() for d, v in sd.items()
                 if prev.start <= d <= prev.end)
    tot_pu = sum(v['units'] for sd in daily.values() for d, v in sd.items()
                 if prev.start <= d <= prev.end)

    def _acct_drift(n):
        c, p = resolve_periods(n, anchor)
        cf = sum(v['fee'] for sd in daily.values() for d, v in sd.items() if c.start <= d <= c.end)
        cu_ = sum(v['units'] for sd in daily.values() for d, v in sd.items() if c.start <= d <= c.end)
        pf_ = sum(v['fee'] for sd in daily.values() for d, v in sd.items() if p.start <= d <= p.end)
        pu_ = sum(v['units'] for sd in daily.values() for d, v in sd.items() if p.start <= d <= p.end)
        a, b = _weighted(cf, cu_), _weighted(pf_, pu_)
        return (a - b) if (a is not None and b is not None) else None

    inc = sum(r.incremental_cost for r in rows)
    sav = sum(r.savings for r in rows)
    position = {
        'current_fee': _weighted(tot_cf, tot_cu),
        'previous_fee': _weighted(tot_pf, tot_pu),
        'drift_7d': _acct_drift(7), 'drift_14d': _acct_drift(14),
        'drift_30d': _acct_drift(30),
        'billed_units': tot_cu, 'total_fee_paid': round(tot_cf, 2),
        'incremental_cost': round(inc, 2), 'savings': round(sav, 2),
        'net_impact': round(inc - sav, 2),
        'skus_total': len(rows),
        'skus_worse': sum(1 for r in rows if r.net_impact > 0),
        'skus_better': sum(1 for r in rows if r.net_impact < 0),
        'skus_cover_signal': sum(1 for r in rows
                                 if r.inventory_signal == 'fee_up_cover_down'),
    }
    for k in ('current_fee', 'previous_fee', 'drift_7d', 'drift_14d', 'drift_30d'):
        if position[k] is not None:
            position[k] = round(position[k], 4)

    # ── account-level daily series (fee/unit + cover, same axis) ────────────
    per_day = defaultdict(lambda: {'fee': 0.0, 'units': 0})
    for sd in daily.values():
        for d, v in sd.items():
            if cur.start <= d <= cur.end:
                per_day[d]['fee'] += v['fee']
                per_day[d]['units'] += v['units']
    cover_day = defaultdict(list)
    for sku, hist in inv_hist.items():
        for d, (_inv, cov) in hist.items():
            if cur.start <= d <= cur.end and cov:
                cover_day[d].append(cov)
    series = [{'date': d.isoformat(),
               'fee': round(per_day[d]['fee'] / per_day[d]['units'], 4),
               'units': per_day[d]['units'],
               'days_cover': (round(sum(cover_day[d]) / len(cover_day[d]), 1)
                              if cover_day.get(d) else None)}
              for d in sorted(per_day) if per_day[d]['units'] > 0]

    return {
        'marketplace': marketplace, 'days': days, 'has_data': True,
        'anchor': anchor.isoformat(),
        'period': {'current': cur.label, 'previous': prev.label,
                   'start': cur.start.isoformat(), 'end': cur.end.isoformat()},
        'position': position,
        'series': series,
        'rows': [r.as_dict() for r in rows],
        'categories': sorted({r.category for r in rows if r.category}),
        'methodology': (
            'Fee per unit is units-weighted: sum(fba_fee_total) / sum(units) '
            'over the window, compared against the immediately preceding window '
            'of equal length. Impact = fee delta x BILLED units (SkuFeeActual.'
            'units) — what Amazon actually charged. Units sold is shown as '
            'context only and does not drive the cost figures.'),
        'scope_note': (
            'Phase A reports WHAT the fee did and WHAT it cost. It does not '
            'attribute the change to base fee, fuel, low-inventory or SIPP — '
            'settlement data contains a single aggregate FBA fee line with no '
            'component split. Inventory relationships are shown as correlation, '
            'never causation.'),
    }
