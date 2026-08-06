# FBA transfers

files: `apps/inventory_planning/{views,models,importer}.py`
       `templates/inventory_planning/fba_transfers.html`
verified against: `ad78c4b` · 2026-08-06

A transfer moves stock we already own from our own warehouse into an Amazon
fulfilment centre. It is the last leg: units that arrived from the factory
months ago becoming sellable.

## Purpose

Stock in a 3PL or AWD warehouse cannot be sold. Only units inside an Amazon FC
fill an order. The transfer is how the business moves the one into the other,
and the record of what is currently between the two.

It also protects a number the planner depends on. Shipping a transfer draws the
units out of the source warehouse immediately, so cover is not counted twice
while cartons are in a truck.

## Scope

Covers the transfer's lifecycle, its effect on warehouse stock, and the bulk
upload.

**Not covered:**
- factory → warehouse movement, which is a container — [containers.md](containers.md)
- Amazon's count of a container — [receiving.md](receiving.md)
- what cover the units contribute once inside Amazon — [planner.md](planner.md)

## Business workflow

```
Draft → Shipped ──────────────→ Received at FC
          ↓                            ↓
 source warehouse drawn down    Amazon's own sync reports them fulfillable
```

A transfer is built as a draft, checked, then shipped. Shipping is the moment
that matters: it is when the units leave our stock figures. Receiving records
arrival for our own audit, but does **not** add to Amazon's stock — that comes
from Amazon.

## Actors

| Actor | Does |
|---|---|
| Ops | builds the draft, ships it, records receipt |
| The system | draws down source stock on ship, returns it on cancel |
| Amazon | reports the units as inbound and then fulfillable, on its own schedule |

## Business rules

1. **A transfer moves stock between places we already hold it.** It creates no
   units and consumes no purchase-order balance — that happened at the container.
2. **Shipping draws down the source warehouse.** The units leave 3PL or AWD
   stock at that moment, not on arrival.
3. **Nothing is ever added to Amazon stock by hand.** Amazon's own sync reports
   the units as inbound and then fulfillable. Writing them ourselves would
   double-count against the figure the sync overwrites. See `INV-D-018`.
4. **Only a draft can be edited or deleted.** A shipped transfer is a physical
   fact; it is cancelled, never deleted.
5. **Cancelling a shipped transfer returns the units to the source**, exactly
   once — the transfer records whether stock was drawn so a second cancel cannot
   double-credit.
6. **Shipping more than the source holds is a confirmation, not a refusal.**
   The shortfall is named per SKU and ops may proceed: the warehouse's physical
   count outranks our figure, which may simply be stale.
7. **Receiving defaults to a full receipt.** A transfer between our own hands
   and Amazon's inbound dock rarely loses units, and Amazon's count is the one
   that settles it.

## States

| State | Meaning | Entered when | Leaves when |
|---|---|---|---|
| Draft | being built, nothing moved | created | shipped, or deleted |
| Shipped | in transit to Amazon; source already drawn down | ops ships | received, or cancelled |
| Received at FC | arrived, recorded for audit | ops confirms | terminal |
| Cancelled | abandoned; stock returned if it had been drawn | ops cancels | terminal |

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Save a draft | ops | a source warehouse and at least one SKU with units | draft created or replaced |
| Bulk upload | ops | the .xlsx template | drafts created from the file |
| Ship | ops | draft | source drawn down; status shipped; confirmation if short |
| Receive | ops | shipped | lines marked received in full; status received |
| Cancel | ops | draft or shipped | stock returned if it had been drawn |
| Delete | ops | draft only | removed; shipped transfers must be cancelled |

## System behaviour

- The transfer form lists **only SKUs the chosen source warehouse actually
  holds**, with their available units, so a draft cannot be built against stock
  that is not there.
- Saving a draft replaces its lines wholesale — the file or form is the complete
  statement of the transfer.
- Open transfers report their in-transit units — shipped minus received — as a
  KPI, which is the stock in neither our warehouse nor Amazon's.

## Data model

- **Transfer** — one movement from one source warehouse: carrier, reference,
  Amazon shipment id, status, and whether source stock has been drawn.
- **Transfer line** — one SKU on the transfer, with units sent and units
  received.

## Validation rules

| Input | Rule | On failure |
|---|---|---|
| Source warehouse | required | refused |
| Lines | at least one SKU with units above zero | refused |
| Units vs source stock | should not exceed what the warehouse holds | confirmation naming each short SKU; ops may proceed |
| Edit | transfer is a draft | refused, naming the current status |
| Delete | transfer is a draft | refused; cancel instead |

## Edge cases

- **Shipping more than the system thinks we hold.** Allowed after confirmation.
  The warehouse's own count is the physical truth and our figure may be stale;
  refusing would block a real shipment over a stale number.
- **Cancelling a shipped transfer.** Units return to the source. A transfer that
  was never shipped returns nothing, and the flag recording whether stock was
  drawn is what keeps the two cases apart.
- **A transfer of a SKU the source no longer stocks.** The stock row is left at
  zero rather than going negative.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INV-CONT-004` | container goods receipt writes AWD stock the Amazon sync then overwrites — the same rule this document states, broken on the container path | bug |

## Related decisions

`INV-D-018`

## Related documents

- [containers.md](containers.md) — the factory-to-warehouse leg
- [planner.md](planner.md) — what the units do to cover once Amazon holds them
- [receiving.md](receiving.md) — why Amazon's count is never overwritten
