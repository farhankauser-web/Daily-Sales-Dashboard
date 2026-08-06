# Proposed documentation map — for review

Not documentation. This is the map to approve before any leaf is written.

**Method.** Inventoried all nine Django apps, every named URL route (~110), the
sidebar sections in `templates/base/base.html`, and line counts per file. The
section names below are the app's own sidebar sections, so the doc you need has
the name you would say out loud.

---

## 1 · Sizing — where the mass actually is

| Section | Primary code | ~lines |
|---|---|---|
| **Inventory** | `apps/inventory_planning` | **6,795** · 15 nav items |
| **Marketing** | ppc_allocator, ams_consumer, hourly_aggregator, campaign views | ~3,500 |
| **Walmart** | `apps/walmart_mcf` | 3,274 |
| Reporting | dashboard daily/historical/hourly + `command_center` | ~3,000 |
| Financials | pnl_engine, unified_txn_importer, COGS, fee drift | ~2,500 |
| Supply Chain | `apps/atlas` | 2,041 |
| Brand Analytics | `apps/sqp` 1,934 + dashboard BA models/views | ~3,100 |
| Settings | `apps/amazon_api` config + `apps/users` | ~1,500 |
| Intelligence | ai_insights, alerts, morning report | ~1,200 |

Two sections carry most of the weight and are split further. Seven are one
document each.

---

## 2 · Where code boundaries do not match business boundaries

This is the part worth reading. Six mismatches, in order of how much they cost.

**M1 — `apps/dashboard/views.py` (7,590 lines) serves SIX sections.**
Reporting (`index`, `historical`, `hourly_patterns`, `product_line`),
Financials (`cogs`, `fba_fee_drift`, `pnl_*`, `targets`),
Marketing (`campaigns`, `search_terms`, `placements`, `leaderboards`),
Brand Analytics (`ba_*`),
Intelligence (`ai_recommendations`, `morning_report`, `alerts`, `summary`),
Settings (`catalog`, `product_create`, `product_edit`).
Six section docs will all point into one unreadable file. Logged as `REP-01`.

**M2 — `apps/dashboard/models.py` (2,071 lines) holds every domain's tables.**
`DailyMetric`, the PPC snapshots, the five `BA*Weekly` models, the P&L models
and `FBAFeeRate` all live together. A `database.md` per section is impossible
until this splits.

**M3 — Search Query Performance exists TWICE.**
`apps/sqp` (1,934 lines · `SQPReport`, `SQPQuery`, `SQPSnapshot`, its own AI
insight cache, mounted at `/sqp/`) **and** `dashboard.BASearchQueryWeekly` with
the `ba_queries` page. Neither my earlier plan nor the proposed tree noticed
`apps/sqp` at all. **Which one is live?** This must be answered before
`brand-analytics/` is written, or the doc will describe a system nobody uses.

**M4 — `apps/amazon_api` (7,887 lines) is two things.**
The credentials UI (`list`, `create`, `edit`, `test`, `anthropic`,
`ai_provider`) is Settings. `data` — `fetch_dashboard_data`, which contains
four separate SKU-table builders — is the Reporting engine. `services.py` is
the shared SP-API/Ads client every section calls. The client belongs in
`architecture.md`, not in any one section.

**M5 — "Cash flow" names two different machines.**
`dashboard.cash_flow` (per-marketplace balance from Amazon payouts) and
`inventory_planning.cashflow` (the region ledger: opening balance + Amazon
inflow − container payments). Both are in the nav. A reader searching "cash
flow" will find the wrong one half the time.

**M6 — MCF appears twice.**
`apps/walmart_mcf` (the state machine, pipeline, orders page) and
`dashboard.mcf_orders` / `api_mcf_sync`. Both have nav entries
(`nav_walmart`, `nav_mcf`).

---

## 3 · Proposed tree

Changes from the version proposed for review are marked **[+]** added,
**[~]** renamed, **[−]** dropped, with the reason.

```
CLAUDE.md                          ← exists (83 lines)

docs/
├── README.md                      ← exists (index + rules)
├── gaps.md                        ← exists (master index, 18 open)
├── architecture.md            [+] six apps, data flow, the shared SP-API client
├── deployment.md              [+] EC2, nginx, gunicorn, cron, TLS
│
├── inventory/                     6,795 lines — active work, build first
│   ├── README.md
│   ├── planner.md                 planner, runway, reorder, loading plan
│   ├── suppliers.md               suppliers, opening balance
│   ├── purchase-orders.md         PO workbook, sourcing, balances
│   ├── production.md              production plans, PP numbering
│   ├── allocation-workbench.md    packing list → container
│   ├── containers.md              in transit, statuses, history
│   ├── receiving.md               AWD + FC receipts, shortfall
│   ├── transfers.md               FBA transfers, goods-receipt variance
│   ├── cashflow.md                the region ledger  ⚠ see M5
│   ├── gaps.md
│   └── decisions.md
│
├── marketing/
│   ├── README.md
│   ├── ads-api.md                 AdsAPIClient, report submit/poll
│   ├── ams-stream.md          [+] S3 + Firehose → hourly snapshots. A separate
│   │                              machine from the Ads API and the biggest
│   │                              source of bugs so far — deltas not
│   │                              restatements, snake_case payloads, flock
│   ├── sku-allocation.md      [~] was campaign-allocation.md. It allocates
│   │                              campaign spend down to SKUs
│   ├── campaigns.md
│   ├── search-terms.md
│   ├── placements.md
│   ├── gaps.md
│   └── decisions.md
│                              [−] hourly-reporting.md → belongs in reporting/
│
├── reporting/
│   ├── README.md
│   ├── daily.md                   the daily dashboard + KPI tiles
│   ├── product-performance.md [+] the SKU table — four builders across two
│   │                              apps, the source of the VAT bugs
│   ├── hourly.md                  hourly patterns, completeness gates
│   ├── historical.md
│   ├── command-center.md      [+] the widget dashboard (own app, no doc)
│   ├── gaps.md
│   └── decisions.md
│                              [−] dashboard.md → overlapped daily.md
│                              [−] executive-dashboard.md → moved to
│                                  intelligence/morning-report.md
│
├── financials/
│   ├── README.md
│   ├── pnl.md                     monthly statement, unified txn import
│   ├── cogs.md                    COGS upload, FBA rates, recalc
│   ├── fee-drift.md               settlement vs uploaded fees
│   ├── targets.md
│   ├── payouts.md             [+] Amazon payouts + the marketplace balance
│   │                              page  ⚠ see M5
│   ├── gaps.md
│   └── decisions.md
│
├── walmart/          README · orders.md · mcf-pipeline.md · gaps · decisions
├── brand-analytics/  README · search-queries.md · market-share.md ·
│                     baskets.md · gaps · decisions        ⚠ blocked on M3
├── intelligence/     README · ai-recommendations.md · profit-alerts.md ·
│                     morning-report.md · gaps · decisions
├── supply-chain/     README · quotes.md · rfqs.md · purchase-orders.md ·
│                     invoices.md · gaps · decisions       (Atlas — B2B)
└── settings/         README · credentials.md · users-roles.md · catalog.md ·
                      gaps · decisions
```

**Tech docs** (`containers-tech.md` etc.) only where a feature doc would
otherwise carry implementation. Written on demand, not upfront. Stable
identifiers — `procurement.commit_packing_list`, not line numbers.

---

## 4 · Context budget — does this meet the 4–5 document rule?

| Task | Documents loaded | |
|---|---|---|
| Container receiving | CLAUDE.md · inventory/README · receiving.md · containers.md · inventory/gaps.md | 5 ✓ |
| SKU PPC allocation | CLAUDE.md · marketing/README · sku-allocation.md · ams-stream.md · marketing/gaps.md | 5 ✓ |
| VAT on the SKU table | CLAUDE.md · reporting/README · product-performance.md · reporting/gaps.md | 4 ✓ |
| Cash flow FOB | CLAUDE.md · inventory/README · cashflow.md · containers.md · inventory/gaps.md | 5 ✓ |

Within budget — **provided M1 is eventually fixed.** Until `dashboard/views.py`
is split, a Reporting or Financials task still means grepping 7,590 lines
however good the doc is. The docs make the grep precise; only the split makes
it small.

---

## 5 · Build order

1. `architecture.md` — the map that says which leaf to open. Cheap, unblocks everything.
2. `inventory/` README + `containers.md` + `receiving.md` + `allocation-workbench.md` + `suppliers.md` + `purchase-orders.md` — this month's work, mostly already established.
3. `inventory/decisions.md` — backfilled from the last week: packed-not-declared, one file per container, opening-before-PO, forward-only FOB, region currency.
4. `deployment.md` — pairs with `INFRA-01`, the highest open gap.
5. `marketing/` — biggest undocumented machine, and the one that has produced the most subtle bugs.
6. Everything else as sessions touch it.

Per-section `gaps.md` files get seeded by splitting the existing master
`docs/gaps.md`, which keeps the IDs already in use.

---

## 6 · Questions blocking parts of this

1. **`apps/sqp` vs the dashboard BA pages** — which is live? Is one dead code?
   Blocks `brand-analytics/`.
2. **`dashboard.cash_flow` vs `inventory_planning.cashflow`** — two intended
   machines, or is one superseded? Decides whether M5 is a doc problem or a
   code problem.
3. **`dashboard.mcf_orders` vs `apps/walmart_mcf`** — same question.
4. **Cash flow: `inventory/` or `financials/`?** The nav puts it under
   Inventory; the concept is financial. I would follow the nav and cross-link,
   since that is where you go looking for it.
5. **`apps/dashboard` split (M1)** — before the Reporting and Financials docs,
   or after? Doing it first makes those docs describe a clean structure;
   doing it after means writing them twice.
