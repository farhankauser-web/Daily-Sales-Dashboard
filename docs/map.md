# Documentation map

The structure of the documentation, and which document to open for a task.

**This map describes the business, not the code.** Where the implementation
does not match, the feature document states what *should* be true and links an
`ARCH-` id in [architecture-mismatches.md](architecture-mismatches.md). Today's
file layout does not get a vote on tomorrow's structure.

Sections are the app's own sidebar sections, so the document you need has the
name you would say out loud.

---

## The tree

Two sections carry most of the weight and are split further. Seven are one
document each. `⚠ ARCH-nnn` marks a document whose business boundary does not
match the code today.

```
CLAUDE.md                          global memory — rules that apply everywhere

docs/
├── README.md                      index + documentation rules
├── architecture.md                the six apps, data flow, shared clients
├── architecture-mismatches.md     technical-debt register
├── deployment.md                  EC2, nginx, gunicorn, cron, TLS
├── gaps.md                        master index of every open gap
│
├── inventory/                     6,795 lines · 15 nav items · active work
│   ├── README.md
│   ├── planner.md                 position, cover, stockout, order-by
│   ├── loading-plan.md            how much to ship on the next container
│   ├── reorder.md                 suggestions → draft POs
│   ├── suppliers.md               suppliers, opening balance
│   ├── purchase-orders.md         PO workbook, production plans, sourcing
│   ├── allocation-workbench.md    packing list → container
│   ├── containers.md              in transit, statuses, history
│   ├── receiving.md               AWD + FC receipts, shortfall
│   ├── transfers.md               FBA transfers, goods-receipt variance
│   ├── cashflow.md                the region ledger — forecast   ⚠ ARCH-005
│   ├── gaps.md
│   └── decisions.md
│
├── marketing/
│   ├── README.md
│   ├── ads-api.md                 AdsAPIClient, report submit/poll/download
│   ├── ams-stream.md              S3 + Firehose → hourly campaign snapshots
│   ├── sku-allocation.md          campaign spend → SKU
│   ├── campaigns.md               campaign centre, profit, detail tabs
│   ├── search-terms.md
│   ├── placements.md
│   ├── gaps.md
│   └── decisions.md
│
├── reporting/                                                    ⚠ ARCH-001
│   ├── README.md
│   ├── daily.md                   the daily dashboard, KPI tiles
│   ├── product-performance.md     the SKU table              ⚠ ARCH-007
│   ├── hourly.md                  hourly patterns, completeness gates
│   ├── historical.md
│   ├── command-center.md          the widget dashboard
│   ├── mcf-orders.md              Amazon MCF order mirror     ⚠ ARCH-006
│   ├── gaps.md
│   └── decisions.md
│
├── financials/                                                   ⚠ ARCH-001
│   ├── README.md
│   ├── pnl.md                     monthly statement, unified txn import
│   ├── cogs.md                    COGS upload, FBA rates, recalc
│   ├── fee-drift.md               settlement actuals vs uploaded fees
│   ├── payouts.md                 Amazon payouts — actuals    ⚠ ARCH-005
│   ├── targets.md
│   ├── gaps.md
│   └── decisions.md
│
├── walmart/
│   ├── README.md
│   ├── orders.md                  import, validate, hold, reprocess
│   ├── mcf-pipeline.md            the state machine, tracking, reconcile
│   ├── gaps.md
│   └── decisions.md
│
├── brand-analytics/                                              ⚠ ARCH-003
│   ├── README.md
│   ├── search-queries.md
│   ├── market-share.md
│   ├── baskets.md
│   ├── gaps.md
│   └── decisions.md
│
├── intelligence/
│   ├── README.md
│   ├── ai-recommendations.md
│   ├── profit-alerts.md
│   ├── morning-report.md
│   ├── gaps.md
│   └── decisions.md
│
├── supply-chain/                  Atlas — the B2B arm
│   ├── README.md
│   ├── quotes.md · rfqs.md · purchase-orders.md · invoices.md
│   ├── gaps.md
│   └── decisions.md
│
└── settings/
    ├── README.md
    ├── credentials.md             API keys, marketplace config   ⚠ ARCH-004
    ├── users-roles.md
    ├── catalog.md
    ├── gaps.md
    └── decisions.md
```

**Technical documents** (`containers-tech.md`, `sku-allocation-tech.md` …) are
written only when a feature document would otherwise have to carry
implementation. They reference stable identifiers —
`procurement.commit_packing_list`, `ContainerAllocator.allocate` — never line
numbers.

---

## Context loading

A normal task loads CLAUDE.md, one section README, one or two feature
documents, and that section's gap log. Checked against real work:

| Task | Documents | |
|---|---|---|
| Container receiving | CLAUDE · inventory/README · receiving · containers · gaps | 5 ✓ |
| SKU PPC allocation | CLAUDE · marketing/README · sku-allocation · ams-stream · gaps | 5 ✓ |
| VAT on the SKU table | CLAUDE · reporting/README · product-performance · gaps | 4 ✓ |
| Cash-flow FOB | CLAUDE · inventory/README · cashflow · containers · gaps | 5 ✓ |
| Walmart reprocess | CLAUDE · walmart/README · orders · gaps | 4 ✓ |

Within budget. Note what the documents can and cannot do: they make the first
grep unnecessary and the second precise. Until `ARCH-001` is worked through, a
Reporting or Financials task still lands in a 7,590-line file — the document
tells you which function, not how big the file is.

---

## Build order

1. `CLAUDE.md` — **done** (`a666708`)
2. `docs/README.md` — **done** (`a666708`)
3. `architecture-mismatches.md` — **done**
4. This map — **done**
5. **`inventory/`** — active work, and most of the content already exists from
   the last week. README, `containers.md`, `receiving.md`,
   `allocation-workbench.md`, `suppliers.md`, `purchase-orders.md`, plus
   `decisions.md` backfilled with what has already been settled.
6. `marketing/` — the largest undocumented machinery, and the source of the
   subtlest bugs so far.
7. `reporting/`
8. `financials/`
9. The rest, as sessions touch them.

`architecture.md` and `deployment.md` are written alongside step 5 —
`deployment.md` pairs with `INFRA-01`, the highest open gap.

Per-section `gaps.md` files are seeded by splitting the master `gaps.md`,
keeping the IDs already in use.

---

## Living documentation

Every feature, enhancement or refactor updates, in the same commit:

- the relevant **feature document** — including its `verified against` line
- the relevant **gap log** — closing a gap keeps the row and records the commit
- the **decision log**, when a choice was made that a future session might
  otherwise revisit
- **`architecture-mismatches.md`**, when a mismatch is created, discovered or
  closed

A document that lies is worse than no document, because it gets believed.

---

## Decisions already taken, to be recorded in section decision logs

Carried from the week of 2026-08-05 so they are not revisited:

| Decision | Section |
|---|---|
| Margins measured ex-VAT; extract with `÷(1+rate)`, revenue stays gross | financials |
| The packing list is truth; variance is packed − received, never declared − received | inventory |
| One packing list describes a whole container, including two suppliers | inventory |
| Uploads ask for the SKU only; category, name and FNSKU are derived | inventory |
| Container FOB is entered per unit in the **region's** currency, never converted | inventory |
| A missing FOB is refused, never inferred from the PO — the currencies differ | inventory |
| Container FOB is snapshotted at allocation, not read live from the PO | inventory |
| Amazon's count never overwrites a human count | inventory |
| Existing containers are not backfilled with rates — forward-only | inventory |
| Amazon CLOSING a shipment moves the container to history; the app values no loss | inventory |
| `apps/sqp` is superseded by the dashboard BA models | brand-analytics |
