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
| Marketing | `marketing/` | PPC — see that folder's README |
| Brand Analytics | `brand-analytics.md` | search-query performance, market share, baskets |
| Walmart | `walmart.md` | Walmart orders → Amazon MCF |
| Supply Chain | `supply-chain.md` | **Atlas — the B2B arm.** Quotes, RFQs, B2B POs, invoices |
| Inventory | `inventory/` | planner, suppliers, POs, containers, receiving — see that folder's README |
| Intelligence | `intelligence.md` | AI recommendations, profit alerts |
| Settings | `settings.md` | API credentials, users, roles, catalog |
| — | `architecture.md` | how the six apps fit together, and how data flows |
| — | `deployment.md` | EC2, nginx, gunicorn, cron, TLS |
| — | **`gaps.md`** | **every open gap across all sections — the backlog** |

**Careful:** in this app *Supply Chain* means Atlas. Containers, suppliers,
purchase orders, allocation and receiving all live under **Inventory**.

## The shape of a leaf

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
- **Gaps carry evidence** — a query or a `file:line`, never an assertion.
- **Aspiration lives in "How it should work"**, never in "How it works today".
  Mixing them is how a doc stops being trusted.

## What exists so far

`gaps.md` and this index. The leaves get written as we work through them —
`inventory/containers.md` and `inventory/suppliers-pos.md` are next, since
that is where the current work is.
