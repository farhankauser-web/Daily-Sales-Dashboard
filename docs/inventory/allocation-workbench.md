# Allocation workbench

files: `apps/inventory_planning/{procurement,views,importer}.py`
       `templates/inventory_planning/allocation.html`
verified against: `86d5f35` · 2026-08-06

The workbench turns a factory's packing list into a container, drawing every
unit off the purchase order that paid for it. It is the only place where
physical goods and procurement meet.

## Purpose

Before a container exists, units are a commitment on a purchase order. After it
exists, they are stock on the water. The workbench is the moment of conversion,
and it is the only path that records **which PO line each unit came from**.
Without that attribution a container is a list of SKUs: it cannot be priced for
cash flow, cannot draw down a supplier's balance, and cannot tell you which
factory owes you what.

Everything downstream depends on getting this right once, because it is
snapshotted and never re-derived (`INV-D-005`).

## Scope

Covers the packing list, its validation, and the allocation it produces.

**Not covered:**
- the container's life once created — [containers.md](containers.md)
- Amazon's count against the packed figure — [receiving.md](receiving.md)
- where the open balance comes from — [purchase-orders.md](purchase-orders.md)
- what the FOB rate does once captured — [cashflow.md](cashflow.md)

## Business workflow

```
Packing list (.xlsx) → Preview: resolve every row → Confirm → Container + lines
                              ↓                                      ↓
                       errors block, warnings do not          PO balances drop
```

Two steps, deliberately. The preview resolves every row against open purchase
orders and writes nothing; the confirm step writes everything in one
transaction. An operator sees every problem before any of it is real.

## Actors

| Actor | Does |
|---|---|
| Ops | downloads the template, fills the packing list, uploads, reviews, confirms |
| The system | resolves rows to PO lines, prices the container, refuses what it cannot attribute |

## Business rules

1. **One packing list describes the whole container**, including one loaded from
   two factories. Each row names its own supplier. See `INV-D-002`.
2. **The row's supplier wins over anything the form says.** A name not on record
   is refused, with near-matches offered — a typo must not create a duplicate
   factory.
3. **Anything derivable from the SKU is derived** — product name and FNSKU. A
   typed name still wins, so older files read as before. See `INV-D-003`.
4. **FOB per unit is required, in the region's currency**, and is never inferred
   from the PO. See `INV-D-004`.
5. **The rate is snapshotted onto the line** at confirm and never re-read. See
   `INV-D-005`.
6. **Units draw FIFO — oldest purchase order first** — from that row's own
   supplier. A PO number on the row makes the match exact instead. See
   `INV-D-014`.
7. **A row that cannot be attributed is left unallocated**, never attributed to
   whatever the form said. A wrong attribution draws down the wrong factory's
   balance and looks correct doing it.
8. **Nothing is written until the preview is clean.** Errors block the confirm
   button; warnings inform and do not.
9. **Re-uploading a container number replaces its lines**, releasing the previous
   allocation first so the container does not compete with itself. See
   `INV-D-010`.
10. **A container is never owned by a supplier.** Ownership lives on each line
    via its PO line, which is what lets two factories share one container.
11. **Vendor and PO number on the container are derived** from the PO lines the
    units actually came from, and sorted — so the same pair of factories always
    produces the same label.

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Download the template | ops | a region | .xlsx with live Supplier, SKU and open-PO dropdowns |
| Preview a packing list | ops | a file with SKU and Units | every row resolved, priced and checked; nothing written |
| Confirm | ops | a preview with no errors | container created or replaced; PO balances drop |
| Override the PO on a row | ops | the row resolved to a supplier | draws from a chosen PO of **that row's own supplier** |

The template is generated at download time from live data, so a supplier or
purchase order added five minutes earlier is already in its dropdowns. The
reference lists stay visible rather than hidden: when a dropdown does not offer
what you expect, the fastest answer is to read the list it was built from.

## System behaviour

- **The preview writes nothing.** It resolves, prices, validates and reports.
- **Units are consumed oldest-PO-first** within the row's supplier, splitting
  across purchase orders where one cannot cover the row. A split is a warning,
  because it is normal but worth seeing.
- **Duplicate (SKU, PO line) pairs merge** into one container line. The same SKU
  drawn from two different PO lines stays two lines, so each factory's
  attribution survives.
- **The whole confirm is one transaction.** A refusal part-way leaves nothing
  written.
- **Capacity is checked, not enforced.** Total CBM against 33 · 67 · 76 for
  20 ft, 40 ft and 40 ft HC — over capacity is a warning, because the operator
  can see the container and the spreadsheet cannot.

## Data model

- **Packing-list row** — one SKU as packed: units (or boxes × per-box), CBM, the
  supplier, an optional PO number, and the FOB rate.
- **Allocation** — the resolved link from a row to one PO line, for a quantity.
  One row may produce several when it spans purchase orders.
- **Container line** — what an allocation becomes: the packed quantity, the PO
  line, the FNSKU applied for that region, and the snapshotted rate.

## Validation rules

| Input | Rule | On failure |
|---|---|---|
| SKU | listed for the region **and** carrying an FNSKU | refused by SKU — nothing can be labelled without one |
| Supplier | matches a supplier on record, per row | refused by name, with near-matches suggested |
| FOB | present and not negative, in the region's currency | refused by SKU, naming the currency expected |
| Units | greater than zero, or boxes × per-box | row skipped silently — this is how a subtotal line is ignored |
| Open balance | the supplier has enough un-allocated PO units | over-allocation error stating the shortfall |
| PO number | optional; when given, must be that supplier's | falls back to FIFO with a warning |
| Container number | required at confirm | commit refused |
| Total CBM | within the container size | **warning only** |

## Edge cases

- **Two factories, one container.** Normal. One file, each row naming its
  supplier; the container's vendor becomes both, sorted.
- **A corrected packing list re-uploaded.** The container's own allocation is
  released back into the open balance before the check, so it does not read as
  competing demand. Without that, correcting a file reported a false
  over-allocation.
- **A SKU open on two suppliers' POs.** Common. The PO-override list loads every
  supplier's open plans but filters to the row's own, so a line cannot be
  reassigned onto the wrong factory's purchase order.
- **A row with no supplier anywhere** — not in the file, not on the form. Refused
  by name rather than attributed to a default.
- **A SKU with no FNSKU for the region.** Refused: there is nothing to label the
  cartons with. On the development snapshot 11 of 224 USA SKUs and 3 of 169 for
  AE and SA are in this state — *provisional, dev-snapshot only; re-measure on
  production.* The refusal itself is correct behaviour, not a gap.

## How a container can come into being

Three paths create containers, and only one of them records attribution. This
matters more than it looks: a container from either import path can never be
priced for cash flow or drawn against a PO balance, because the fields were
never populated and are not re-derivable.

| Path | Exposed in | Records PO line, FOB, FNSKU |
|---|---|---|
| **Allocation workbench** | Allocation page | **yes** |
| Container manifest import | Containers, Planner | no — and replaces the lines of any container it names (`INV-ALLOC-003`) |
| Status workbook import | not exposed | no — and deletes every container in the region first (`INV-CONT-011`) |

*Source: code.* On the development snapshot, 0 of 2,615 container lines carry a
rate, a PO link or an FNSKU, so no container in that database came through the
workbench — *provisional; re-measure on production.*

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INV-CONT-002` | ~~opening balance is not consumable~~ — **built 2026-08-10**: packing lists draw opening backlog first (oldest), then PO FIFO | resolved |
| `INV-ALLOC-003` | the container-manifest import strips FOB and PO attribution from the lines it replaces | bug |
| `INV-CONT-011` | the status-workbook import deletes every container in the region | bug |
| `INV-ALLOC-004` | append mode is unreachable, and its docstring still presents it as the two-supplier route | bug — stale documentation |

## Related decisions

`INV-D-002` `INV-D-003` `INV-D-004` `INV-D-005` `INV-D-010` `INV-D-011` `INV-D-014`

## Related documents

- [containers.md](containers.md) — what the container does once it exists
- [receiving.md](receiving.md) — the packed figure every count is measured against
- [purchase-orders.md](purchase-orders.md) — where the open balance comes from
- [cashflow.md](cashflow.md) — what the snapshotted rate pays for
