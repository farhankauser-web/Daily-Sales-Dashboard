# Containers

files: `apps/inventory_planning/{views,models,procurement,cashflow}.py`
       `templates/inventory_planning/{containers,allocation,receiving,container_history}.html`
verified against: `653aa28` · 2026-08-05

A container is one physical shipment of our goods from factories to an Amazon
warehouse. It is the unit we plan cash against, track in transit, and reconcile
when Amazon counts it in.

## Purpose

A container answers three questions the business asks constantly: *what is on
the water*, *when do we pay for it*, and *did all of it arrive*. Without it,
units ordered from a factory disappear from view between the PO and Amazon's
inventory report — a gap of two to four months.

## Scope

Covers the container's life from creation to archive, and its statuses.

**Not covered:**
- how a container is created from a packing list — [allocation-workbench.md](allocation-workbench.md)
- Amazon's counting and shortfall — [receiving.md](receiving.md)
- when the payment lands in the ledger — cashflow.md *(pending)*

## Business workflow

```
Allocation workbench → Container (in transit) → Receiving → Container history
                            ↓                       ↓
                      Cash-flow outflow      Warehouse stock
```

A container is created by uploading a packing list. Its units are drawn from
open production plans and count as inbound in the planner from that moment.
When Amazon starts counting the container in, it moves to Receiving; when
Amazon closes the shipment, it moves to history and stops being planned for.

## Actors

| Actor | Does |
|---|---|
| Ops | creates the container, sets dates, enters the Amazon shipment ID |
| Amazon | counts units in, reports status, closes the shipment |
| The system | polls Amazon twice daily, advances stages, prices the payment |

## Business rules

1. A container belongs to a **region**. Its FNSKUs, destination and cash-flow
   ledger are all region-specific.
2. A container is **not owned by a supplier**. Ownership lives on each line via
   the PO line the units were drawn from, so one container may carry goods from
   several factories. Its vendor is *derived* from those lines.
3. A line's **packed quantity is the truth** — what left the factory. What was
   declared to Amazon when labels were generated is a separate figure and is
   never used to compute loss. See `INV-D-001`.
4. **Declared ≥ packed, always.** We declare at least what we pack.
5. Each line carries a **FOB rate per unit, in the region's currency**, captured
   when the container is created and never re-read from the PO. See `INV-D-004`
   and `INV-D-005`.
6. A container appears in **exactly one** of In Transit, Receiving or History.
   Never two. See `INV-D-009`.
7. Units on a container count as **inbound** to the planner until Amazon counts
   them; only the un-received remainder is inbound after that.
8. Containers bound for a **fulfilment centre** are netted out of Amazon's own
   inbound figure, because Amazon reports the same cartons and the container is
   the more detailed record.
9. **Re-uploading a packing list replaces the container's lines.** Always send
   the complete list. See `INV-D-010`.
10. Deleting a container **releases its units** back to their production plans;
    the outstanding PO balance rises by exactly what was on it.
11. The app **books no financial loss**. Units short stay on the line as packed
    minus counted, for the COGS system to value. See `INV-D-008`.

## States

Active — the container still has units we are waiting for:

| State | Meaning | Entered when | Leaves when |
|---|---|---|---|
| Production complete | made, not collected | set by ops | pickup |
| Waiting for pickup | awaiting collection | set by ops | collected |
| In transit | moving inland to port | set by ops | loaded |
| On vessel | at sea | set by ops | arrival |
| At port | arrived, not cleared | set by ops | cleared |
| Customs clearance | with customs | set by ops | released |
| Inland transit | port to warehouse | set by ops | out for delivery |
| Out for delivery | final leg | set by ops | Amazon receipt |
| **Receiving** | Amazon is counting it in | first unit counted | Amazon closes |

Terminal — no longer planned for:

| State | Meaning | Entered when |
|---|---|---|
| Received | Amazon closed the shipment, or ops confirmed receipt | automatic on close |
| Cancelled | abandoned | set by ops |

`pending` and `departed` are legacy values, treated as active.

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Create | ops | a packing list with SKU, supplier, units and FOB | container with lines; PO balances drop |
| Edit | ops | container exists | dates, destination, status, Amazon shipment ID |
| Add Amazon shipment ID | ops | container exists | receipts sync can find it |
| Delete | ops | container exists | container and lines removed; PO balances rise |
| Mark received | ops | active container | archived; 3PL stock increased |
| Re-upload packing list | ops | same container number | **lines replaced**, not added |

## System behaviour

- Twice daily the receipt syncs poll Amazon for every container carrying a
  shipment ID, and record what Amazon has counted. See [receiving.md](receiving.md).
- A container with any counted units belongs to Receiving, regardless of its
  status field. This is derived, not stored — which is why a container whose
  status was never advanced still appears in the right place.
- When Amazon reports the shipment CLOSED, the container becomes *received* and
  moves to history.
- The container's payment is written to the region's cash-flow ledger as
  `Σ(units × FOB) + freight`, dated on the port ETA less the payment lead.
- Vendor and PO number on the container are derived from the lines' PO lines and
  sorted, so the same pair of factories always produces the same label.

## Data model

- **Container** — one physical shipment. Carries a region, a destination
  warehouse, dates, a status and an Amazon shipment ID.
- **Container line** — one SKU on one container. Carries the packed quantity,
  the FOB rate, the PO line the units came from, and Amazon's declared and
  counted figures kept in separate fields so neither overwrites the other.
- **Production plan** — the open manufacturing commitment a line draws from.

## Integrations

| System | Direction | What moves |
|---|---|---|
| Amazon AWD | in | shipment status and per-SKU counts, in **cases** |
| Amazon FBA Inbound | in | shipment status and per-SKU counts, in **eaches** |

The unit difference is not cosmetic: applying the AWD case conversion to an FBA
payload multiplies every figure by the case pack.

## Dependencies

Draws on purchase orders for balances, and the allocation workbench for
creation. Feeds cash flow, the planner and [receiving](receiving.md).
*(purchase-orders.md, allocation-workbench.md, cashflow.md and planner.md are
all pending.)*

## Edge cases

- **No Amazon shipment ID.** The receipt syncs cannot find the container, so it
  never advances and sits in transit past its ETA with no signal. The commonest
  cause of a "stuck" container, and currently true of every active one —
  `INV-RECV-001`.
- **Amazon declares more than we packed.** Normal, and not a loss — Amazon
  reconciles against its own declared figure, so its discrepancy report will
  look worse than reality.
- **Amazon counts more than we packed.** A pack-size disagreement, not a gain.
  Reported separately from shortfall because the remedy differs.
- **Received without a human count.** An auto-closed container has only Amazon's
  figure; history reads that rather than treating the container as a total loss.
  See `INV-D-006`.
- **Two suppliers, one container.** Normal — 17 of 131 containers on record.
  One packing list carries both, each row naming its supplier.

## Known gaps

- `INV-CONT-001` — 188 in-transit lines carry no FOB rate
- `INV-CONT-002` — opening balance is not consumable
- `INV-CONT-003` — no stall alert for a container stuck in Receiving
- `INV-CONT-004` — goods receipt writes AWD stock the sync then overwrites
- `INV-CONT-011` — the status-workbook import deletes every container in the region
- `INV-ALLOC-003` — the container-manifest import strips FOB and PO attribution
- `INV-RECV-001` — no active container carries an Amazon shipment ID
- `INV-RECV-002` — archived containers with no count report as a total loss
- `INV-RECV-003` — per-SKU variance views ignore Amazon's count

## Related decisions

`INV-D-001` `INV-D-004` `INV-D-005` `INV-D-006` `INV-D-008` `INV-D-009` `INV-D-010`

## Related documents

- [receiving.md](receiving.md) — what Amazon counted and what is short
- [allocation-workbench.md](allocation-workbench.md) — how a container comes into being
- cashflow.md *(pending)* — when its payment falls due
- [architecture-mismatches.md](../architecture-mismatches.md) — `ARCH-005`
