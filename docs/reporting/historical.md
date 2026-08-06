# Historical

files: `apps/dashboard/views.py` — `historical`, `product_line_analysis`
       `apps/dashboard/sync.py` — `sync_window`, `apply_ppc_from_snapshots`
       `templates/dashboard/historical.html`
verified against: `b70e2c2` · 2026-08-06

Trends over weeks and months, and per-product-group performance against target.
The view for questions that a single day cannot answer.

## Purpose

The daily dashboard answers "how is today". This answers "is the trend good" —
whether a product line is growing, whether margin is holding as volume rises,
whether a month will hit its target with a week to go.

## Scope

Covers the multi-day view, its backfill, and the product-line analysis against
monthly targets.

**Not covered:**
- today and short windows — [daily.md](daily.md)
- the SKU table's construction — [product-performance.md](product-performance.md)
- reconciliation to money received — [financials](../financials/README.md)

## Business rules

1. **Historical ends at yesterday.** Today is never shown here, because a
   partial day in a trend line reads as a decline. See `REP-D-005`.
2. **A missing day can be backfilled on demand**, for a chosen number of days,
   rather than waiting for a scheduled run.
3. **Advertising is applied from stored snapshots**, not re-fetched, so a
   backfill of orders does not silently drop the ad spend already recorded
   against those days.
4. **Days missing advertising data are identified rather than assumed
   complete** — the view knows which days in its window have no ad figures.
5. **Product-line analysis is measured against monthly targets pro-rated to the
   selected range**, so a part-month comparison is fair.
6. **Margins are measured on revenue ex-VAT** — see
   [product-performance.md](product-performance.md).

## Product line analysis is its own machine

It answers a different question from the SKU table: per-product-group profit and
loss over a chosen historical range, against monthly targets, sourced from
Amazon's all-orders report rather than the daily snapshots.

It shares a shape with [product-performance.md](product-performance.md) and not
a purpose. `ARCH-007` previously counted it as a duplicate builder of that
table; it is not, and consolidating the two would merge two machines that should
stay separate. If it duplicates anything, it duplicates the Financials P&L.

## Data model

- **Daily metric** — the settled per-day record the trend is built from.
- **Monthly target** — per marketplace, month, product type and pack size.

## Edge cases

- **A window containing days that were never synced.** Those days are absent
  from the trend rather than plotted as zero, and the backfill control exists to
  fill them.
- **A backfill over a period that already has advertising data.** The stored ad
  figures are re-applied rather than lost.

## Observations — not gaps

*Source: local development data; provisional.*

- **Daily metrics stop at 2026-07-25.** Expected — the syncs run only in
  production. A trend ending there is this machine being switched off, not a
  break in it.

## Related decisions

`REP-D-005`

## Related documents

- [daily.md](daily.md) — the short-window view
- [product-performance.md](product-performance.md) — the SKU table, and why this is not it
- [architecture-mismatches.md](../architecture-mismatches.md) — `ARCH-007`
