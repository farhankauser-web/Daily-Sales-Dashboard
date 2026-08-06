# Purchase orders

files: `apps/inventory_planning/{procurement,views,models,reorder}.py`
       `templates/inventory_planning/{purchase_orders,sourcing,suppliers}.html`
verified against: `873b658` · 2026-08-06

A purchase order is our commitment to a factory: what we ordered, at what agreed
rate, and how much of it has not yet shipped. Every inventory movement traces
back to one.

## Purpose

The PO is the **source of every balance** in the section. The supplier ledger is
its lines aggregated one way; the sourcing view is the same lines aggregated by
product; a packing list is spent against them; wastage closes them. It also
carries the **agreed FOB per category** — the price the money figures on the
supplier side are built from.

Production plans hang off it: one plan per product category on the PO, numbered
PP-1, PP-2 …, tracking the commitment from order to container. There is no live
feed from the factories, so a plan moves only when units are allocated to a
container or received.

## Scope

Covers the PO workbook import, the order → group → line → plan structure,
balances, wastage, short-closing, regional reservations and the sourcing views.

**Not covered:**
- how balance is consumed by a packing list — [allocation-workbench.md](allocation-workbench.md)
- the supplier registry the PO belongs to — [suppliers.md](suppliers.md)
- what to buy, and how a draft PO is proposed — [reorder.md](reorder.md)
- what to ship on the next container — [loading-plan.md](loading-plan.md)
- what happens after units ship — [containers.md](containers.md), [receiving.md](receiving.md)

## Business workflow

```
Ops PO workbook (.xlsx) → Supplier → PO → Category groups (FOB agreed)
                                              ↓
                                    SKU lines → Production plans (PP-1, PP-2 …)
                                              ↓
              balance = ordered − wastage − allocated   →   drawn by packing lists
```

The workbook mirrors how ops and the factory actually agree an order: a
**Summary sheet** of category totals with the FOB rate per category, and a
**Production Plan sheet** of the SKU (colour/variant) rows inside them. The
import reconciles the two and reports any category whose SKU rows do not sum to
its Summary total.

## Business rules

1. **The category group is where the price lives.** FOB is agreed per category,
   not per SKU; every SKU line prices at its group's rate, **in the supplier's
   currency**. This figure never touches a container — that one is the region's
   (`INV-D-004`).
2. **A line's balance is ordered − wastage − allocated.** Received is a fact
   about transit, not a component of balance: allocation is what spends a PO.
3. **Wastage closes balance permanently.** We do not pay for factory-fault
   units and the factory does not remake them. See `INV-D-016`.
4. **Wastage lands FIFO** — oldest PO first within its scope — the same draw
   order as allocation (`INV-D-014`).
5. **One production plan per category on the PO**, numbered PP-1, PP-2 … in
   workbook order. The plan is derived from its group and carries no progress of
   its own.
6. **Re-importing a PO replaces its lines only while nothing is allocated.**
   Once a container has drawn from it, re-import is refused — reshaping a PO
   under a live allocation would orphan the attribution.
7. **A PO closes when every line closes.** Short-closing a line realises its
   unallocated remainder as **production shortage** — a factory that made
   less than ordered — distinct from transit shortage, which is units lost
   after shipping.
8. **Open balance can be reserved to a region.** A reservation subtracts from
   available-to-promise so two regions' loading plans cannot promise the same
   units. It reserves; only allocation spends.
9. **A PO belongs to a supplier that already exists.** See `INV-D-015` — the
   upload does not yet enforce this (`INV-SUP-004`).

## States

The order:

| State | Meaning |
|---|---|
| Draft | proposed by the reorder engine, not yet sent to the factory |
| Open | committed; lines still carry balance |
| Partially / fully allocated | derived milestones as packing lists draw it down |
| Closed | every line closed or short-closed |
| Cancelled | abandoned; its lines stop counting everywhere |

A line additionally distinguishes **closed** (fully allocated) from
**short-closed** (remainder realised as production shortage).

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Import a PO workbook | ops | supplier, PO number, the .xlsx | order, groups, lines and plans created; discrepancies reported |
| Re-import | ops | nothing allocated yet | lines replaced; refused otherwise |
| Upload wastage | ops | supplier chosen; PO optional | balance reduced FIFO; unmatched remainder reported by SKU |
| Short-close a line | ops | line open | remainder realised as production shortage |
| Bulk short-close | ops | lines with nothing left in transit | same, across a supplier or PO |
| Reserve balance to a region | ops | line has unreserved balance | available-to-promise falls for everyone else |
| Browse sourcing | anyone | — | product → SKU → every supplier's open balance and history |

## System behaviour

- Categories, product names and FNSKUs are **derived from the SKU** wherever the
  workbook omits them; a typed value on an older file still wins, and a SKU the
  catalogue does not know is reported back, never left silently blank
  (`INV-D-003`).
- The import **finds its header rows** rather than assuming positions, so the
  ops file's layout can drift without breaking.
- Sourcing ranks a SKU's suppliers: those holding open balance first (allocate
  from these), then past suppliers by average FOB (candidates for the next
  order).
- The reorder engine proposes **draft POs** grouped per supplier from approved
  suggestions. Drafts are proposals: they carry no balance the business acts on
  until purchasing confirms them. See [reorder.md](reorder.md).

## Data model

- **Purchase order** — the commitment: supplier, number, dates, terms, status.
- **Category group** — one product category on the order, carrying the agreed
  FOB rate and the Summary totals.
- **PO line** — one SKU inside a group; the grain a packing list ships at, so
  allocation, wastage and shortage all land here.
- **Production plan** — the manufacturing view of one group: PP number and
  expected-ready date. Everything else it reports is derived from its lines.
- **Reservation** — a regional claim on a line's open balance; spent only by
  allocation.

## Validation rules

| Input | Rule | On failure |
|---|---|---|
| Workbook | a Summary sheet with Category + Units, and a Production Plan sheet with SKU | refused, naming the missing sheet or header |
| Category totals | SKU rows sum to the Summary figure | imported; discrepancy reported per category |
| SKU | known to the catalogue | imported; reported back as uncategorised |
| Re-import | no units allocated | refused, stating the allocated count |
| Wastage row | open balance exists in scope | applied up to balance; remainder reported by SKU |

## Edge cases

- **A SKU row whose category has no Summary row.** Imported into a synthetic
  group at zero ordered units and reported — the units are real even though the
  Summary missed them.
- **Wastage exceeding open balance.** Applied up to the balance; the remainder
  is reported, not forced — over-reported wastage is a file error, not a fact.
- **A cancelled PO.** Its lines stop counting toward every balance and ledger;
  nothing is deleted.
- **The same SKU open with two suppliers.** Normal, and why every draw — 
  allocation, wastage, sourcing — is scoped to one supplier before it is FIFO.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INV-SUP-004` | the PO upload takes free text for the supplier and mints one on a typo | bug |
| `INV-SUP-002` | `POLineGroup.pcs` is written and never read | legacy schema |
| `INV-CONT-002` | opening balance is not consumable, so it never shields PO balance | missing implementation |
| `INV-RECV-003` | shortage properties read the human count alone — auto-closed containers overstate transit shortage | bug |

## Related decisions

`INV-D-003` `INV-D-011` `INV-D-014` `INV-D-015` `INV-D-016`

## Related documents

- [suppliers.md](suppliers.md) — the registry and the ledger built from these lines
- [allocation-workbench.md](allocation-workbench.md) — how balance becomes a container
- [reorder.md](reorder.md) — where draft POs come from
- [loading-plan.md](loading-plan.md) — what reservations serve
