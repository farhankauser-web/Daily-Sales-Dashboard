# Financials — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**The laptop is the development environment; production runs the jobs.** No
cron, background worker or automated import runs here — by design, and it is not
always on. **Stale timestamps, empty tables, missing scheduled runs and jobs
that look like they never ran are expected and are never on their own evidence
of a defect.** Only the code can prove one.

**Absence of data is not a defect.** Every gap carries a **Classification** —
missing implementation, bug, configuration, missing operational process, or
legacy data — a root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|

*No gaps filed yet.*

**Carried from the root index, not yet registered here:** `FIN-001` — referral
fee computed on gross revenue, never checked against a settlement. It predates
this register and uses the older two-part id scheme. It gets a full entry —
root cause, classification, evidence — when `fee-drift.md` is written, which is
the document that would prove or disprove it. Listing it in the table above
before then would imply an analysis that has not happened.

---

## Closed

| ID | Title | Closed by |
|---|---|---|
| `FIN-003` | Margins divided by gross revenue while the numerator was ex-VAT | `b6a8603` |
| `FIN-004` | Cash-flow page hardcoded `$` for every region | `fd6af91` |
