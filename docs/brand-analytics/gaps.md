# Brand Analytics — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**The laptop is the development environment; production runs the jobs.** Stale
timestamps, empty tables and jobs that look like they never ran are expected
here and are never on their own evidence of a defect. Only the code can prove
one.

**Absence of data is not a defect.** Every gap carries a **Classification**, a
root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|
| `BA-002` | `BABrandShareWeekly` has no writer and no reader | P3 | missing implementation | open |

**Carried from the root index, not yet registered here:** `BA-001` — three Brand
Analytics reports submitted, never collected. It uses the older two-part id
scheme. The local sync log shows 32 pending against 12 collected, which is
consistent with the claim and **also** consistent with a machine that simply
stopped running its poller — the two are indistinguishable without production.
It gets a full entry when someone checks production, which is where the poller
actually runs.

---

## `BA-002` · `BABrandShareWeekly` has no writer and no reader

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes — either build it or drop it |
| **Dependencies** | none |

**Current behaviour** — A model and its migration exist for weekly brand share.
**Nothing writes it and nothing reads it.** The ingestion command submits three
report kinds and brand share is not among them; the market-share page and its
API read search-query rows instead.

**Expected behaviour** — Either the table is populated and used, or it does not
exist.

**Root cause** — It was created as part of the Brand Analytics schema in
anticipation of a report that was never wired up. Amazon's per-ASIN restriction
changed what could be ingested after the schema was designed, and the table was
left behind rather than removed.

**Evidence** — source: **code**. `BABrandShareWeekly` appears in exactly two
files: the model definition and the migration that created it. No command, view,
API or template references it. Market share is derived from
`BASearchQueryWeekly`.

**Business impact** — None. The market-share page works, sourcing share from the
search-query data, which carries it.

**Technical impact** — A table that looks like the source of the market-share
page and is not. A reader tracing that page backwards will find it first and be
wrong.

**Recommendation** — Drop it. Market share is already derived from search-query
rows, which is the report Amazon actually provides per ASIN. If a genuine brand
share report returns, it can be added back with a writer at the same time.

**Related documents** — [README.md](README.md)
