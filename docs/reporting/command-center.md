# Command Center

files: `apps/command_center/{views,widgets,models}.py`
       `templates/command_center/`
verified against: `b70e2c2` · 2026-08-06

The post-login home: a configurable board of widgets, each a compressed answer
from somewhere else in the application.

## Purpose

Six sections each have their own page, and nobody opens six pages every morning.
This is the one screen that says whether anything needs attention today — and
if it does, links to the page that explains it.

It is deliberately **a reader, not a source**. Every figure on it belongs to
another section, and the widget's job is to compress and link rather than to
compute.

## Scope

Covers the board, the widgets and how they source their figures.

**Not covered:**
- any figure's derivation — each widget's own section owns that
- the daily dashboard — [daily.md](daily.md)

## Business rules

1. **A widget computes nothing of its own.** It reads a section's existing
   figures and compresses them. A widget that computed its own number would be
   a second source for a fact that already has one.
2. **A widget links to its source page.** The board answers "does this need
   attention"; the page answers "why".
3. **An empty widget states why it is empty.** "No data yet" and "nothing to
   report" are different, and a blank card is neither.
4. **The board is per user.** Which widgets appear and in what order is a
   personal arrangement, not a global setting.

## Data model

- **Widget placement** — per user: which widget, where on the board.
- Everything a widget displays belongs to another section's tables.

## Edge cases

- **A widget whose source section has no data.** Renders its empty state rather
  than a zero, by rule 3.
- **A widget pointing at a superseded source.** Reads empty and looks like a
  data problem — which is exactly what `ARCH-003` describes for the Search Query
  Performance widget.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INT-001` | phase 3 — per-widget configuration and resizing | missing implementation |

## Architecture mismatches

`ARCH-003` — a widget reads `apps/sqp`, which is superseded by the dashboard's
Brand Analytics models and holds no rows. The widget renders empty and reads as
a data problem rather than a dead dependency.

## Related documents

- [daily.md](daily.md) — the fuller view of the same figures
- [architecture-mismatches.md](../architecture-mismatches.md) — `ARCH-003`
