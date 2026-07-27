"""
Region cash-flow planner.

Opening bank balance + estimated Amazon inflows + funds injections
− container payments (FOB from allocations + manual freight/duty) − running costs
= a dated running-balance ledger that shows when cash goes short.

Container payments are timed on the container's Port/ETA date. Amazon inflows
are projected from payout history. Both are materialised as CashFlowEntry rows
(auto_source set) and refreshed; any row the user edits is `locked` and kept.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta

CYCLES_AHEAD = 4          # plan only 4 payment cycles forward (~56 days on the
                          # USA biweekly cadence; ~4 months on a monthly one)
RECENT_EVENTS = 6         # base the per-cycle estimate on this many recent
                          # settlement events (≈ last 10-12 raw payout rows)


def _settlement_events(region: str) -> list[tuple]:
    """Payouts collapsed into settlement events: Amazon pays a big settlement
    plus small same-cycle top-ups a day or two later — those belong to ONE
    disbursement. Returns [(date, total_amount)] oldest→newest."""
    from apps.dashboard.models import AmazonPayout
    rows = list(AmazonPayout.objects.filter(marketplace=region)
                .order_by('payout_date')
                .values_list('payout_date', 'amount'))
    events: list[list] = []
    for d, a in rows:
        a = float(a or 0)
        if events and (d - events[-1][0]).days <= 4:
            events[-1][1] += a            # same settlement — add the top-up
        else:
            events.append([d, a])
    return [(d, amt) for d, amt in events]


def _payout_cadence(region: str) -> int:
    """Median gap (days) between settlement events. Fallback 14."""
    events = _settlement_events(region)
    if len(events) < 2:
        return 14
    gaps = [(b[0] - a[0]).days for a, b in zip(events, events[1:])]
    cad = int(round(statistics.median(gaps))) if gaps else 14
    return min(max(cad, 7), 35)


def horizon_days(region: str) -> int:
    """The planning window = CYCLES_AHEAD payment cycles."""
    return CYCLES_AHEAD * _payout_cadence(region)


# ── Amazon inflow estimate from the last real settlements ───────────────────

def estimate_amazon_inflows(region: str, start: date) -> list[dict]:
    """Project the next CYCLES_AHEAD Amazon disbursements. Amount per cycle =
    average of the last RECENT_EVENTS actual settlement events (the real money
    that hit the bank), not a diluted run-rate. Cadence from event spacing."""
    events = _settlement_events(region)
    if not events:
        return []
    cadence = _payout_cadence(region)
    recent = [amt for _, amt in events[-RECENT_EVENTS:] if amt > 0]
    if not recent:
        return []
    # drop tiny off-cycle disbursements (reserve releases / partials) that would
    # drag the typical-settlement average down
    med = statistics.median(recent)
    core = [a for a in recent if a >= 0.25 * med] or recent
    per_period = round(sum(core) / len(core), 2)

    out, d = [], max(events[-1][0] + timedelta(days=cadence), start)
    for _ in range(CYCLES_AHEAD):          # exactly 4 cycles forward
        out.append({'date': d, 'amount': per_period})
        d += timedelta(days=cadence)
    return out


# ── container payment amount = FOB (allocations) + freight/duty (manual) ────

def _container_fob(sh) -> float:
    total = 0.0
    for l in sh.lines.all():
        if l.po_line_id and l.po_line and l.po_line.group_id:
            total += l.units * float(l.po_line.group.fob_rate or 0)
    return round(total, 2)


def _pay_date(sh):
    return sh.eta_port or sh.eta_destination or sh.departure_date


# ── refresh: materialise auto rows, preserve locked / manual ────────────────

def refresh_region(region: str) -> dict:
    from django.db import transaction
    from .models import CashFlowEntry, CashFlowPlan, InTransitShipment

    res = {'containers': 0, 'amazon': 0, 'skipped_locked': 0}
    today = date.today()
    plan = CashFlowPlan.objects.filter(region=region).first()
    lead = plan.pay_lead_days if plan else 0
    with transaction.atomic():
        # ── container outflows (one per active, dated, region container) ──
        active = (InTransitShipment.objects.filter(region=region)
                  .exclude(status__in=['received', 'cancelled'])
                  .prefetch_related('lines__po_line__group'))
        seen = set()
        for sh in active:
            pay = _pay_date(sh)
            if not pay:
                continue
            seen.add(sh.pk)
            fob = _container_fob(sh)
            amount = fob + float(sh.freight_cost or 0)
            e = CashFlowEntry.objects.filter(region=region,
                                             auto_source='container',
                                             container=sh).first()
            if e and e.locked:
                res['skipped_locked'] += 1
                continue
            if e is None:
                e = CashFlowEntry(region=region, auto_source='container',
                                  container=sh, direction='out',
                                  category='container')
            e.date = pay - timedelta(days=lead)      # pay N days before port
            e.description = sh.container_no or (sh.shipment_id or f'#{sh.pk}')
            e.vendor = sh.vendor
            e.amount = amount
            e.save()
            res['containers'] += 1
        # drop container auto-rows whose container is no longer active
        (CashFlowEntry.objects.filter(region=region, auto_source='container')
         .exclude(container_id__in=seen).exclude(locked=True).delete())

        # ── Amazon inflow projection ── drop ALL unlocked estimate rows
        # (they're forward-looking guesses; regenerate the next cycles fresh)
        (CashFlowEntry.objects.filter(region=region, auto_source='amazon',
                                      locked=False).delete())
        for it in estimate_amazon_inflows(region, today):
            CashFlowEntry.objects.create(
                region=region, auto_source='amazon', direction='in',
                category='amazon', date=it['date'], amount=it['amount'],
                description='Amazon Inflow (est.)')
            res['amazon'] += 1
    return res


# ── build the running ledger ────────────────────────────────────────────────

def build_ledger(region: str, horizon: int | None = None) -> dict:
    from .models import CashFlowEntry, CashFlowPlan

    plan = CashFlowPlan.objects.filter(region=region).first()
    opening = float(plan.opening_balance) if plan else 0.0
    as_of = plan.opening_as_of if (plan and plan.opening_as_of) else date.today()
    if horizon is None:
        horizon = horizon_days(region)
    end = date.today() + timedelta(days=horizon)

    entries = (CashFlowEntry.objects.filter(region=region, date__lte=end)
               .select_related('container').order_by('date', 'id'))
    rows, bal = [], opening
    min_bal, min_date = opening, as_of
    tot_in = tot_out = 0.0
    for e in entries:
        # a container that's been received is already paid — never plan for it,
        # even if its auto row hasn't been cleaned up by a refresh yet
        if (e.auto_source == 'container' and e.container
                and e.container.status in ('received', 'cancelled')):
            continue
        amt = float(e.amount)
        inflow = amt if e.direction == 'in' else 0.0
        outflow = amt if e.direction == 'out' else 0.0
        bal += inflow - outflow
        tot_in += inflow
        tot_out += outflow
        if bal < min_bal:
            min_bal, min_date = bal, e.date
        # container payment = FOB (from the PO allocations) + freight/duty
        fob = freight = None
        if e.auto_source == 'container' and e.container:
            fob = _container_fob(e.container)
            freight = float(e.container.freight_cost or 0)
        rows.append({
            'id': e.pk, 'date': e.date.isoformat(),
            'particulars': dict(CashFlowEntry.CATEGORIES).get(e.category,
                                                              e.category),
            'category': e.category, 'direction': e.direction,
            'description': e.description, 'vendor': e.vendor,
            'inflow': round(inflow, 2), 'outflow': round(outflow, 2),
            'fob': fob, 'freight': freight,
            'balance': round(bal, 2),
            'port_date': (e.container.eta_port.isoformat()
                          if e.container and e.container.eta_port else None),
            'auto': e.auto_source, 'locked': e.locked, 'note': e.note,
            'container_id': e.container_id,
        })
    return {
        'region': region,
        'opening': round(opening, 2),
        'as_of': as_of.isoformat(),
        'horizon_days': horizon,
        'cycles': CYCLES_AHEAD,
        'window_end': end.isoformat(),
        'rows': rows,
        'total_in': round(tot_in, 2), 'total_out': round(tot_out, 2),
        'ending': round(bal, 2),
        'min_balance': round(min_bal, 2),
        'min_date': min_date.isoformat(),
        'shortfall': round(-min_bal, 2) if min_bal < 0 else 0.0,
    }
