# Walmart — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**The laptop is the development environment; production runs the jobs.** No
cron, background worker or automated import runs here — by design, and it is not
always on. **Stale timestamps, empty tables, orders piling up in an early state
and jobs that look like they never ran are expected and are never on their own
evidence of a defect.** Only the code can prove one.

**Absence of data is not a defect.** Every gap carries a **Classification** —
missing implementation, bug, configuration, missing operational process, or
legacy data — a root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|
| `WM-ERR-001` | The error log has no lifecycle, so a repeating failure buries every real one | P2 | missing implementation | open |

**Carried from the root index, not yet registered here:** `WM-001` — every
non-JSON response reported as "session expired". It uses the older two-part id
scheme and belongs to orders.md, which covers the operator view. It gets a full
entry when someone confirms the behaviour against the current templates; its
sibling `WM-002` (a crash on `request.user.username`) was fixed in `bf26090`,
and the two may share a cause.

---

## `WM-ERR-001` · The error log has no lifecycle, so a repeating failure buries every real one

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — Every failure writes a row carrying the exception, a
stack trace, the endpoint, the order and a retry count. The row has a `resolved`
flag. **Nothing ever sets it except a human clicking an admin action**, and
nothing deduplicates, caps or ages the log.

So a single persistent condition writes a row per attempt, indefinitely, and an
operation that later succeeds leaves its failures marked unresolved forever.

**Expected behaviour** — An error log that can be read: repeated identical
failures collapsed or counted, and an error resolved when the thing it describes
later succeeds.

**Root cause** — The log was built as a forensic record — capture everything,
decide later — which is right for diagnosing one incident and wrong as a
standing signal. The `resolved` flag anticipated a lifecycle that was never
built, and the retry-with-backoff helper logs every attempt rather than only the
final outcome.

**Evidence** — source: **code**, so it holds in production. `log_error` creates
a row unconditionally with no lookup for an existing one; the retry helper calls
it once per attempt. `resolved` is written in exactly one place — an admin bulk
action. No command, view or scheduled task reads or sets it.

*Local corroboration, provisional:* 9,651 unresolved rows. 8,581 of them are one
repeating condition — an Amazon credential rejection, which is a local
configuration matter and not itself a defect. Beneath them sit 206 genuinely
distinct fatal errors from Walmart's shipping endpoint. One order accumulated 84
failures across four days, **succeeded on the fifth**, and reached COMPLETED —
all 84 rows are still unresolved.

**Business impact** — The log cannot answer the question it exists for: *what is
wrong right now?* A real fatal error is invisible among thousands of rows from a
condition somebody already knows about. This is the section that writes to an
external system on the business's behalf, so the errors that matter here are the
ones that reach a customer.

**Technical impact** — Unbounded growth with no retention, and a field
maintained by hand that could be maintained by the code that already knows the
outcome.

**Recommendation** — Two changes, both small:
1. **Log the outcome, not every attempt.** The retry helper should write one row
   when retries are exhausted, carrying the attempt count it already tracks.
2. **Resolve on success.** When an order or package operation succeeds, mark its
   open errors resolved — the code that succeeds already knows which order it is.

Then the default view is unresolved errors, and it is short enough to read.
Retention can follow; it is not the problem.

**Related documents** — [orders.md](orders.md), [mcf-pipeline.md](mcf-pipeline.md)

---

## Closed

| ID | Title | Closed by |
|---|---|---|
| `WM-002` | Reprocess crashed on `request.user.username` | `bf26090` |
