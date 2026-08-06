# Walmart — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

Never edit a decision to change its meaning. Add a new one, mark the old
`superseded by`, and say what changed.

| ID | Decision | Date | Status |
|---|---|---|---|
| `WM-D-001` | An unmapped or short SKU holds the order; it never part-ships | 2026-07-13 | accepted |
| `WM-D-002` | Exclusivity by compare-and-swap, not by locking | 2026-07-13 | accepted |
| `WM-D-003` | An order archives only once Walmart has the tracking | 2026-07-20 | accepted |
| `WM-D-004` | A 4xx is fatal and never retried | 2026-07-13 | accepted |

---

## `WM-D-001` · An unmapped or short SKU holds the order; it never part-ships

| | |
|---|---|
| **Date** | 2026-07-13 · **Status** accepted |

**Context** — A Walmart order can name a SKU with no Amazon mapping, or ask for
more units than Amazon currently holds. Both are common and neither is a fault
in the order.

**Decision** — The order goes to **HOLD**, whole, with the SKU and the shortfall
recorded. It is re-checked on every submission run and proceeds when the
condition clears. It is **never part-shipped**.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Ship what is available and short the rest | Creates a customer promise on Walmart we cannot complete, and Walmart's shipping update expects the lines it was told about |
| Route to ERROR | ERROR means "a person must look"; being briefly out of stock does not, and an operator would drown |
| Substitute a similar Amazon SKU | The mapping is a business decision about what the customer bought; guessing it is how a customer receives the wrong item |

**Reason** — Being short is a temporary condition with an automatic remedy.
Holding is the state that says so, and re-checking costs nothing.

**Consequences** — An order can sit in HOLD indefinitely if stock never arrives,
which is why the reconcile stage reports anything unfinished beyond a threshold.
The hold reason names the SKU, so the remedy — restock, or fix the mapping — is
visible without opening the order.

**Affected documents** — [orders.md](orders.md)

---

## `WM-D-002` · Exclusivity by compare-and-swap, not by locking

| | |
|---|---|
| **Date** | 2026-07-13 · **Status** accepted |

**Context** — Five stages run on overlapping schedules and can touch the same
order. Two workers moving one order simultaneously would submit two fulfilment
orders to Amazon — a real duplicate shipment, not a reporting error. The
development database has no row-level locking.

**Decision** — A transition is an **atomic conditional update**: move the order
*only if* it is still in one of the expected source states. Exactly one caller
wins; every other gets nothing and skips the order. The audit event is written
in the same transaction as the move.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Row-level locking | Unavailable on the development database, so the behaviour would differ between environments — the worst property for a concurrency mechanism |
| An application-level queue | Real, and a large amount of infrastructure for a problem a conditional update solves |
| Rely on the stage locks alone | They stop two runs of the *same* stage; they do not stop two different stages meeting on one order |

**Reason** — The condition and the write are the same statement, so there is no
window between checking and acting. It behaves identically on both databases.

**Consequences** — A losing caller gets no error, only a skip, so a stage's
"processed" count is a count of *won* transitions and may be lower than the
orders it examined. That is correct and can read as under-performance. Because
the audit event shares the transaction, a state with no audit event is
impossible.

**Affected documents** — [orders.md](orders.md)

---

## `WM-D-003` · An order archives only once Walmart has the tracking

| | |
|---|---|
| **Date** | 2026-07-20 · **Status** accepted |

**Context** — Amazon reports an order shipped. Walmart may also report it
shipped. Either could be taken as the signal to archive the order and stop
working it.

**Decision** — An order reaches COMPLETED **only after its tracking number has
been retrieved from Amazon and successfully uploaded to Walmart**. Amazon's
shipped status alone is not sufficient, and neither is Walmart's.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Archive on Amazon's shipped status | The customer has no tracking number, and the order leaves the active list — so nobody notices it never got one |
| Archive on Walmart's shipped status | Manually fulfilled orders show as shipped on Walmart while our tracking upload has not happened; they would archive with the job half done |
| Archive on either | Combines both failure modes |

**Reason** — The pipeline's purpose is not to ship the order; Amazon does that.
Its purpose is to make sure Walmart and the customer know it shipped. Until the
tracking upload succeeds, that work is not done.

**Consequences** — Orders fulfilled outside the system are backfilled — their
tracking is uploaded first, and only then do they archive — rather than being
closed as a special case. An order whose tracking upload keeps failing stays in
the active list and is caught by the stuck-order report, which is the intended
behaviour.

**Affected documents** — [mcf-pipeline.md](mcf-pipeline.md), [orders.md](orders.md)

---

## `WM-D-004` · A 4xx is fatal and never retried

| | |
|---|---|
| **Date** | 2026-07-13 · **Status** accepted |

**Context** — Both APIs fail in two distinct ways: the request was wrong, or the
service was briefly unavailable. Treating them alike wastes rate limit on the
first and gives up too early on the second.

**Decision** — Statuses in the 4xx family are **fatal**: the order routes to
ERROR and nothing is retried. Throttling and 5xx statuses are **retryable**,
with exponential backoff.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Retry everything | A malformed request returns the same 4xx every time, consuming rate limit that a genuinely transient failure then cannot use |
| Retry nothing | A single network blip would send healthy orders to ERROR and demand a human for each |
| Retry 4xx a fixed number of times | The middle option with none of the benefit: still wasteful, still eventually fatal |

**Reason** — The two classes have different remedies. A 4xx needs the request
changed, which only a person can do; a 5xx needs patience, which the machine has.

**Consequences** — A transient condition Amazon reports as 4xx — an address it
temporarily will not validate — routes to ERROR rather than resolving itself,
which is why address variants are tried *before* giving up. Every attempt
currently writes its own error row, which is part of `WM-ERR-001`.

**Affected documents** — [mcf-pipeline.md](mcf-pipeline.md)
