# Supply Chain — Atlas

The B2B arm: selling towels wholesale to trade customers, quote to cash. A
separate business from the Amazon one, sharing only this application.

## Purpose

Atlas runs a **business-to-business** operation. A customer asks for a price, we
quote, they accept, we source and ship, we invoice and chase payment. None of it
touches Amazon.

**Careful — the naming is confusing and worth remembering.** In this application
*Supply Chain* means **Atlas, the B2B arm**. The physical supply chain for our
own Amazon brand — suppliers, containers, receiving — is
[inventory](../inventory/README.md).

**This section is complete and frozen** except for future feature changes. Four
features are documented; the process lessons are in
[RETROSPECTIVE.md](RETROSPECTIVE.md).

## Data source

Atlas holds its own records. Nothing is imported from Amazon or Walmart, and
nothing is exported to them.

| Source | Grain | Authoritative for |
|---|---|---|
| Entered by commercial | per quotation, customer, product | prices quoted and orders won |
| Entered by supply chain | per RFQ response, purchase order | costs, lead times, stage progress |

The **Statement of Work is the specification**, and the code cites its section
numbers throughout — pricing at §15, backorders at §91–93, the RFQ turnaround at
§13. Where this documentation and the SOW disagree, the SOW is the business
truth and the disagreement is a finding.

## Features

| Document | Covers | Open here when |
|---|---|---|
| [quotes.md](quotes.md) | pricing, quotations and their revisions | a price or a quote total looks wrong |
| [rfqs.md](rfqs.md) | commercial → supply-chain cost requests | an RFQ is unanswered or stale |
| [purchase-orders.md](purchase-orders.md) | supplier orders, stage tracking, backorders | an order is late or short |
| [invoices.md](invoices.md) | invoicing, payments and receivables ageing | a customer balance looks wrong |

## Relationships

```
Customer asks
      ↓
   RFQ ──→ supply chain responds with cost, MOQ, lead time
      ↓
Quotation (priced by weight × kg rate) ──→ sent ──→ won / lost
      ↓ won
Purchase order ──→ seven tracked stages ──→ received
      ↓                         ↓
   Backorder if short      Invoice ──→ payments ──→ AR ageing
```

Two facts about this shape cause most confusion:

- **A price is derived from physical weight**, not set per unit. Length, width
  and GSM give a weight; a kg rate gives the price. See [quotes.md](quotes.md).
- **Two companies trade through one system.** Every record belongs to a selling
  entity with its own currency and VAT rate, and they must never mix.

## Ground truth

Established before writing. *Source: local development data; provisional.*

**The laptop runs no scheduled jobs — by design.** Atlas's only scheduled work
is an hourly alert sweep for RFQ and stage breaches, which runs in production.

The local database holds **one worked example end to end** — one customer, one
product, one RFQ answered, one quotation won with three revisions, one purchase
order received through seven stages, one backorder resolved, one over-commitment
logged. No invoices exist yet.

That is a smoke test, not a business. It confirms every path executes and says
nothing about volume, which is exactly what local data is for.

| | Local |
|---|---|
| Companies · payment terms | 2 · 9 |
| Customers · products · suppliers | 1 · 1 · 1 |
| Quotations (won) · revisions | 1 · 3 |
| RFQs (responded) · responses | 1 · 1 |
| Purchase orders (received) · stages | 1 · 7 |
| Backorders · over-commitments | 1 · 1 |
| Invoices · payments | 0 · 0 |

## Navigation

| Working on… | Load |
|---|---|
| a price or quote total | `CLAUDE.md` · this README · [quotes.md](quotes.md) · `gaps.md` |
| a late supplier order | `CLAUDE.md` · this README · [purchase-orders.md](purchase-orders.md) · `gaps.md` |
| a customer balance | `CLAUDE.md` · this README · [invoices.md](invoices.md) · `gaps.md` |

## Method

Follows [methodology.md](../methodology.md). For this section: the SOW is the
specification, so a rule the code implements and the SOW does not — or the
reverse — is a finding worth recording rather than a detail to reconcile
silently.

## Related sections

- [inventory](../inventory/README.md) — the *other* supply chain, for our own brand
