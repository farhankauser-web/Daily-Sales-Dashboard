# Purchase orders

files: `apps/atlas/{supply,models,views}.py`
verified against: `69a7a9a` · 2026-08-06

An order placed with a supplier to fulfil a won quotation, tracked through seven
stages from confirmation to the warehouse.

## Purpose

Between winning an order and delivering it sit roughly two months of
manufacturing and freight. The customer asks where their goods are; the answer
has to be better than "with the supplier".

Seven named stages, each with an expected duration, turn that into a specific
answer — and turn a delay into something noticed on the day it happens rather
than the week the goods fail to arrive.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Won quotation | per order | what was sold, to whom, at what price |
| RFQ response | per line | the supplier cost the order was committed at |
| Stage progress | per stage | where the order is, and whether it is late |
| Goods receipt | per line | what actually arrived |

## Business rules

1. **A purchase order carries seven stages with expected durations** — confirmed,
   in production, quality check, shipped, at destination port, customs cleared,
   delivered. The durations are the business's own expectations, not the
   supplier's promise. See `SC-D-005`.
2. **A stage running past its window is a breach**, alerted hourly in production
   until it completes or is acknowledged.
3. **Receiving less than ordered creates a backorder**, which stays open against
   the customer and **surfaces on their next quotation** until it is received or
   cancelled. See `SC-D-006`.
4. **A backorder is resolved, never deleted.** It closes as received or as
   cancelled, and which of those it was matters commercially.
5. **A purchase order belongs to a customer as well as a supplier**, because it
   exists to fulfil a specific won quotation — this is not stock buying.

## States

The order moves through **received** as its lines are fulfilled; the detail lives
in the stages rather than in a single status field, because "where is it" has
seven possible answers and only one of them is "arrived".

## Edge cases

- **A short receipt.** Backordered, visible to the salesperson at the moment they
  next quote that customer, which is when it matters.
- **A stage completed out of order.** Recorded as it happened; the sequence is
  the expectation, not a constraint.
- **An order fulfilled from stock rather than a supplier.** Has no supplier, and
  the field allows it.

## Observations — not gaps

*Source: local development data; provisional.* One purchase order, received,
with all seven stages present, and one backorder that resolved as received. The
full path executes.

## Related decisions

`SC-D-005` `SC-D-006`

## Related documents

- [quotes.md](quotes.md) — what a won quotation commits to
- [rfqs.md](rfqs.md) — the cost the order was committed at
- [invoices.md](invoices.md) — billing the customer
