# SSOT — INV-REPACK-001: Factory repack (assembled SKU draws down its base SKU)

**Decision:** accepted. Build a pack-assembly (BOM) map so allocating an
assembled SKU deducts its base SKU's PO/opening balance at ratio:1.
**Date:** 2026-08-13 · **Owner:** godmode_lead_architect · **Status:** built

## Intent (from Farhan)
- We place POs and carry opening balances in the **base** (procurement) SKU:
  kitchen towels as a **pack of 6**, wash cloths as a **pack of 12**.
- The factory repacks 2 base packs into 1 **assembled** (retail) SKU: the
  **pack of 12** towel, the **pack of 24** wash cloth. The assembled SKU is what
  ships to and sells on Amazon (it holds the FNSKU).
- So shipping `100 × TW-BLK-KTH-12` must consume `200 × TW-BLK-KTH-6` from the
  base PO/opening balance. Ratio is **always 2**.
- Drawdown is a **factory-level** concern only. Sales and demand forecasts stay
  attributed to the assembled SKU — this change does NOT touch them.

## Model
- New `PackAssembly(assembled_sku unique, component_sku, component_per_pack=2,
  active, note)`. Explicit map, **not** string-derived — naming is inconsistent
  (a `-12` suffix for towels, a `-24-` infix for wash cloths). Admin-editable.
- `InTransitLine.source_units` (nullable): base units drawn from the linked
  `po_line`/`opening_balance`. Equals `units` for a direct line; `units × ratio`
  for a repack line. NULL = legacy/direct line.
- Helper props on `InTransitLine`: `drawn_units` (source_units or units),
  `draw_ratio`, `drawn_received_units` (receipt scaled to base units).
- `POLine` and `SupplierOpeningBalance` rollups (`allocated_units`,
  `loaded_units`, `received_units`, `receipt_variance`, `transit_shortage`,
  `over_receipt`) now read `drawn_units`/`drawn_received_units`, so a base line
  reconciles in base units. Direct lines are unchanged (ratio 1).

## Allocation engine (procurement.py)
- `pack_assembly_index()` → {ASSEMBLED (upper): (component_sku, ratio)}.
- `preview_packing_list` per row: if the row SKU is assembled, draw order is
  **(1) its own finished-goods backlog first** (already-assembled opening/PO,
  ratio 1), **then (2) the base component** (opening-first then PO FIFO,
  ratio N). `avail` is exposed in **assembled units** (`base_remaining // ratio`,
  whole packs only); each alloc carries `source_units = take × ratio` and
  `ratio`/`comp_sku`.
- A repack row adds a warning: "`100 × X-12 draws 200 × X-6 …`".
- `commit_packing_list`: merge sums `source_units`; the open-balance guard
  compares the **base draw** against the base line's remaining; the written
  `InTransitLine` keeps the assembled `sku`/`units` (for FNSKU + region
  inventory) and stores `source_units` as the base drawdown.

## Invariants kept
- FNSKU/region gate runs on the **assembled** SKU (the base is never listed on
  Amazon) — unchanged, correct.
- Supplier resolution and the PO-number filter now apply to the component
  lookup; behaviour otherwise identical.
- A base SKU shipped directly (as a 6-pack) still works with no repack logic; a
  base PO can be drawn by both direct and repack shipments — the unified
  `source_units` rollup handles both.
- No reverse: shipping a base SKU never draws down assembled stock (confirmed).

## Seed (migration 0014, ratio 2)
Kitchen towels 6→12: BLK, GRY, GRN, BLU, YEL, RED.
Wash cloths 12→24: NBL, DGY, WHT, SND, LGY, TEL, PUR, BLU, RED.

## Verification
`manage.py check` · migration applies + seeds 15 rows · rolled-back shell test:
repack row deducts ratio×units from the base line, base `remaining_units` drops
by the base amount, over-allocation guard fires in base units, own
finished-goods drawn before base, a plain non-kitted SKU is untouched.
