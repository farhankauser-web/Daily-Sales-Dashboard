# Settings — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**The laptop is the development environment; production runs the jobs.** Stale
timestamps, empty tables and jobs that look like they never ran are expected
here and are never on their own evidence of a defect. Only the code can prove
one.

**Absence of data is not a defect.** Every gap carries a **Classification**, a
root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|

*No gaps filed yet.*

**Carried from the root index, not yet registered here:** `SET-001` — AE and SA
marketplace ids missing, blocking the UAE P&L. It uses the older two-part id
scheme. All four marketplaces are configured and active locally, which neither
confirms nor refutes the claim: the ids in question are Amazon marketplace
identifiers held *inside* a configuration, not the configuration itself. It gets
a full entry when checked against production, which is where the P&L in question
is produced. See [credentials.md](credentials.md).

---

## Closed

| ID | Title | Closed by |
|---|---|---|
