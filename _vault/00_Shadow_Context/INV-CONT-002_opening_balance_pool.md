# SSOT — INV-CONT-002 + INV-SUP-001: Opening balance becomes an allocatable, priced supply pool

**Decision:** INV-D-011 (accepted). Build the two-tier supply pool + opening-balance FOB rate.
**Date:** 2026-08-10 · **Owner:** godmode_lead_architect · **Status:** built

## Intent (from the project's own SSOT)
- A packing list draws from **opening balance first (oldest as_of), then PO lines FIFO** (INV-D-011).
- Mirror the PO-line pattern exactly: `remaining = units − allocations` (counting, not a decrementing counter).
- A container line links to the opening balance it drew from.
- Re-uploading an opening balance already drawn against is **refused**, not replaced.
- Opening balance carries an optional **FOB rate** so Outstanding FOB no longer understates (INV-SUP-001).

## Currency invariant (unchanged)
- **Container line FOB** = the packing-list row price, in the **region's** currency (unchanged).
- **Opening-balance fob_rate** = the **supplier's** currency, feeds only the supplier ledger's Outstanding FOB — never mixed into container value. Same rule as PO fob_rate vs container FOB.

## Model (migration)
- `SupplierOpeningBalance`: add `fob_rate` (Decimal, default 0). Add props mirroring POLine:
  `_live_allocations()`, `allocated_units`, `received_units`, `remaining_units`, `fob_value`.
- `InTransitLine`: add nullable `opening_balance` FK (SET_NULL, related_name `allocations`).
  A line points to **either** `po_line` **or** `opening_balance` (or neither = legacy).

## Allocation engine (procurement.py)
- Draw order per packing-list row:
  - Opening balances (as_of asc) are ALWAYS drawn first, then PO lines (order_date asc).
  - A PO number on the row only narrows the PO side to that specific PO (INV-D-014);
    it never excludes opening balance. (Fixed 2026-08-10 — the earlier "PO number
    bypasses opening" behaviour blocked rows that had opening backlog.)
- `_remaining_ob(ob, release_container_id)` mirrors `_remaining_for`.
- Preview alloc entries carry a `src` = `opening` | `po` and the source id.
- Commit merges by `(sku, src, src_id)`, over-alloc guard reads the right source's remaining,
  writes `InTransitLine(opening_balance=…)` or `(po_line=…)`; vendor derived from both.

## Display
- `opening_by_category` → uses **remaining** (units − allocations), not raw units.
- Supplier list + category drilldown: opening remaining flows into Balance (as suppliers.md rule 3 states).
- PO detail: opening backlog for the PP's category shown as a labelled read-only block (drawn first);
  not added to per-PO-line balance to avoid double counting.

## Edge cases
- Legacy allocations (po_line null, opening null) untouched.
- Cancelled containers release both PO and opening remaining (via `_live_allocations`).
- Re-upload guard: refuse if any row for (supplier, as_of) has live allocations.

## Verification
manage.py check · migration applies · rolled-back test: opening draws before PO, remaining
decrements, over-allocation blocked, re-upload guard fires, fob_rate persists.
