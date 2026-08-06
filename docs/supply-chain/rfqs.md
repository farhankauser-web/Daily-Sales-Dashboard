# RFQs

files: `apps/atlas/{supply,models,views}.py`
verified against: `69a7a9a` · 2026-08-06

Commercial asking supply chain what something will cost, and supply chain
answering with a rate, a minimum order and a lead time.

## Purpose

A salesperson cannot quote a price without a cost, and the cost depends on
manufacturer, construction, quantity and port. The RFQ is the formal question,
and its response is what makes a quotation defensible rather than optimistic.

It exists as a tracked record rather than an email because **the answer has a
deadline** and an unanswered RFQ is a deal not being quoted.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Commercial request | per RFQ | what is being asked for, and for whom |
| Supply-chain response | per numbered response | FOB rate, MOQ, lead time, dates |

## Business rules

1. **Supply chain must respond within 24 hours.** The turnaround is a stated
   commitment, and a breach is alerted rather than merely visible. See
   `SC-D-004`.
2. **Every re-submission is a new numbered response**, never an edit. A revised
   cost is a new answer, and the previous one stays on record.
3. **A response is budgetary or actual.** Budgetary is indicative and quotable
   with caution; actual is committed. Which one it is must be explicit, because
   the commercial risk differs.
4. **Revalidation can be requested** when a response has aged, moving the RFQ
   back into the supply chain's queue rather than a salesperson assuming an old
   rate still holds.
5. **An RFQ belongs to a customer and, usually, a product** — but a product that
   does not exist yet can be described in words, because a customer often asks
   for something not yet in the catalogue.

## States

| State | Meaning | Leaves to |
|---|---|---|
| Open | awaiting supply chain | Responded |
| Responded | a costed answer exists | Revalidation · Closed |
| Revalidation requested | the answer has aged and is being re-checked | Responded · Closed |
| Closed | no longer live | terminal |

## System behaviour

- An hourly sweep in production flags RFQs past their turnaround and purchase
  orders past a stage's window. It reports; it does not act.
- Responses are numbered per RFQ, so "response 3" is unambiguous in a
  conversation.

## Edge cases

- **A product that does not exist yet.** Described in free text with size, GSM
  and construction, so an RFQ never waits on catalogue setup.
- **An RFQ answered, then re-answered.** Both responses persist; the latest is
  current and the earlier is still readable.
- **An RFQ nobody answers.** Stays open and is alerted every sweep — the
  intended pressure.

## Observations — not gaps

*Source: local development data; provisional.* One RFQ, responded once.

## Related decisions

`SC-D-004`

## Related documents

- [quotes.md](quotes.md) — what the cost is used for
- [purchase-orders.md](purchase-orders.md) — what happens once the business commits
