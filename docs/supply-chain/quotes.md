# Quotations

files: `apps/atlas/{services,models,views}.py`
       `templates/atlas/quotation*.html`
verified against: `69a7a9a` · 2026-08-06

A price offered to a trade customer, and the record of every version of it.

## Purpose

B2B pricing is negotiated, not listed. The same towel goes to two customers at
different rates, and a quotation is revised several times before it is accepted
or lost. The business needs both the current price and the history of how it got
there — a customer who queries a figure is querying a version.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Product attributes | per article | dimensions and GSM, from which weight is derived |
| Customer kg rate | per customer, per order type | the rate applied |
| Per-article override | per customer and product | a rate that beats the customer's flat one |

Everything is entered by the commercial team. Nothing is fetched.

## Business rules

1. **Price is derived from physical weight, never typed.** Length × width × GSM
   gives a weight in kilograms; weight × kg rate gives the unit price. A quote
   is therefore a consequence of the article's specification. See `SC-D-001`.
2. **The rate is the most specific one that applies**: a per-article override
   for that customer, else the customer's flat rate for that order type, else
   nothing — and nothing means the setup is incomplete, not that the price is
   zero.
3. **Local and container orders price differently.** A customer carries a rate
   for each, because the commercial terms differ.
4. **Every change writes an immutable revision.** The snapshot is the whole
   quotation as it stood, not a diff, so any version can be reproduced exactly.
   See `SC-D-002`.
5. **Discounts exist at two levels** — per line and per quotation — and both are
   recorded, because a customer negotiates them separately.
6. **Committing more than stock is allowed and logged.** A line exceeding
   available stock is flagged on the quote and recorded as an over-commitment
   rather than blocked. See `SC-D-003`.
7. **A quotation belongs to one selling entity**, with that entity's currency and
   VAT rate. Two companies trade through this system and must never mix.

## States

| State | Meaning | Leaves to |
|---|---|---|
| Draft | being prepared | Sent |
| Sent | with the customer | Won · Lost |
| Won | accepted — becomes a purchase order and an invoice | terminal |
| Lost | declined | terminal |

## User actions

| Action | Who | Result |
|---|---|---|
| Create a quotation | commercial | lines priced automatically from weight and rate |
| Revise it | commercial | a new immutable revision, with a change note |
| Mark sent, won or lost | commercial | status moves; won quotations feed sourcing and invoicing |
| Set a per-article rate | commercial | that article prices differently for that customer |

## Edge cases

- **A customer with no rate set.** Prices at zero, which is visibly wrong rather
  than plausibly wrong — the intended signal that setup is incomplete.
- **A product missing dimensions or GSM.** Weight is zero, so the price is zero.
  Same signal, same reason.
- **A quotation revised after being sent.** Permitted; the revision history is
  what makes the earlier version still answerable.
- **A line short of stock.** Quoted anyway, flagged, and logged — the business
  sells forward deliberately.

## Observations — not gaps

*Source: local development data; provisional.* One quotation exists, won, with
three revisions — the revision mechanism working on the one example present.

## Related decisions

`SC-D-001` `SC-D-002` `SC-D-003`

## Related documents

- [rfqs.md](rfqs.md) — where the cost behind a price comes from
- [purchase-orders.md](purchase-orders.md) — what a won quotation becomes
- [invoices.md](invoices.md) — how a won quotation is billed
