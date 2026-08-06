# Reporting — gaps

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
| `REP-PROD-001` | The live fallback builds its own SKU table without the allocator | P2 | bug | open |

---

## `REP-PROD-001` · The live fallback builds its own SKU table without the allocator

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | bug |
| **Code alone fixes it** | yes |
| **Dependencies** | none. This is the straggler in `ARCH-009`'s sibling, `ARCH-007` |

**Current behaviour** — When neither cached tier can serve a window, the
dashboard falls through to a live call to Amazon and builds the SKU table with
its own code. That builder measures margin ex-VAT correctly, and implements none
of the other rules: it does not read the allocator, does not distinguish today
from past days for advertising attribution, and emits no unallocated-spend row.
Its ad spend columns are hard zeros.

**Expected behaviour** — One builder for this table, implementing every rule
once. See [product-performance.md](product-performance.md).

**Root cause** — The live path is the oldest of the three and was the only one
when it was written. Each business rule since — the allocator, the today/past
attribution split, the unallocated row — was added to the cached builder, which
is where the traffic goes. The straggler was never revisited because it is
rarely exercised, and consolidation onto the canonical builder has already
happened for one of the other two paths.

**Evidence** — source: **code**, so it holds in production.
`_build_cached_skus` reads `SkuPpcAllocation` and branches on whether the date
is today; the live path's row construction sets `'spSpend': 0, 'sdSpend': 0,
'sbSpend': 0, 'totalPpc': 0` unconditionally. Both compute margin through
`net_factor`, so the ex-VAT rule is the one thing they agree on.

**Business impact** — When the live path serves, every SKU shows zero ad cost,
so contribution margin is overstated by the whole of advertising and TACoS reads
zero. The page gives no sign it is on this path. How often that happens in
production is unknown — it requires both cached tiers to be unavailable.

**Technical impact** — This is the surviving instance of the failure that makes
`ARCH-007` the most expensive mismatch on record: the ex-VAT fix was applied to
one builder and appeared to do nothing, because a different builder was serving
the page.

**Recommendation** — Point the live path at the canonical builder, or delete the
branch if the cached tiers now cover every case — **establish which before
touching it**. Do not write a third builder; see `ARCH-007`.

**Production verification** — how often does the live fallback actually serve?
The log line "falling through to live" answers it and would set the priority.

**Related documents** — [product-performance.md](product-performance.md), [daily.md](daily.md)
**Related decisions** — `REP-D-002`

---

## Production verification queue

Findings below rest on local development data and **cannot be settled here**.
Each is one query or one log grep on production. Until then their priority is
provisional.

**This queue is worked at implementation time, not documentation time.**

| Gap | The one question production answers |
|---|---|
| `REP-PROD-001` | how often does the live fallback serve? The "falling through to live" log line answers it |

---

## Closed

| ID | Title | Closed by |
|---|---|---|
