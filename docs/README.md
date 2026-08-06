# Pulse docs

One leaf per sidebar section, so the doc you need has the name you'd say out
loud. The two biggest sections are split further — a doc that covers fifteen
nav items is just another monolith.

**Read the leaf, not the code.** Each leaf lists its entry points with
`file:line`, so the first grep is unnecessary and the second is precise. That
is the whole point: `apps/dashboard/views.py` alone is 7,590 lines.

## Index

| Section | Doc | Covers |
|---|---|---|
| Reporting | `reporting.md` | daily & historical sales, hourly patterns, morning report, command centre |
| Financials | `financials.md` | P&L, COGS, FBA fee drift, targets, settlements |
| Marketing | [`marketing/`](marketing/README.md) | PPC — see that folder's README |
| Brand Analytics | `brand-analytics.md` | search-query performance, market share, baskets |
| Walmart | `walmart.md` | Walmart orders → Amazon MCF |
| Supply Chain | `supply-chain.md` | **Atlas — the B2B arm.** Quotes, RFQs, B2B POs, invoices |
| Inventory | [`inventory/`](inventory/README.md) | planner, suppliers, POs, containers, receiving — see that folder's README |
| Intelligence | `intelligence.md` | AI recommendations, profit alerts |
| Settings | `settings.md` | API credentials, users, roles, catalog |
| — | **[`methodology.md`](methodology.md)** | **how we document and analyse — read before opening a section** |
| — | `map.md` | the full documentation map and build order |
| — | `templates/` | the four document templates, the writing style and the id schemes |
| — | `architecture.md` | how the six apps fit together, and how data flows |
| — | `architecture-mismatches.md` | technical-debt register — where code and business disagree |
| — | `deployment.md` | EC2, nginx, gunicorn, cron, TLS |
| — | **`gaps.md`** | **every open gap across all sections — the backlog** |
| — | [`inventory/RETROSPECTIVE.md`](inventory/RETROSPECTIVE.md) | the first section retrospective — evidence behind the methodology |

**Careful:** in this app *Supply Chain* means Atlas. Containers, suppliers,
purchase orders, allocation and receiving all live under **Inventory**.

## The shape of a leaf

Templates live in `templates/`. Copy the skeleton, fill it in, **delete what
does not apply** — a page of empty headings buries the content that matters.

```markdown
# Containers
files: apps/inventory_planning/{views,procurement,models}.py
       templates/inventory_planning/{containers,allocation,receiving}.html
verified against: <commit> · <date>

## How it works today     — only what is true, present tense
## How it should work     — the target, and why
## Gaps                   — a table; every row also appears in gaps.md
```

## Rules

- **Touching a file means updating its doc's `verified against` line in the
  same commit.** A doc that lies is worse than none, because it gets believed.
- **Write a doc as a by-product of working in that area**, not cold. Fifteen
  docs written speculatively would be stale before the last was finished.
- **Gaps carry evidence** — a query or a `file:line`, never an assertion — and
  **name its source**. Code → business rules → production → local development
  data. See [`templates/`](templates/README.md).
- **The laptop runs no scheduled jobs; production runs them all.** Stale
  timestamps, empty tables and jobs that look like they never ran are expected
  locally and are never on their own evidence of a defect. Only the code can
  prove one.
- **Classify before recommending.** Missing implementation, bug, configuration,
  missing operational process, or legacy data. Absence of data is not a defect.
- **Aspiration lives in "How it should work"**, never in "How it works today".
  Mixing them is how a doc stops being trusted.
- **Documents describe the business, not the code.** Where the implementation
  does not match the business boundary, say what should be true and link the
  `ARCH-` id — do not bend the documentation to fit today's file layout.
- **Every change updates its docs in the same commit**: the feature document,
  the gap log, the decision log where a choice was made, and the mismatch
  register where one was created or closed.

## What exists so far

`map.md`, `architecture-mismatches.md`, `gaps.md`, `templates/` and this index.

**[inventory](inventory/README.md) is complete and frozen** — ten feature
documents, 18 open gaps, 20 decisions, and a
[retrospective](inventory/RETROSPECTIVE.md). It is the reference standard for
every section that follows: one business machine per document, business
architecture first, and gaps carrying evidence, a root cause and a
classification.

**[marketing](marketing/README.md) is started** — README and registers, with
the ground truth of both pipelines established. Its six leaves are pending.
Every other section in the table above is still unwritten.

Leaves are written as we work through each section. See `map.md` for the build
order.
