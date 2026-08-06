# Loading plan

files: `apps/inventory_planning/{planning,views}.py`
       `templates/inventory_planning/loading_plan.html`
verified against: `3c62773` · 2026-08-06

The loading plan answers one question: **how many units of each SKU should the
next container to this region carry.** It is the bridge between the planner's
position and a packing list.

## Purpose

A container is a fixed, expensive slot with a two-to-four-month round trip. Fill
it with the wrong mix and one SKU stocks out while another sits in a warehouse
for a year. The loading plan sizes each SKU's share against the pipeline it
needs, nets off everything already committed to that region, and rounds the
remainder to something a factory can actually pack.

It also separates two different answers: what to **ship** (units that exist or
are on order) from what to **buy** (units that do not exist yet) — because those
go to different people.

## Scope

Covers the per-region ship quantity, its netting, and the split between what
open POs can cover and what needs a fresh order.

**Not covered:**
- the position and cover days it is built on — [planner.md](planner.md)
- cross-region purchasing — [reorder.md](reorder.md)
- turning a quantity into an actual container — [allocation-workbench.md](allocation-workbench.md)

## Business workflow

```
Planner position → target pipeline → net off committed → round → NEED TO LOAD
                                                                      ↓
                                            covered by reserved PO  ·  needs a new PO
```

## Business rules

1. **The plan is per region.** Each region has its own lane, lead time and
   pipeline, and a unit put on a USA container is not available to the UK.
2. **Target pipeline = demand per day × (shipping lead + the SKU's target cover
   days).** It covers the voyage *and* the cover the SKU should hold on arrival,
   which is why it is longer than the planner's target alone.
3. **Committed = on-hand + in transit.** Need is the shortfall against target,
   never the whole target.
4. **Only PO balance reserved to this region counts as on order.** Unreserved
   open balance is a **pool** — visible, assignable, but not yet promised, so
   two regions cannot plan the same units. See [purchase-orders.md](purchase-orders.md).
5. **Quantities round up to a whole carton, then to the minimum ship quantity.**
   A factory ships cartons; a suggestion of 1,417 units is not actionable.
6. **A SKU with no demand basis is not planned.** No PDS and no sales means
   there is no defensible quantity, and a guess would fill a container slot.
7. **The plan ranks by urgency, not size** — point of no return first, then
   overdue-to-order, then by stockout date. The largest need is rarely the most
   urgent one.

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Read the plan | anyone | a region | ranked ship quantities with their netting shown |
| Override cover days | planning | — | a flat horizon replaces each SKU's tier target |
| Filter by category or tier | anyone | — | narrows to one product family |
| Export | planning | — | .xlsx to work the container from |

The cover-days override is how a deliberately light or heavy container gets
planned — filling a 40 ft slot, or a top-up shipment between sailings.

## System behaviour

- The plan is a **layer on the planner's projection**, recomputed live, so the
  two can never disagree about the position.
- Rows with no need are dropped rather than shown at zero — the plan is a list
  of what to load, not a catalogue.
- Factory stock and in-production units are shown alongside each row for
  context, but are **not** netted off the need: they are not committed to this
  region until they are on a packing list.

## Data model

The loading plan owns no entities. Every figure is derived from the planner's
position, purchase-order balances and reservations at the moment it is read.

## Edge cases

- **A SKU needed in two regions with one open PO.** The unreserved pool shows
  in both plans, deliberately. Reserving it to one region removes it from the
  other's on-order figure — that is what reservations are for.
- **Need smaller than the minimum ship quantity.** Rounded up to the minimum,
  which can over-ship a slow SKU. Accepted: the alternative is not shipping it
  at all.
- **A SKU at point of no return.** It appears first with a full need, even
  though ocean freight cannot arrive in time. The quantity is still right for
  the *next* container; the timing problem belongs to the planner's alert.

## Related documents

- [planner.md](planner.md) — the position and cover this is built on
- [reorder.md](reorder.md) — the cross-region purchasing view, and why its
  numbers differ from these
- [allocation-workbench.md](allocation-workbench.md) — where the plan becomes a container
- [purchase-orders.md](purchase-orders.md) — reservations and open balance
