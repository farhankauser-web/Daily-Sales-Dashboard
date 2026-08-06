# Reporting — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

Never edit a decision to change its meaning. Add a new one, mark the old
`superseded by`, and say what changed.

| ID | Decision | Date | Status |
|---|---|---|---|
| `REP-D-001` | Figures are served from the freshest sufficient source | 2026-06-20 | accepted |
| `REP-D-002` | The allocator's output is preferred; the fallback is deliberate | 2026-06-20 | accepted |
| `REP-D-003` | An incomplete day is excluded from Hourly Patterns entirely | 2026-06-02 | accepted |
| `REP-D-004` | SB/SD hourly spend is distributed uniformly, and labelled | 2026-06-02 | accepted |
| `REP-D-005` | Historical ends at yesterday | 2026-06-20 | accepted |

---

## `REP-D-001` · Figures are served from the freshest sufficient source

| | |
|---|---|
| **Date** | 2026-06-20 · **Status** accepted |

**Context** — The dashboard can get its numbers three ways: stored daily
figures, hourly snapshots written through the day, or a live call to Amazon.
They differ in speed, freshness and reliability, and no single one is best for
every window.

**Decision** — A fixed tier order, tried in sequence, each falling through to
the next **on failure as well as on absence**: stored daily figures → hourly
snapshots → a live call. A live call is the last resort, never the default.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Always call Amazon live | Slow and rate limited; the page becomes unusable exactly when several people open it at once |
| Always serve the cache and show nothing when it is empty | Today would be blank until the first sync of the day, which is when the page is most watched |
| Pick the tier per window at configuration time | The right tier depends on whether a job has run, which configuration cannot know |

**Reason** — Freshness and speed are both real requirements, and the cheapest
sufficient answer is different at different times of day. Falling through on
*failure* as well as absence is what makes the page degrade to slow-but-correct
rather than to an error.

**Consequences** — The same window can be served by different code on two
consecutive requests, so a figure can change slightly on refresh as a better
tier becomes available. It also means a bug in one tier is invisible while
another is serving — which is how a table built by two paths came to disagree
(`ARCH-007`).

**Affected documents** — [daily.md](daily.md), [product-performance.md](product-performance.md)

---

## `REP-D-002` · The allocator's output is preferred; the fallback is deliberate

| | |
|---|---|
| **Date** | 2026-06-20 · **Status** accepted |

**Context** — Per-SKU advertising cost can come from the allocator, which
reconciles to campaign spend exactly and records how each figure was derived, or
from the older campaign-name attribution that predates it.

**Decision** — Where the allocator has produced rows for a date, **those are
used everywhere**. Where it has not, the older attribution is used and the row
says which it is. The fallback is never silently equivalent to the allocator.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Use the allocator only, showing zero where it has not run | A zero ad cost is a wrong number that inflates margin, which is worse than an approximate one |
| Keep the legacy attribution as primary | It does not reconcile to campaign spend and carries no confidence, so every figure derived from it is unqualified |
| Blend them | Two attribution methods averaged is a third method nobody chose |

**Reason** — An attributed figure that records its own provenance is worth more
than one that does not, and a labelled approximation is worth more than a zero.

**Consequences** — Ad cost for a date can change when the allocator later runs
for it, which is correct and can surprise. The per-row source is what makes the
change explicable, so anything that drops it makes the table less trustworthy
rather than simpler.

**Affected documents** — [product-performance.md](product-performance.md), [sku-allocation.md](../marketing/sku-allocation.md)

---

## `REP-D-003` · An incomplete day is excluded from Hourly Patterns entirely

| | |
|---|---|
| **Date** | 2026-06-02 · **Status** accepted |

**Context** — An hourly heatmap is read for *patterns*. A day whose orders data
is complete but whose advertising is missing would still render 24 cells, and
those cells would be read as a real shape.

**Decision** — A day that fails the **core** completeness check — hourly orders
and hourly advertising, both — is **excluded from the view entirely**. Not
greyed, not partial: absent. Days failing only the Brands/Display layer still
render, with those columns unknown.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Render what is present | A heatmap invites pattern-reading, and a half-sourced day produces a pattern that is not there |
| Render with the missing cells greyed | Better, and still shows a shape built from partial data; the eye reads the coloured cells |
| Fill from the daily total ÷ 24 | Fabricates the exact thing the view exists to reveal |

**Reason** — This view's whole value is the shape of a day. A shape assembled
from incomplete sources is worse than no shape, because it is actionable and
wrong.

**Consequences** — The heatmap has missing days, which reads as a bug to anyone
who does not know the rule — the same cost `MKT-D-007` accepts for the same
reason. A day can also appear late, once its sources complete.

**Affected documents** — [hourly.md](hourly.md)

---

## `REP-D-004` · SB/SD hourly spend is distributed uniformly, and labelled

| | |
|---|---|
| **Date** | 2026-06-02 · **Status** accepted |

**Context** — Sponsored Products reports advertising spend hourly. Sponsored
Brands and Display report it only daily. An hourly cost view needs a figure for
all three.

**Decision** — Brands and Display daily spend is **divided evenly across the 24
hours**, and only for days whose source is confirmed complete. It is labelled as
an estimate. This is the **only** estimation permitted in the view.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Omit SB/SD from the hourly view | The total ad cost per hour is the number people come for; without them it is wrong in a direction nobody can see |
| Distribute SB/SD in proportion to SP's hourly curve | Assumes the three ad products have the same daily rhythm, which is a stronger claim than uniformity and equally unverifiable |
| Distribute by hourly revenue | Circular: it would make ad cost track revenue by construction, and the view exists to compare them |

**Reason** — Uniform distribution is the assumption that adds the least
information. Anything shaped implies knowledge we do not have.

**Consequences** — Any per-hour Brands or Display figure is an estimate and must
be labelled wherever shown. The **total** is withheld unless all three
components are known (`REP-D-003` rule 3), so a labelled estimate never silently
becomes part of an unlabelled aggregate.

**Affected documents** — [hourly.md](hourly.md)

---

## `REP-D-005` · Historical ends at yesterday

| | |
|---|---|
| **Date** | 2026-06-20 · **Status** accepted |

**Context** — The historical view plots a trend over weeks or months. Today is
a real day with real data, and it is partial until it ends.

**Decision** — The historical window **always ends at yesterday**. Today never
appears in a trend line.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Include today | A part-day plotted against whole days reads as a collapse, every day, until the day closes |
| Include today, marked as partial | The mark is in a legend and the drop is in the chart; the chart wins |
| Extrapolate today to a full day | Invents a number to protect a chart's appearance |

**Reason** — A trend line is read for direction. A guaranteed final-point drop
destroys that reading daily, and no annotation is loud enough to prevent it.

**Consequences** — Today's figures live only on [daily.md](daily.md), which is
where their partial nature is stated. Anyone comparing the two views must know
they end on different days.

**Affected documents** — [historical.md](historical.md), [daily.md](daily.md)
