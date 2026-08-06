# Walmart retrospective

Written 2026-08-06, after the section was completed and frozen. Evidence for the
rules in [methodology.md](../methodology.md), not a second copy of them.

Scale: 2 feature documents, 1 new gap, 4 decisions — all backfilled.

---

## The section that acts

Four sections before this one report. This one **writes to an external system on
the business's behalf**, and that changes what a defect costs. Elsewhere a bug
produces a wrong number on a screen. Here it can produce a duplicate shipment or
send a customer the wrong tracking number.

That difference is why three of the four decisions are about **correctness under
concurrency and failure** rather than about what to display:

- exclusivity by compare-and-swap (`WM-D-002`), because two workers meeting on
  one order would submit two fulfilment orders to Amazon
- fatal versus retryable failures (`WM-D-004`), because retrying a malformed
  request burns the rate limit a transient failure will need
- archive only once the tracking upload has succeeded (`WM-D-003`), because the
  pipeline's job is not to ship the order — Amazon does that — but to make sure
  Walmart and the customer know it shipped

The prediction in the Financials retrospective — *expect operational rather than
structural gaps* — held. The one gap filed is about operational visibility, not
architecture.

---

## The finding that was almost a defect, twice

**9,651 unresolved errors** is the number that jumps out of this section, and
two readings of it were wrong before the right one.

The first reading was "the pipeline is broken." It is not: 348 orders reached
COMPLETED and every one of 358 packages was uploaded to Walmart successfully.

The second was "there is a poison-pill order retrying forever." There is an
order with 84 failures across four days — and it **succeeded on the fifth** and
completed normally. Checking what happened *after* the failures is what
distinguished a stuck order from a recovered one, and it took one query.

The actual finding is smaller and more useful: **8,581 of those rows are one
repeating condition** — an Amazon credential rejection, which is local
configuration and not a defect at all — and they bury 206 genuinely distinct
fatal errors underneath. The log cannot answer the question it exists for.

That became `WM-ERR-001`, and it is the *computed and unread* pattern from
Marketing in its sharpest form yet: `resolved` is a real field, filterable in
the admin, **set by exactly one human-triggered action and by no code path at
all** — including the code that later succeeds and knows the error is stale.

---

## What the state machine gave the documentation

Most sections needed a **States** table constructed from scattered code. This one
had a declared transition table, which made the document easy and made three
edges worth explaining rather than listing:

- **PROCESSING → NEW** is a clean rollback, not a failure
- **TRACKING_UPLOADED → SHIPPED** is a split shipment, not a regression
- **HOLD → HOLD** is a re-check that found the same shortage

Each reads as a bug in a transition diagram and is deliberate. Naming them in the
document is the whole value of documenting a state machine that already declares
itself in code.

---

## Nothing promoted

One candidate considered: *check what happened after a failure before calling it
stuck.* It is an instance of establishing ground truth, and the Financials
retrospective already rejected a near-identical candidate for the same reason.
Recording it twice would make the methodology longer without making it better.

The *computed and unread* pattern gained a third instance across a third section,
which is worth noting inside that entry but does not change it.

---

## For the next section

1. Supply Chain (Atlas) is the last large section and the only remaining one
   with a quote-to-cash lifecycle. Expect state machines again, and expect the
   declared-transition-table advantage to repeat.
2. `WM-001` remains carried and unregistered here, waiting for someone to
   confirm the behaviour against the current templates. Its sibling was fixed in
   `bf26090` and the two may share a cause — worth checking together rather than
   separately.
3. Brand Analytics carries `ARCH-003`, whose canonical implementation is already
   named in the entry. Read it rather than re-deriving it.
