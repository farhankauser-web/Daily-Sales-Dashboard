# Supply Chain — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**The laptop is the development environment; production runs the jobs.** No
cron, background worker or automated import runs here — by design, and it is not
always on. **Stale timestamps, empty tables and jobs that look like they never
ran are expected and are never on their own evidence of a defect.** Only the
code can prove one.

**Absence of data is not a defect.** Every gap carries a **Classification** —
missing implementation, bug, configuration, missing operational process, or
legacy data — a root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|

*No gaps filed yet.*

---

## Closed

| ID | Title | Closed by |
|---|---|---|
