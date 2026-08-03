"""
Inventory Planner engine — mirrors ops' "Inventory" sheet column-for-column,
computed live.

Coverage days everywhere divide by PDS (potential daily sale), exactly as the
sheet does. Target coverage days come from the SKU tier (Alpha/Beta/Ceta).
Averages (7 / 30 / 90-day) are computed live from DailySkuSnapshot.

Also builds a 120-day position series (demand = max(PDS, ADS)) for the
per-SKU drill-down chart and the shortage/order-by alerts.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

# Target coverage days by tier (ops "Alpha/Beta/Ceta" constants, editable).
TARGET_DAYS = {'alpha': 45, 'beta': 40, 'ceta': 35}
DEFAULT_TARGET_DAYS = 40

# Lead-time legs (days) — region-lane defaults for the order-by date.
LEAD_LEGS = {
    'usa': {'production': 90, 'sea': 45, 'port_to_wh': 10},
    'uk':  {'production': 90, 'sea': 45, 'port_to_wh': 10},
    'ae':  {'production': 90, 'sea': 15, 'port_to_wh': 10},
    'sa':  {'production': 90, 'sea': 15, 'port_to_wh': 10},
}
HORIZON_DAYS = 120
SAFETY_STOCK_DAYS = 7


def total_lead_days(region: str) -> int:
    return sum(LEAD_LEGS.get(region, LEAD_LEGS['usa']).values())


def ship_lead_days(region: str) -> int:
    """Sea + port→warehouse only — the time to SHIP already-produced stock
    from origin to FC. Below this many days of on-hand cover with nothing in
    transit, a shortage can no longer be prevented by ocean freight."""
    legs = LEAD_LEGS.get(region, LEAD_LEGS['usa'])
    return legs['sea'] + legs['port_to_wh']


def target_days_for(tier: str) -> int:
    return TARGET_DAYS.get((tier or '').strip().lower(), DEFAULT_TARGET_DAYS)


def _ads_windows(region: str) -> dict[str, tuple]:
    """{sku: (avg7, avg30, avg90)} units/day from DailySkuSnapshot."""
    from django.db.models import Sum
    from apps.dashboard.models import DailySkuSnapshot
    end = date.today() - timedelta(days=1)
    out: dict[str, list] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for i, win in enumerate((7, 30, 90)):
        start = end - timedelta(days=win - 1)
        rows = (DailySkuSnapshot.objects
                .filter(marketplace=region, date__gte=start, date__lte=end)
                .values('sku').annotate(q=Sum('qty')))
        for r in rows:
            out[r['sku'].upper()][i] = (r['q'] or 0) / win
    return {k: tuple(v) for k, v in out.items()}


def _pds_map(region: str) -> dict[str, list]:
    from .models import DemandInput
    out: dict[str, list] = defaultdict(list)
    for d in (DemandInput.objects.filter(region=region)
              .order_by('-effective_from', '-created_at')):
        out[d.sku.upper()].append((d.effective_from, d.effective_to, d.pds))
    return out


def _pds_on(day: date, entries: list):
    for eff_from, eff_to, pds in entries:
        if eff_from <= day and (eff_to is None or day <= eff_to):
            return pds
    return None


def _cov(units: float, pds: float):
    return round(units / pds, 1) if pds and pds > 0 else None


def build_projection(region: str = 'usa', horizon: int = HORIZON_DAYS) -> dict:
    from .models import (InTransitLine, InTransitShipment, PlanningSku,
                         WarehouseStock)

    today = date.today()
    lead = total_lead_days(region)
    avgs = _ads_windows(region)
    pds_map = _pds_map(region)

    # stock by SKU: FBA detail (available/inbound/reserved), AWD, all-3PL
    fba_detail: dict[str, dict] = defaultdict(dict)
    awd_units = defaultdict(int)
    threepl = defaultdict(lambda: defaultdict(int))   # sku → {name: units}
    threepl_total = defaultdict(int)
    stale = {}
    for row in (WarehouseStock.objects.select_related('warehouse')
                .filter(warehouse__region=region, warehouse__is_active=True)):
        sku = row.sku.upper()
        k = row.warehouse.kind
        if k == 'fba':
            fba_detail[sku] = row.detail or {'available': row.units}
        elif k == 'awd':
            awd_units[sku] += row.units
        else:                                          # any 3PL warehouse
            threepl[sku][row.warehouse.name] += row.units
            threepl_total[sku] += row.units
        prev = stale.get(sku)
        if prev is None or row.as_of < prev:
            stale[sku] = row.as_of

    # transit units + arrival schedule
    transit_units = defaultdict(int)
    # Subset of the above bound for an Amazon FC rather than AWD/3PL. Amazon
    # already reports these as FBA "inbound", so they get netted out below to
    # avoid counting the same cartons twice.
    fc_transit_units = defaultdict(int)
    arrivals = defaultdict(lambda: defaultdict(int))
    arrival_detail = defaultdict(lambda: defaultdict(list))   # sku→date→[lines]
    unscheduled = defaultdict(int)
    for line in (InTransitLine.objects.select_related('shipment', 'shipment__destination')
                 .filter(shipment__region=region)
                 .exclude(shipment__status__in=['received', 'cancelled'])):
        sku = line.sku.upper()
        # Once Amazon starts counting a container in, the units it has already
        # received are in warehouse stock — only the REMAINDER is still
        # inbound. Counting the whole line through receiving would double it
        # against on-hand, which is what left 45,088 phantom units on the
        # books while five containers sat "at port" after landing.
        #
        # amazon_received_units is Amazon's own figure (sync_awd_receipts),
        # already converted from cases to eaches. Zero for anything Amazon has
        # not begun receiving, so this is a no-op while genuinely in transit.
        got = int(line.amazon_received_units or 0)
        remaining = max(0, int(line.units or 0) - got)
        if remaining == 0:
            continue                       # fully counted in by Amazon
        transit_units[sku] += remaining
        dest = line.shipment.destination
        if dest is not None and dest.kind == 'fba':
            fc_transit_units[sku] += remaining
        eta = line.shipment.eta_destination
        bucket = None
        if eta and eta >= today:
            bucket = eta
        elif eta:
            bucket = today + timedelta(days=3)
        if bucket is not None:
            arrivals[sku][bucket] += remaining
            sh = line.shipment
            arrival_detail[sku][bucket].append({
                'container': sh.container_no or sh.shipment_id or f'#{sh.pk}',
                'units': remaining,
                'received': got,
                'status': sh.status})
        else:
            unscheduled[sku] += remaining

    skus = list(PlanningSku.objects.filter(region=region, is_active=True))
    known = {s.sku.upper() for s in skus}

    rows = []
    for ps in skus:
        sku = ps.sku.upper()
        a7, a30, a90 = avgs.get(sku, (0.0, 0.0, 0.0))
        p_entries = pds_map.get(sku, [])
        pds = _pds_on(today, p_entries)
        pdsv = pds if (pds and pds > 0) else 0.0

        fd = fba_detail.get(sku, {})
        available = int(fd.get('available', 0))
        reserved = int(fd.get('reserved', 0))
        # Amazon's FBA "inbound" covers anything booked into an inbound
        # shipment. When a container of ours is sent straight to an FC, those
        # same units are ALSO an open InTransitLine — counting both inflates
        # cover. The container is the more detailed record (it carries ETA and
        # per-line units), so it wins: net the FC-bound container units out of
        # Amazon's inbound. Anything left over is a genuine AWD→FC transfer,
        # which has no container behind it and must still count.
        inbound_raw = int(fd.get('inbound', 0))
        inbound = max(0, inbound_raw - int(fc_transit_units.get(sku, 0)))
        total_amazon = available + inbound + reserved
        awd = int(awd_units.get(sku, 0))
        tpl = int(threepl_total.get(sku, 0))          # all 3PL warehouses
        total_wh = tpl + awd
        transit = int(transit_units.get(sku, 0))
        pak_stock = int(ps.factory_stock)
        in_prod = int(ps.factory_production)
        target = target_days_for(ps.sku_type)

        cov_amz = _cov(total_amazon, pdsv)
        stock_req_days = round(target - (cov_amz or 0), 1)
        units_req_fc = round(pdsv * stock_req_days) if pdsv else 0
        cov_wh = _cov(total_wh, pdsv)
        cov_transit = _cov(transit, pdsv)
        cov_pak = _cov(pak_stock, pdsv)
        total_cov_usa = round((cov_amz or 0) + (cov_wh or 0)
                              + (cov_transit or 0), 1) if pdsv else None
        total_cov = _cov(total_amazon + total_wh + transit + pak_stock, pdsv)

        # 120-day depletion series. Demand basis = PDS when the sales team has
        # set one (their planning intent), else the live 7-day ADS.
        demand_basis = pdsv if pdsv > 0 else a7
        pos = float(total_amazon + total_wh)
        series, stockout = [], None
        arrival_markers = []
        for i in range(horizon):
            day = today + timedelta(days=i)
            arr = arrivals[sku].get(day, 0)
            if arr:
                arrival_markers.append({'offset': i, 'units': arr,
                                        'date': day.isoformat(),
                                        'containers': arrival_detail[sku].get(
                                            day, [])})
            pos += arr
            pv = _pds_on(day, p_entries)
            demand = pv if (pv is not None and pv > 0) else (a7 if pdsv == 0 else 0.0)
            pos -= demand
            series.append(round(pos, 1))
            if stockout is None and demand_basis > 0 \
                    and pos <= demand_basis * SAFETY_STOCK_DAYS:
                stockout = day
        order_by = (stockout - timedelta(days=lead)) if stockout else None
        cover_total_days = round((total_amazon + total_wh + transit) / demand_basis, 1) \
            if demand_basis > 0 else None
        # On-hand-only cover (FC + WH + 3PL, no transit) drives the "can we
        # still prevent it?" logic.
        cover_onhand_days = round((total_amazon + total_wh) / demand_basis, 1) \
            if demand_basis > 0 else None
        ship_lead = ship_lead_days(region)
        ship_by = (stockout - timedelta(days=ship_lead)) if stockout else None
        # Point of no return: will short, nothing on the water, and on-hand
        # cover is already below the shipping lead → ocean freight can't arrive
        # in time even if we ship today.
        point_of_no_return = bool(
            stockout and transit == 0
            and cover_onhand_days is not None
            and cover_onhand_days < ship_lead)

        # status from the sheet's own numbers
        if pdsv == 0:
            status = 'no_pds'
        elif total_cov is not None and total_cov < target:
            status = 'critical'          # even all sources won't hit target
        elif units_req_fc > 0:
            status = 'warning'           # Amazon FC below target; WH/transit covers
        elif total_cov is not None and total_cov > target * 3:
            status = 'excess'
        else:
            status = 'ok'

        rows.append({
            'sku_type': ps.sku_type, 'category': ps.category, 'name': ps.name,
            'sku': sku,
            'ads': round(a7, 2), 'avg30': round(a30, 2), 'avg90': round(a90, 2),
            'pds': pds,
            'cov_30': _cov(available + reserved, a30),
            'cov_90': _cov(available + reserved, a90),
            'days_avail_pds': _cov(available, pdsv),
            'available': available, 'inbound': inbound, 'reserved': reserved,
            'total_amazon': total_amazon, 'cov_amazon': cov_amz,
            'stock_req_days': stock_req_days, 'units_req_fc': units_req_fc,
            'three_pl': tpl, 'awd': awd, 'total_wh': total_wh,
            'threepl_detail': dict(threepl.get(sku, {})),
            'cov_wh': cov_wh,
            'transit': transit, 'transit_unscheduled': int(unscheduled.get(sku, 0)),
            'cov_transit': cov_transit,
            'total_cov_usa': total_cov_usa,
            'pak_stock': pak_stock, 'cov_pak': cov_pak,
            'in_production': in_prod, 'total_cov': total_cov,
            'target_days': target,
            'stockout_date': stockout.isoformat() if stockout else None,
            'order_by': order_by.isoformat() if order_by else None,
            'order_overdue': bool(order_by and order_by <= today),
            'status': status, 'series': series,
            'arrival_markers': arrival_markers,
            'has_transit': transit > 0,
            'demand_basis': round(demand_basis, 2),
            'cover_total_days': cover_total_days,
            'cover_onhand_days': cover_onhand_days,
            'ship_by': ship_by.isoformat() if ship_by else None,
            'ship_lead': ship_lead,
            'point_of_no_return': point_of_no_return,
            'stock_as_of': stale.get(sku).isoformat() if stale.get(sku) else None,
        })

    unknown = sorted(s for s, v in avgs.items()
                     if v[0] > 0 and s not in known and not s.startswith('AMZN.'))
    order = {'critical': 0, 'warning': 1, 'no_pds': 2, 'ok': 3, 'excess': 4}
    rows.sort(key=lambda r: (order.get(r['status'], 9),
                             r['total_cov'] if r['total_cov'] is not None else 1e9))
    kpi = {
        'critical': sum(1 for r in rows if r['status'] == 'critical'),
        'warning':  sum(1 for r in rows if r['status'] == 'warning'),
        'excess':   sum(1 for r in rows if r['status'] == 'excess'),
        'no_pds':   sum(1 for r in rows if r['status'] == 'no_pds'),
        'transit_units': sum(r['transit'] for r in rows),
        'unknown_selling': len(unknown),
    }
    return {'rows': rows, 'kpi': kpi, 'horizon': horizon, 'lead_days': lead,
            'ship_lead_days': ship_lead_days(region),
            'region': region, 'today': today.isoformat(),
            'target_days': TARGET_DAYS, 'unknown_selling': unknown[:50]}


# ── Loading Plan: "how much to ship on the next container" ───────────────────
# Coverage-target replenishment, netting against everything already committed
# (on-hand + in transit + open PO balance). Answers: for each SKU, how many
# units should the next container carry so we don't stock out.

def build_loading_plan(region: str = 'usa', cover_days: int | None = None,
                       category: str = '', tier: str = '') -> dict:
    """Layer on top of build_projection. For each active, selling SKU:

        target pipeline  = demand/day × (ship_lead + tier target days)
        committed        = on-hand (FBA+WH) + in transit
        NEED TO LOAD     = max(0, target − committed)          ← the suggestion
        of which already on open POs        = on_order
        → fresh PO still required           = max(0, need − on_order)

    Rounded up to the carton, then the min-ship-qty. `cover_days` overrides the
    per-tier target (a flat horizon for the whole plan) when set.
    """
    import math

    from .models import PlanningSku, POLine

    proj = build_projection(region)
    ship_lead = proj['ship_lead_days']

    meta = {p.sku.upper(): p for p in
            PlanningSku.objects.filter(region=region, is_active=True)}

    # open PO balance by SKU. Region-blind total, but the per-region plan only
    # counts units RESERVED to this region (multi-supplier reservation), so the
    # same open-PO units aren't double-promised. Unreserved balance is a pool.
    on_order: dict[str, int] = defaultdict(int)       # reserved to THIS region
    open_pool: dict[str, int] = defaultdict(int)      # unreserved, assignable
    for l in (POLine.objects.select_related('po')
              .prefetch_related('reservations')
              .exclude(po__status__in=['closed', 'cancelled', 'short_closed'])):
        sku = l.sku.upper()
        rem = l.remaining_units
        reserved_here = 0
        reserved_total = 0
        for rv in l.reservations.all():
            reserved_total += rv.units
            if rv.region == region:
                reserved_here += rv.units
        on_order[sku] += reserved_here
        open_pool[sku] += max(rem - reserved_total, 0)

    rows, tot = [], {'need': 0, 'new_po': 0, 'on_order_used': 0,
                     'boxes': 0, 'skus': 0, 'urgent': 0}
    for r in proj['rows']:
        demand = r['demand_basis']
        if demand <= 0:                    # no PDS and no sales — nothing to plan
            continue
        if category and r['category'] != category:
            continue
        if tier and (r['sku_type'] or '').lower() != tier.lower():
            continue

        target_days = cover_days or (ship_lead + r['target_days'])
        pipeline_need = round(demand * target_days)
        onhand = r['total_amazon'] + r['total_wh']
        committed = onhand + r['transit']
        need = max(0, pipeline_need - committed)

        ps = meta.get(r['sku'])
        upb = ps.units_per_box if ps else 0
        msq = ps.msq if ps else 0
        if need and upb:                   # round up to whole cartons
            need = math.ceil(need / upb) * upb
        if 0 < need < msq:                 # honour a minimum ship qty
            need = msq

        oo = on_order.get(r['sku'], 0)     # reserved to THIS region
        pool = open_pool.get(r['sku'], 0)  # unreserved open PO, assignable
        from_po = min(need, oo)            # covered by units reserved here
        new_po = max(0, need - oo - pool)  # fresh PO after reserved + poolable
        factory = r['pak_stock'] + r['in_production']
        boxes = math.ceil(need / upb) if (need and upb) else 0

        rows.append({
            'sku': r['sku'], 'name': r['name'], 'category': r['category'],
            'sku_type': r['sku_type'],
            'demand': demand, 'basis': 'PDS' if r['pds'] else 'ADS',
            'target_days': target_days,
            'onhand': onhand, 'transit': r['transit'],
            'cover_now_days': r['cover_total_days'],   # on-hand + transit
            'need': need, 'on_order': oo, 'open_pool': pool,
            'from_po': from_po, 'new_po': new_po, 'factory': factory,
            'boxes': boxes,
            'units_per_box': upb,
            'stockout_date': r['stockout_date'],
            'ship_by': r['ship_by'], 'order_overdue': r['order_overdue'],
            'point_of_no_return': r['point_of_no_return'],
            'status': r['status'],
        })
        if need > 0:
            tot['need'] += need
            tot['new_po'] += new_po
            tot['on_order_used'] += from_po
            tot['boxes'] += boxes
            tot['skus'] += 1
            if r['point_of_no_return'] or r['order_overdue']:
                tot['urgent'] += 1

    # most urgent / largest need first
    rows = [r for r in rows if r['need'] > 0]
    rows.sort(key=lambda r: (
        0 if r['point_of_no_return'] else (1 if r['order_overdue'] else 2),
        r['stockout_date'] or '9999', -r['need']))

    cats = sorted({r['category'] for r in proj['rows'] if r['category']})
    return {'rows': rows, 'totals': tot, 'region': region,
            'ship_lead_days': ship_lead, 'today': proj['today'],
            'categories': cats, 'cover_days': cover_days}
