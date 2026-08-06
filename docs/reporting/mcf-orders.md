# MCF orders

files: `apps/dashboard/views.py` — `mcf_orders`, `mcf_export_csv`
       `templates/dashboard/mcf_orders.html`
verified against: `b70e2c2` · 2026-08-06

A read-only mirror of Amazon's Multi-Channel Fulfilment order list: orders
Amazon fulfilled for us from stock held in its warehouses, sold somewhere other
than Amazon.

## Purpose

MCF revenue does not appear in Amazon sales reporting, because the sale did not
happen on Amazon — only the fulfilment did. Without this mirror, stock leaves
the warehouse with no visible order behind it.

## Scope

Covers the mirrored order list, its filters and its export.

**Not covered:**
- creating MCF orders from Walmart sales — [walmart](../walmart/README.md), `ARCH-006`
- the stock those orders consume — [containers.md](../inventory/containers.md)

## Business rules

1. **This is a mirror, not a source.** Orders are created in Amazon or by the
   Walmart pipeline; nothing here creates or modifies one.
2. **An order belongs to the marketplace that fulfilled it**, which is where the
   stock was held — not where the sale was made.
3. **The list is exportable**, because reconciling MCF against another channel's
   own records is a spreadsheet job.

## Naming

**"MCF" means two different things in this application**, and they are unrelated
machines:

| | Owns |
|---|---|
| **MCF Orders** — this document | a read-only mirror of Amazon's MCF order list |
| **Walmart Fulfilment** — [walmart](../walmart/README.md) | the pipeline that *creates* MCF orders from Walmart sales |

`ARCH-006` records the naming collision. A bug reported against "MCF" has
already cost more time to locate than to fix.

## Data model

- **MCF order** — one mirrored order: its Amazon identifiers, status, items and
  the marketplace that fulfilled it.

## Edge cases

- **An order created by the Walmart pipeline.** It appears here too, because
  Amazon fulfilled it — the mirror does not distinguish who asked.

## Observations — not gaps

*Source: local development data; provisional.* 1,783 mirrored orders. The sync
that writes them runs only in production.

## Architecture mismatches

`ARCH-006` — "MCF" names this mirror and the Walmart pipeline. Both are wanted;
only the shared name is wrong.

## Related documents

- [walmart](../walmart/README.md) — the other MCF
- [architecture-mismatches.md](../architecture-mismatches.md) — `ARCH-006`
