# Catalogue

files: `apps/dashboard/views.py` — `catalog`, `product_form`
verified against: `82744aa` · 2026-08-06

The product record: what a SKU is, what it is called, which ASIN it belongs to,
and how it groups.

## Purpose

A SKU string appears in orders, adverts, packing lists, purchase orders and
cost uploads. The catalogue is what turns it into a product — a name, an ASIN, a
type and a pack size — everywhere it appears.

That makes it the quiet dependency of almost every other section. A product
missing here does not break; it simply falls out of every grouping.

## Business rules

1. **A SKU implies its category, name and FNSKU.** Uploads across the
   application ask for the SKU only, and derive the rest — a typed value on an
   older file still wins. This is a project-wide invariant; see `INV-D-003`.
2. **Product type and pack size come from the title**, split on a separator.
   That derived pair is the grouping every report and target uses.
3. **A product belongs to a marketplace.** The same SKU in two marketplaces is
   two records, because its ASIN and FNSKU differ.
4. **A product is deactivated rather than deleted**, so historical rows keep
   their identity.

## Edge cases

- **A title that does not split.** The product groups alone rather than
  disappearing — see [reporting/product-performance.md](../reporting/product-performance.md).
- **A SKU in orders and not in the catalogue.** Appears in reports keyed by SKU
  or ASIN with whatever is known; revenue is never dropped for want of a
  catalogue row.
- **A SKU with no cost record.** Shows without a margin rather than with a zero
  one — see [financials/cogs.md](../financials/cogs.md).

## Observations — not gaps

*Source: local development data; provisional.* 494 products across four
marketplaces.

## Related decisions

From Inventory: `INV-D-003`

## Related documents

- [reporting/product-performance.md](../reporting/product-performance.md) — the grouping this drives
- [financials/cogs.md](../financials/cogs.md) — the costs attached to these products
