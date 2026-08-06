# Intelligence — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**The laptop is the development environment; production runs the jobs.** Stale
timestamps, empty tables and jobs that look like they never ran are expected
here and are never on their own evidence of a defect. Only the code can prove
one.

**Absence of data is not a defect.** Every gap carries a **Classification**, a
root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|
| `INT-001` | Command Center phase 3 — per-widget configuration and resizing | P3 | missing implementation | open |

---

## `INT-001` · Command Center phase 3 — per-widget configuration and resizing

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open — planned, not built |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — The Command Center board places widgets per user. A
widget cannot be resized, and cannot be configured — a widget showing a
marketplace or a window shows the one it was built with.

**Expected behaviour** — Per-widget configuration and resizing, so one board
serves people who watch different marketplaces.

**Root cause** — Planned as a later phase and not yet built. The layout model
carries placement only.

**Evidence** — source: **code**. The layout model stores which widget and where;
nothing stores per-widget options or dimensions.

**Business impact** — Small and growing with the number of marketplaces. A user
who works one marketplace sees widgets scoped to another.

**Technical impact** — None; it is additive work on an existing model.

**Recommendation** — Add per-widget options and size to the layout record. Do it
when a second person needs a different board, not before.

**Related documents** — [reporting/command-center.md](../reporting/command-center.md), [ai-briefings.md](ai-briefings.md)
