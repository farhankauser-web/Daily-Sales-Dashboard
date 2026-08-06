# Architecture mismatch register

Where the code's structure disagrees with the business's structure. This is the
technical-debt register: the documentation describes the **business** boundary,
and every place the implementation does not match it is recorded here.

A mismatch is not a bug. Nothing here is broken for a user today. Each one is a
place where the next change costs more than it should, or where a reader is
likely to be misled.

**Refactoring happens when a feature is touched, not to satisfy this file.**
An entry sitting at `open` for months is fine if nobody is working in that area.

Effort is measured in **sessions** — a session being one focused piece of work
with its own verification — because that is what actually constrains us.

Seeded 2026-08-05 from the codebase at `85e4438`.

| ID | Mismatch | Priority | Effort | Status |
|---|---|---|---|---|
| `ARCH-001` | `dashboard/views.py` serves six business sections | P1 | 4–6 | open |
| `ARCH-002` | `dashboard/models.py` holds every domain's tables | P2 | 2–3 | open |
| `ARCH-003` | Search Query Performance implemented twice; one is dead | P1 | 1 | open |
| `ARCH-004` | `amazon_api` conflates Settings, the Reporting engine and the shared client | P2 | 2–3 | open |
| `ARCH-005` | "Cash flow" names two different machines | P3 | 0.5 | open |
| `ARCH-006` | "MCF" names two different features | P3 | 0.5 | open |
| `ARCH-007` | The SKU table has one canonical builder and one straggler | P1 | 1–2 | open |
| `ARCH-008` | Business logic lives in views; no consistent service layer | P2 | ongoing | open |
| `ARCH-009` | Advertised-product data is stored twice, in two generations of table | P2 | 1 | open |

---

## ARCH-001 · `dashboard/views.py` serves six business sections

**Current implementation**
One 7,590-line module contains Reporting (`index`, `historical`,
`hourly_patterns`, `product_line`), Financials (`cogs`, `fba_fee_drift`,
`pnl_*`, `targets`, `cash_flow`), Marketing (`campaigns`, `search_terms`,
`placements`, `leaderboards`), Brand Analytics (`ba_*`), Intelligence
(`ai_recommendations`, `morning_report`, `alerts`, `summary`) and Settings
(`catalog`, `product_create`, `product_edit`).

**Desired architecture**
One module per business section, under a package:

```
apps/dashboard/views/
    __init__.py          re-exports, so urls.py and imports do not change
    reporting.py         daily, historical, hourly, product line, exports
    financials.py        P&L, COGS, fee drift, targets, payouts
    marketing.py         campaigns, search terms, placements, leaderboards
    brand_analytics.py   BA pages and APIs
    intelligence.py      AI recommendations, morning report, alerts
    catalog.py           products, catalogue admin
```

**Why it matters**
A reader cannot load the file. Every task in six sections begins with blind
grepping, and the documentation boundary cannot match the code boundary — six
section docs would all point at one file. It is also how the VAT bug survived:
the same calculation existed in several places and only one was found first.

**Recommended solution**
Split by section, `__init__.py` re-exporting every view so `urls.py` is
untouched and the change is invisible at runtime. One commit per section, with
`manage.py check` plus a request to each affected page after each. Move nothing
else in the same commit — no renaming, no logic changes — so any regression has
exactly one possible cause.

**Do it incrementally, when a section is touched.** The next Financials task
extracts `financials.py`; the next Marketing task extracts `marketing.py`. Do
not schedule it as a project.

**Dependencies** — none. Each slice is independent.
**Priority** P1 · **Effort** 4–6 sessions spread across normal work · **Status** open

---

## ARCH-002 · `dashboard/models.py` holds every domain's tables

**Current implementation**
2,071 lines: `DailyMetric`, `DailySkuSnapshot`, the hourly snapshots, the PPC
campaign/allocation tables, five `BA*Weekly` models, the P&L models,
`FBAFeeRate`, `SettlementReport`, `AmazonPayout`, `McfOrder`.

**Desired architecture**
`apps/dashboard/models/` mirroring the views split — `sales.py`, `ppc.py`,
`brand_analytics.py`, `financials.py`, `catalog.py` — with `__init__.py`
re-exporting so every `from .models import X` keeps working.

**Why it matters**
No section can own its schema, so a per-section `database.md` is impossible.
Reading the tables for one domain means paging through five others.

**Recommended solution**
Same shape as ARCH-001, and **after** it — the views split establishes the
section names, and doing models first would mean guessing them. Django needs
`app_label` intact; a package with re-exports achieves that with no migration.

**Dependencies** — ARCH-001 (do that first)
**Priority** P2 · **Effort** 2–3 sessions · **Status** open

---

## ARCH-003 · Search Query Performance implemented twice

**Current implementation**
`apps/sqp` — 1,934 lines, mounted at `/sqp/`, with `SQPReport`, `SQPQuery`,
`SQPSnapshot`, `AIInsightCache`, `AIInsightHistory` and four migrations.
Separately, `dashboard.BASearchQueryWeekly` with the `ba_queries` page.

**Evidence of which is live**

| | `apps/sqp` | dashboard BA |
|---|---|---|
| rows | `SQPReport` **0**, `SQPQuery` **0**, `SQPSnapshot` **0** | `BASearchQueryWeekly` **940**, `BAMarketBasketWeekly` **296** |
| sidebar | absent — `grep -c sqp base.html` → 0 | four nav entries |
| templates | no `templates/sqp/` directory | present |
| consumers | one: `command_center/widgets.py:466` | the BA pages |

**Desired architecture**
One Search Query Performance implementation. **The dashboard BA models are
canonical** — they hold the data, they are in the navigation, they have pages,
and they were built later.

**Why it matters**
1,934 lines of dead code that a reader must first understand and then discard.
Worse, a Command Center widget reads `SQPSnapshot`, so it renders empty and
looks like a data problem rather than a dead dependency. Any future Brand
Analytics work risks being built on the wrong foundation.

**Recommended solution**
1. Repoint `command_center/widgets.py:466` at `BASearchQueryWeekly`.
2. Remove the `/sqp/` route and `'apps.sqp'` from `INSTALLED_APPS`.
3. Delete `apps/sqp` and its tables in a migration.

Steps 1–2 are reversible and can land immediately; step 3 after a week with
nothing broken. **No business decision here** — the tables are empty, so
nothing is lost.

**Dependencies** — none
**Priority** P1 · **Effort** 1 session · **Status** open

---

## ARCH-004 · `amazon_api` conflates three responsibilities

**Current implementation**
7,887 lines covering the credentials UI (`list`, `create`, `edit`, `test`,
`anthropic`, `ai_provider` — a **Settings** concern), `fetch_dashboard_data`
(the **Reporting** engine, containing several SKU-table builders), and
`services.py` (the SP-API + Ads clients, used by every section).

**Desired architecture**
Three things, named for what they are:

- `apps/amazon_api/` keeps **only** the API clients — infrastructure, no
  business logic, belongs to `architecture.md` rather than any section
- credentials UI moves to Settings
- `fetch_dashboard_data` moves to Reporting (and see ARCH-007)

**Why it matters**
"Where does the dashboard get its numbers?" currently has an answer inside an
app called *amazon_api*, which reads as an integration detail. Two sections'
documentation has to point into it.

**Recommended solution**
Do it in the order the sections get touched: extract the SKU builders when
Reporting is next worked (ARCH-007 covers that), move the credentials UI when
Settings is next worked. Leave `services.py` where it is — it is correctly a
shared client and only the naming misleads.

**Dependencies** — ARCH-007 overlaps
**Priority** P2 · **Effort** 2–3 sessions · **Status** open

---

## ARCH-005 · "Cash flow" names two different machines

**Current implementation**
`dashboard.cash_flow` — per-marketplace balance built from `AmazonPayout`,
i.e. money that has **actually arrived**.
`inventory_planning.cashflow` — the region ledger: opening balance + projected
Amazon inflows − container payments − running costs, i.e. **forecast**.

**Desired architecture**
Both stay. They are different machines, not duplication — actuals versus
forecast — and both are wanted. What is wrong is that they share a name.

- **Amazon Payouts** — `docs/financials/payouts.md`
- **Cash Flow Planner** — `docs/inventory/cashflow.md`

**Why it matters**
Anyone searching "cash flow" finds the wrong one half the time, and a doc
titled `cashflow.md` in two sections would be indefensible.

**Recommended solution**
Rename the page titles and nav labels only. No model or URL changes — the cost
is not worth it for a naming fix, and URLs are bookmarked.

**Dependencies** — none
**Priority** P3 · **Effort** 0.5 session, do it with the next Financials work
**Status** open

---

## ARCH-006 · "MCF" names two different features

**Current implementation**
`dashboard.mcf_orders` reads `dashboard.models.McfOrder` — a mirror of Amazon's
MCF order list per marketplace, read-only.
`apps/walmart_mcf` is the Walmart → Amazon MCF fulfilment pipeline, with its own
state machine, and is where orders are created.

**Desired architecture**
Two features, clearly named: **MCF Orders (Amazon)** under Reporting, and
**Walmart Fulfilment** under Walmart.

**Why it matters**
Two nav entries called variations of "MCF" pointing at unrelated machines. The
Reprocess bug was reported against "MCF" and finding the right code took longer
than fixing it.

**Recommended solution**
Rename nav labels and page titles. Document as two features in two sections,
each cross-referencing the other. No code move.

**Dependencies** — none
**Priority** P3 · **Effort** 0.5 session · **Status** open

---

## ARCH-007 · The SKU table has one canonical builder and one straggler

**Current implementation**
Four code paths were recorded here as producing the Product Performance table.
Re-examined 2026-08-06, they are not four peers:

| Path | Status |
|---|---|
| `_build_cached_skus` (`amazon_api/views.py`) | **canonical** |
| the DailyMetric range path | already calls the canonical builder |
| the live SP-API fallback | **the straggler** — its own builder, still reachable |
| `dashboard.product_line_analysis` | a different machine — see below |

Consolidation has already begun and was not recorded here. The DailyMetric path
was migrated onto `_build_cached_skus`, with a comment saying so: *"re-use the
proven grouping helper that the cache-first path uses."*

**Which one is canonical, and why**
`_build_cached_skus` is the only path that: prefers the PPC allocator's output
where it exists and falls back deliberately where it does not; measures margin
ex-VAT via `net_factor`; distinguishes today (group-level PPC attribution,
because Amazon's per-ASIN report lags a day) from past days (per-ASIN actuals);
and emits the "Unallocated PPC" row. Every one of those is a business rule the
straggler does not implement.

**`product_line_analysis` is not a fourth builder.** It answers a different
question — per-product-group P&L over a historical *range*, against monthly
targets, sourced from the all-orders report rather than the daily snapshots. It
shares a shape with the SKU table, not a purpose. Counting it here overstated
the duplication and would have led to merging two machines that should stay
separate.

**Desired architecture**
One builder for the daily SKU table — the one that already exists. Not a new
module: `_build_cached_skus` carries the business rules and two of the three
callers already use it.

**Why it matters**
This is still the most expensive mismatch by evidence: the ex-VAT margin fix had
to be applied in several places and the first attempt appeared to do nothing
because the page was served by a different path than the one changed. The
straggler is where that risk now lives, and it is one path rather than three.

**Recommended solution**
1. Make the live SP-API fallback call `_build_cached_skus`, or delete the branch
   if the cache and hourly paths now cover every case — establish which before
   touching it.
2. Move `_build_cached_skus` out of `amazon_api/views.py` into Reporting, keeping
   the function intact. That is `ARCH-004`'s concern, and it should not be done
   in the same commit as step 1.
3. Leave `product_line_analysis` alone. If it is later found to duplicate
   something, it duplicates the Financials P&L, not this table.

**Do not design a third builder.** The earlier entry here recommended extracting
a new `sku_table.py` with one builder and migrating four callers; that was
written before the canonical one was identified, and would have created a fourth
implementation on the way to removing three.

**Dependencies** — overlaps ARCH-001 and ARCH-004
**Priority** P1 · **Effort** 1–2 sessions, revised down from 2–3 · **Status** open

---

## ARCH-008 · Business logic lives in views

**Current implementation**
Mixed. `inventory_planning` has a de-facto service layer — `procurement.py`,
`planning.py`, `cashflow.py` hold the rules and the views stay thin. `dashboard`
does not: `views.py` contains margin arithmetic, VAT handling and table
building inline.

**Desired architecture**
The `inventory_planning` pattern everywhere: views parse the request, call a
named service function, and render. Business rules live in modules that can be
tested and read without a request.

**Why it matters**
Logic in a view can only be exercised through HTTP, which is why several bugs
this month were found by driving pages rather than by reading code. It is also
why the rules are hard to document — there is no function to point a doc at.

**Recommended solution**
No refactor for its own sake. Apply the rule going forward: **new business
logic goes in a service module, never in a view.** When a view is edited for
another reason and already contains logic, extract that logic as part of the
change. `inventory_planning` is the reference to copy.

**Dependencies** — none
**Priority** P2 · **Effort** ongoing, absorbed into normal work · **Status** open

---

## ARCH-009 · Advertised-product data is stored twice

**Current implementation**
Two tables hold per-ASIN advertising spend, written by two commands on two
schedules:

| | `PPCProductSnapshot` | `AdsAdvertisedProductDailySnapshot` |
|---|---|---|
| campaign grain | none | `campaign_id` |
| SKU | stored | stored |
| ad products | SP only | SP, SB, SD |
| written by | `backfill_ppc` | `ingest_ads_detail_reports` |

The second is a strict superset of the first. Both are maintained.

**Desired architecture**
One table. `AdsAdvertisedProductDailySnapshot` is canonical — it carries the
campaign grain, covers all three ad products, and is fed by the Phase 1
pipeline that the rest of the detail reports use.

**Why it matters**
The SKU allocator, the most consequential consumer of this data, reads the
weaker table — spreading each campaign's spend across every ASIN advertised that
day (`MKT-ALLOC-002`) and re-deriving a SKU that Amazon already stated
(`MKT-ALLOC-003`). Both are symptoms of the duplication rather than independent
defects. Any future consumer faces the same coin-flip, with nothing in either
model saying which to prefer.

**Recommended solution**
1. Repoint the allocator at the canonical table — this is `MKT-ALLOC-002` and
   delivers the business value on its own.
2. Repoint the remaining readers (`dashboard/views.py`, `amazon_api/views.py`).
3. Stop writing `PPCProductSnapshot` in `backfill_ppc`, leave the table in place
   for a period, then drop it.

Steps 1–2 are reversible. **No business decision here** — the canonical table
contains everything the other does.

**Dependencies** — none
**Priority** P2 · **Effort** 1 session · **Status** open

---

## How this file is maintained

- A new mismatch is recorded the moment it is noticed, with evidence, even if
  nobody intends to fix it.
- Closing an entry means recording the commit and keeping the row.
- **Feature documents describe the business boundary, not this one.** Where the
  code does not match, the feature doc says what *should* be true and links the
  `ARCH-` id. Documentation must not be bent to fit today's implementation.
