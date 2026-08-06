# Product performance

files: `apps/amazon_api/views.py` — `_build_cached_skus`, `fetch_dashboard_data`
       `apps/dashboard/sync.py` — `net_factor`
verified against: `edcec31` · 2026-08-06

The SKU table: every product grouped by type and pack size, with revenue, cost,
fees, ad spend and the margin that falls out of them. The most consequential
table in the application.

## Purpose

Revenue alone does not say which products are worth selling. This table puts
the whole per-unit economics on one row — what it sold for, what it cost to
buy, what Amazon took, what advertising cost — and computes contribution margin
from them.

It is the table every pricing, sourcing and advertising decision is argued from,
which is why the rules below are stated as invariants rather than as behaviour.

## Scope

Covers the table's construction, its grouping, and the attribution rules that
decide what appears in each cell.

**Not covered:**
- the headline figures above it — [daily.md](daily.md)
- how campaign spend becomes per-SKU spend — [sku-allocation.md](../marketing/sku-allocation.md)
- historical range analysis against targets — historical.md *(pending)*

## Business rules

1. **Margins are measured on revenue ex-VAT.** Revenue is displayed as the
   customer paid it; every ratio divides by revenue ÷ (1 + rate). This has
   caused a real error: the numerator was already ex-VAT while the denominator
   was gross, reading UK margin roughly nine points too flattering.
2. **Rows are grouped by product type and pack size**, derived by splitting the
   product title. The group is the unit a buying decision is actually made at.
3. **Ad spend comes from the allocator where it exists.** Its output reconciles
   to campaign spend exactly and carries an attribution source and confidence.
   Where it has not run for a date, the table falls back deliberately rather
   than showing zero. See `REP-D-002`.
4. **Today and past days attribute advertising differently, and must.** Amazon's
   per-ASIN advertising report lags a day, so today's ad spend can only be
   attributed at group level from campaign names; past days use the per-ASIN
   actuals. Each row records which of the two it is.
5. **Spend that cannot be attributed is shown as its own row**, never spread
   across SKUs. See `MKT-D-001`.
6. **Amazon Renewed and Warehouse listings carry no ad spend**, because we do
   not advertise them. See `MKT-D-004`.
7. **A SKU with no cost record still appears**, with the costs it has. Hiding it
   would hide revenue.

## The canonical builder

One function builds this table for every cached path. It is the only one that
implements rules 1, 3, 4 and 5 — which is what makes it canonical rather than
merely first.

A separate live-Amazon fallback path builds its own version when neither cached
tier can serve the window. It implements rule 1 and not the others, so its ad
spend columns read zero. That path is the straggler in `ARCH-007`, and the
recommendation there is to point it at the canonical builder rather than to
write a third.

**`product_line_analysis` is not this table.** It answers a different question —
per-product-group P&L over a historical range against monthly targets, from the
all-orders report. Same shape, different purpose. See historical.md *(pending)*.

## Data model

- **Daily SKU snapshot** — one per marketplace, date and SKU: units, revenue,
  cost of goods, Amazon fee, fulfilment fee and the margins derived from them.
- **Product** — the catalogue row carrying the title the grouping is derived
  from, and the SKU ↔ ASIN relationship.
- **SKU ad cost** — supplied by [sku-allocation.md](../marketing/sku-allocation.md),
  not computed here.

## Edge cases

- **A product whose title does not split into type and pack.** Falls back to the
  title, then to the SKU, so it groups alone rather than vanishing.
- **A SKU present in orders but absent from the catalogue.** Appears keyed by
  SKU or ASIN with whatever is known; revenue is never dropped for want of a
  catalogue row.
- **A window spanning today and past days.** Ad attribution differs within the
  same table, by rule 4. The per-row source is what makes that legible.
- **A group whose ad spend is entirely unattributed.** Its SKU cells read zero
  while the group carries spend — correct, and the reason the unallocated row
  exists.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `REP-PROD-001` | the live fallback path builds its own table without the allocator, ex-VAT-aware PPC or the unallocated row | bug |

## Architecture mismatches

`ARCH-007` — one canonical builder, one straggler, and one machine that was
wrongly counted as a third. `ARCH-004` — the canonical builder lives in an app
named for an integration.

## Related decisions

`REP-D-002` · and from Marketing: `MKT-D-001` `MKT-D-004`

## Related documents

- [daily.md](daily.md) — the page this table sits on
- [sku-allocation.md](../marketing/sku-allocation.md) — where the ad cost comes from
- [architecture-mismatches.md](../architecture-mismatches.md) — `ARCH-007`
