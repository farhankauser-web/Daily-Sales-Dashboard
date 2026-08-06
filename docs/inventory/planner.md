# Planner

files: `apps/inventory_planning/{planning,views,models}.py`
       `templates/inventory_planning/{planner,runway}.html`
verified against: `3c62773` · 2026-08-06

The planner answers one question for every SKU in a region: **where do we stand,
and when do we run out.** It is the position and the clock — not the decision
about what to do, which two other machines own.

## Purpose

Stock for one SKU sits in five places at once: Amazon's fulfilment centres, AWD,
3PL warehouses, on the water, and at the factory. Each has a different distance
from a customer. The planner puts them on one line, converts each into **days of
cover** against expected demand, and projects the position forward far enough to
name the day the SKU runs out — and, working back through the lead time, the day
an order had to be placed to prevent it.

Everything downstream is a decision taken on this position: what to put on the
next container ([loading-plan.md](loading-plan.md)) and what to buy
([reorder.md](reorder.md)).

## Scope

Covers the per-SKU position, the cover calculation, the depletion projection,
and the alert states derived from it. The Planner and Runway pages are two
presentations of this one engine.

**Not covered:**
- how much to ship on the next container — [loading-plan.md](loading-plan.md)
- what to buy, and draft POs — [reorder.md](reorder.md)
- where in-transit units come from — [containers.md](containers.md)
- open PO balance — [purchase-orders.md](purchase-orders.md)

## Business workflow

```
Amazon stock sync ─┐
Warehouse stock  ──┼→ Position per SKU → Cover days → 120-day projection
Containers       ──┤                                        ↓
Demand (PDS/ADS) ──┘                          stockout date → order-by · ship-by
```

## Actors

| Actor | Does |
|---|---|
| Sales | sets PDS — the planned daily sale, their intent for the SKU |
| The system | syncs stock, computes cover, projects depletion, raises the alert state |
| Planning | reads the position and acts through the loading plan or reorder |

## Business rules

1. **Demand is PDS where the sales team has set one, otherwise the live 7-day
   average.** PDS is planning intent and outranks history; a SKU with neither
   cannot be planned and is flagged rather than assumed. See `INV-D-017`.
2. **PDS is dated.** Each entry applies from a date, optionally to a date, so a
   seasonal plan projects correctly rather than flattening to one number.
3. **Cover days = units ÷ demand per day**, computed separately for each
   location so the reader can see *where* the cover is, not just how much.
4. **Target cover comes from the SKU's tier** — Alpha 45 days, Beta 40, Ceta 35,
   defaulting to 40. The tier is a commercial judgement about how much stock a
   SKU deserves.
5. **Only the un-received remainder of a container counts as in transit.** Units
   Amazon has already counted are in warehouse stock, and counting both would
   inflate cover. See [receiving.md](receiving.md).
6. **FC-bound containers are netted out of Amazon's own inbound figure**, because
   Amazon reports the same cartons and the container is the more detailed record.
7. **Stockout is the day the position falls to the safety floor** — seven days of
   demand — not the day it reaches zero. Running to zero is already too late.
8. **Order-by = stockout − total lead time.** Ship-by = stockout − shipping lead
   only, which is the later, harsher date: it is the last day already-made stock
   could still be put on a ship and arrive in time.
9. **Point of no return: the SKU will short, nothing is on the water, and
   on-hand cover is already below the shipping lead.** Ocean freight can no
   longer prevent it. This is a distinct state because the remedy changes — air
   freight or a stockout, not a purchase order.
10. **Status is derived from the numbers, never stored** — *critical* when all
    sources together miss target, *warning* when Amazon FC alone is short but
    warehouse or transit covers it, *excess* above three times target,
    *no PDS* when demand is unknown.
11. **Stock carries an as-of time.** A position computed from a stale sync is
    labelled, not silently trusted.

## States

| Status | Meaning | What it asks of the reader |
|---|---|---|
| Critical | every source combined is below target cover | act now — buy or ship |
| Warning | Amazon FC below target; warehouse or transit covers it | move stock, do not buy |
| No PDS | no planned demand and no sales history | set a PDS, or retire the SKU |
| OK | at or near target | nothing |
| Excess | more than three times target cover | stop buying; consider promoting |

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Set or edit PDS | sales | a SKU and an effective-from date | projection recomputes on the next read |
| Refresh Amazon stock | ops | — | FBA and AWD positions re-pulled now |
| Drill into a SKU | anyone | — | 120-day chart with container arrivals marked |
| Read the runway | anyone | — | the same position ordered by time-to-stockout |

## System behaviour

- **The projection is computed live on every read**, never stored, so it cannot
  disagree with the stock and container records beneath it.
- Twice daily the Amazon stock sync refreshes FBA and AWD, ten minutes before
  the receipt syncs — the ordering matters, see [receiving.md](receiving.md).
- The 120-day series steps forward day by day, adding each container's arrival
  on its ETA and subtracting that day's demand, so an arrival visibly rescues a
  projected stockout.
- Containers with no ETA are counted in the position but reported separately as
  **unscheduled** — they cannot be placed on the timeline.
- SKUs selling on Amazon that the catalogue does not know are listed back as
  **unknown selling**, so the position is never quietly incomplete.

## Data model

- **Planning SKU** — the master record per SKU and region: tier, category,
  units per box, minimum ship quantity, factory stock and production, and the
  region's FNSKU.
- **Demand input (PDS)** — a planned daily sale for a SKU, effective between
  dates.
- **Warehouse stock** — units of a SKU at one warehouse, with an as-of time and
  for Amazon a split of available, reserved and inbound.
- The position itself is not an entity — it is derived on every read.

## Edge cases

- **A SKU with sales but no PDS.** Projected on its 7-day average and flagged
  *no PDS*, because a plan nobody set is not a plan.
- **A SKU with PDS but no sales.** Projected on PDS. This is correct for a
  launch, and is why PDS outranks history.
- **A container with no ETA.** Counted in cover but absent from the timeline, so
  the projected stockout is pessimistic. Reported as unscheduled rather than
  placed on a guessed date.
- **Stale stock.** The as-of time is carried through to the row, so a position
  built on a sync that failed last night is visibly old rather than wrong.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INV-PLAN-001` | lead times exist twice — region constants drive the order-by date while the supplier's own figures drive reorder | bug |

## Related decisions

`INV-D-017`

## Related documents

- [loading-plan.md](loading-plan.md) — how much to put on the next container
- [reorder.md](reorder.md) — what to buy, and draft POs
- [receiving.md](receiving.md) — why only the un-received remainder is inbound
- [containers.md](containers.md) — where transit units come from
