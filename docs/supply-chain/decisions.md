# Supply Chain — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

Never edit a decision to change its meaning. Add a new one, mark the old
`superseded by`, and say what changed.

| ID | Decision | Date | Status |
|---|---|---|---|
| `SC-D-001` | Price is derived from weight, never typed | 2026-07-30 | accepted |
| `SC-D-002` | Every quotation change writes an immutable full snapshot | 2026-07-30 | accepted |
| `SC-D-003` | Over-commitment is allowed, flagged and logged | 2026-07-30 | accepted |
| `SC-D-004` | RFQs carry a 24-hour turnaround, alerted not enforced | 2026-07-31 | accepted |
| `SC-D-005` | Seven fixed PO stages with expected durations | 2026-07-31 | accepted |
| `SC-D-006` | A short receipt becomes a backorder visible at the next quote | 2026-07-31 | accepted |
| `SC-D-007` | Part paid is a state, not an absence of paid | 2026-08-01 | accepted |

---

## `SC-D-001` · Price is derived from weight, never typed

| | |
|---|---|
| **Date** | 2026-07-30 · **Status** accepted |

**Context** — Towels are traded by weight. A customer negotiates a rate per
kilogram, and every article's price follows from its dimensions and GSM.

**Decision** — Unit price is **computed**: length × width × GSM gives weight,
weight × the applicable kg rate gives the price. It is never entered directly.
The rate applied is the most specific available — a per-article override for that
customer, then the customer's flat rate for that order type.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Enter unit prices per article per customer | Hundreds of numbers maintained by hand, each able to drift from the negotiated kg rate that is the actual agreement |
| Compute, but allow an override on the line | The override becomes the norm, and within a month nobody knows which prices follow the rate |

**Reason** — The kg rate *is* the commercial agreement. Deriving from it means
the quotation and the negotiation cannot disagree.

**Consequences** — A missing rate or missing dimensions produce a price of zero
rather than a plausible wrong number, which is deliberate: it is visibly broken
rather than quietly wrong. Changing a customer's rate re-prices every future
quotation without touching any article.

**Affected documents** — [quotes.md](quotes.md)

---

## `SC-D-002` · Every quotation change writes an immutable full snapshot

| | |
|---|---|
| **Date** | 2026-07-30 · **Status** accepted |

**Context** — A B2B quotation is revised repeatedly during negotiation. A
customer querying a figure is querying a specific version of it, often weeks
later.

**Decision** — Every change writes a **numbered, immutable revision containing
the whole quotation**, not a diff, with a change note.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Store diffs | Reconstructing version 4 means replaying three diffs correctly, and one bad diff corrupts everything after it |
| Store only the current version with an audit trail of fields changed | Answers "what changed" and not "what did we send them", which is the question actually asked |
| Version only on send | Loses the working history that explains how a price was reached |

**Reason** — Storage is cheap and a mis-reconstructed price offered to a
customer is not. A full snapshot answers the question directly.

**Consequences** — Revisions accumulate on heavily negotiated quotations, which
is acceptable. A snapshot is a point-in-time record and does not follow later
corrections to a product or a customer — which is the point.

**Affected documents** — [quotes.md](quotes.md)

---

## `SC-D-003` · Over-commitment is allowed, flagged and logged

| | |
|---|---|
| **Date** | 2026-07-30 · **Status** accepted |

**Context** — A salesperson often quotes more than is in stock, because the
goods will be manufactured against the order. Sometimes that is deliberate and
sometimes it is a mistake.

**Decision** — Quoting beyond available stock is **permitted**, **flagged on the
line**, and **recorded** as an over-commitment. It is never blocked.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Block the line | Stops the normal case — selling forward against production — to catch the rare error |
| Allow silently | The rare error then surfaces as a delivery failure, which is the expensive place to find it |

**Reason** — Selling forward is the business model. The flag makes the deliberate
case visible and the accidental case obvious, without stopping either.

**Consequences** — The over-commitment log is only useful if someone reads it;
it records a business risk rather than raising it. Stock at quote time is a
moment's snapshot and may be met or missed by the time the order is placed.

**Affected documents** — [quotes.md](quotes.md)

---

## `SC-D-004` · RFQs carry a 24-hour turnaround, alerted not enforced

| | |
|---|---|
| **Date** | 2026-07-31 · **Status** accepted |

**Context** — Commercial cannot quote without a cost from supply chain. A slow
answer is a deal not being quoted, and the delay is invisible to the person
waiting.

**Decision** — A 24-hour response commitment, checked hourly, **alerted** when
breached. Nothing is enforced or escalated automatically.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| No stated turnaround | The commitment exists in the SOW regardless; not measuring it means only the salesperson knows it was missed |
| Auto-escalate on breach | Escalation is a management judgement about a specific deal, and an automatic one trains people to ignore it |

**Reason** — The value is in making the breach visible to both sides at the
moment it happens. What to do about it is a person's call.

**Consequences** — An alert nobody acts on changes nothing; this measures rather
than fixes. The clock runs on wall time, so an RFQ raised late on a Friday
breaches over the weekend.

**Affected documents** — [rfqs.md](rfqs.md)

---

## `SC-D-005` · Seven fixed PO stages with expected durations

| | |
|---|---|
| **Date** | 2026-07-31 · **Status** accepted |

**Context** — Roughly two months pass between an order and delivery. "With the
supplier" is not an answer to a customer asking where their goods are.

**Decision** — Every purchase order carries the **same seven stages** — order
confirmed, in production, quality check, shipped, at destination port, customs
cleared, delivered to warehouse — each with an expected duration. Running past a
stage's window is a breach, alerted hourly.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Stages configurable per supplier or product | Every order becomes individually shaped, and no two orders' progress can be compared |
| A single expected delivery date | A slip is invisible until the date passes, by which point nothing can be done |
| Free-text status updates | Unqueryable, so no alert is possible |

**Reason** — Fixed stages make delay detectable early and make orders
comparable. The durations are the business's own expectations, which is what
makes a breach meaningful.

**Consequences** — An order that genuinely skips a stage still carries it. The
durations are estimates and will need revisiting as lanes change; they are
defined in one place for that reason.

**Affected documents** — [purchase-orders.md](purchase-orders.md)

---

## `SC-D-006` · A short receipt becomes a backorder visible at the next quote

| | |
|---|---|
| **Date** | 2026-07-31 · **Status** accepted |

**Context** — Suppliers deliver short. The customer is still owed the balance,
and the person most likely to act on it is the salesperson — who next
encounters that customer when quoting them again.

**Decision** — A short receipt creates a **backorder** against the customer,
which **surfaces on their next quotation** until it is received or cancelled.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| A backorder report | Read by whoever remembers to open it, which is not the salesperson mid-quote |
| Auto-add the shortfall to the next order | Assumes the customer still wants it, months later, at the same price |
| Adjust the original order down | Erases the fact that the customer was promised more than they got |

**Reason** — Putting the reminder where the decision is made beats putting it
where someone must go looking.

**Consequences** — A customer quoted rarely may carry an open backorder for a
long time; the ageing is visible but nothing forces the issue. Cancelling is an
explicit act, so the record distinguishes "delivered late" from "written off".

**Affected documents** — [purchase-orders.md](purchase-orders.md), [quotes.md](quotes.md)

---

## `SC-D-007` · Part paid is a state, not an absence of paid

| | |
|---|---|
| **Date** | 2026-08-01 · **Status** accepted |

**Context** — Terms commonly involve an advance and a balance, so an invoice
spends most of its life partly settled. That is the normal case, not an
exception.

**Decision** — **Part paid is a first-class state**, alongside draft, sent, paid
and void. Payments are their own records, several per invoice.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| A paid boolean plus an amount | "Not paid" would describe an invoice 90% settled and one never touched, and every report would need to re-derive the difference |
| Derive the state from payments on every read | Correct and unindexable; ageing a receivables ledger by a derived state is slow and the state is queried constantly |

**Reason** — The advance-and-balance term makes part payment the default
condition. A state that describes the common case is worth having.

**Consequences** — The state must be maintained when a payment is recorded, so
it can in principle disagree with the sum of payments. Receivables age from the
**due** date, not the invoice date, because a term customer is not late until
their term expires.

**Affected documents** — [invoices.md](invoices.md)
