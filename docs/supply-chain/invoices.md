# Invoices

files: `apps/atlas/{models,views}.py`
       `templates/atlas/invoice*.html`
verified against: `69a7a9a` · 2026-08-06

Billing a trade customer, recording what they pay, and knowing who owes what.

## Purpose

B2B customers pay on terms — thirty days, or an advance and a balance — so an
invoice is not the end of a sale but the start of a receivable. The business
needs to know, at any moment, how much is outstanding and how overdue it is.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Won quotation | per invoice | what is being billed and at what price |
| Payment record | per payment | what has been received against it |
| Payment term | per invoice | when it falls due |

## Business rules

1. **An invoice is raised from a won quotation**, so what is billed is what was
   agreed rather than re-entered.
2. **Payment terms carry an advance percentage and a number of days.** A term of
   30% advance with 120 days is one term, not two, because that is how it is
   negotiated.
3. **An invoice's status follows its payments** — draft, sent, part paid, paid —
   with part paid a first-class state rather than an absence of paid. See
   `SC-D-007`.
4. **An invoice is voided, never deleted.** A cancelled invoice is a fact about
   the relationship.
5. **Receivables age from the due date**, not the invoice date, because a term
   customer is not overdue until their term expires.
6. **An invoice is in the selling entity's currency**, with that entity's VAT
   rate.

## States

| State | Meaning |
|---|---|
| Draft | prepared, not issued |
| Sent | issued to the customer |
| Part paid | some payment received, a balance outstanding |
| Paid | settled |
| Void | cancelled, kept on record |

## Edge cases

- **A payment larger than the balance.** Recorded as received; the overpayment is
  a commercial conversation, not a validation error.
- **Several payments against one invoice.** Normal under advance-and-balance
  terms, which is why payments are their own records rather than a paid flag.
- **An invoice for a quotation later disputed.** Voided rather than deleted, so
  the dispute has a document to refer to.

## Observations — not gaps

*Source: local development data; provisional.* No invoices exist locally. The
one worked example stops at goods received, so this is the only Atlas path with
no local exercise — it says nothing about production.

## Related decisions

`SC-D-007`

## Related documents

- [quotes.md](quotes.md) — what is billed
- [purchase-orders.md](purchase-orders.md) — fulfilment, which invoicing follows
