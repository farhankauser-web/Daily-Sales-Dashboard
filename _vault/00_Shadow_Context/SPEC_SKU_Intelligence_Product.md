# SKU Intelligence — Product Specification (final recommended UX)

**Status:** design only, no code. Second-pass on `AUDIT_SKU_Campaign_Intelligence.md`.
**Date:** 2026-08-17. One recommended UX, alternatives evaluated inline.

**The product in one sentence:** turn `pnl_skus` into a triage workspace where a
manager running 200+ SKUs goes *attention → SKU → why → campaign → target →
action* in one thread, reusing Pulse's existing engines with exactly one new
read endpoint per drill level.

---

## A. Existing functionality we reuse (verbatim)

- `SkuPpcAllocation` — SKU-level PPC spend, per campaign, with `attribution_source`, `confidence_score`, `settlement_state`. **The spine's backbone.**
- `AdsAdvertisedProductDailySnapshot` — campaign→ASIN/SKU sales. Covers **SP + SB + SD** (verified in `ads_detail_reports.py`), with one caveat: **SB rows carry ASIN but no SKU** (`purchasedProduct` has no SKU field; 14d attribution window vs SP/SD 7d).
- `DailySkuSnapshot` — authoritative SKU total revenue/units/COGS/fees.
- `CampaignProfitDaily`, `Campaign` dim — campaign KPIs + brand/family parsing.
- `campaign_detail` page + its 4 APIs (top-SKUs, targeting, daily, hourly) — unchanged.
- `_tag_search_term` + `_ST_ACTION` — deterministic term tagging/actions.
- `AIRecommendation` — already has `scope_type='sku'` + `scope_id` (verified).
- `StiOpportunity` — has `subject` (term/node/SKU) + auditable `evidence` JSON.
- `CampaignBudgetUsageDaily` — exact budget caps.
- Optimizer's compound numeric filter + Excel export pattern.

## B. Current problems (from audit, unchanged)

1. No SKU→Campaign reverse drill (the broken link).
2. Search terms on 3 surfaces; SKU performance on 3 surfaces.
3. SKU table is flat, filed under Financials, answers "which" but never "why".
4. Confidence/settlement/attribution exists in data but is invisible in UX.
5. Navigation is a list of 7 flat pages, not a workflow.

---

## C. The user workflow (designed from the 7 questions)

A PPC manager with 200 SKUs does **triage**, not browsing. The workflow:

```
L1 "What needs attention?"   → Hub header: 4 KPIs + 3 attention counts + Attention list
L2 "What is happening?"      → SKU table row: trend arrows + status chips
L3 "Why?"                    → Expand row: PPC-vs-organic split + trend + what changed
L4 "Which campaigns?"        → Campaign Drivers block (same expanded view)
L5 "Why this campaign?"      → link → existing Campaign detail (period carried)
L6 "Which targets/terms?"    → Campaign detail's targeting/search-term tabs (exists)
L7 "What should I do?"       → Opportunities strip in the expanded view + STI/AI links
```

Design rule: **L1–L4 happen without leaving the page.** L5–L7 reuse existing
pages, entered with context (SKU + period pre-filtered). Nothing to remember,
nothing to re-search.

An improvement over the questions-as-given: L3 and L4 are answered **together**
— "why" for an advertised product almost always *is* the campaign mix, so
splitting them into separate screens adds a click for nothing.

---

## D. Information architecture — Marketing navigation

```
Marketing
├── SKU Intelligence        ← the new default landing (the hub, this spec)
├── Campaigns               ← existing campaigns_list + campaign_detail (KEEP)
├── Search Terms            ← existing explorer page (KEEP as the deep-dive)
├── Search Intelligence     ← STI opportunity engine (KEEP)
├── Budget & Pacing         ← Optimizer, budget tab only (terms tab retired)
├── Placements              ← unchanged
└── Hourly Patterns         ← stays in Reporting (cross-links from campaign detail)
```

Verdict on the audit's "Overview" page: **cut it.** The hub's header row *is*
the overview; a separate Overview page would be a dashboard nobody opens twice.
Same for a standalone "Opportunities" nav item: opportunities surface
contextually (in the SKU row + STI page already exists). Fewer pages, not more.
Leaderboards → retire nav item; its boards become saved sorts of the hub and
Campaigns tables. Legacy PPC Analytics → redirect to Campaigns.

---

## E. SKU Intelligence page (the hub)

### E1. Header strip (answers L1 — before touching the table)

Exactly 4 KPIs + 3 attention counters. Every one answers a question:

| KPI | Question answered | Source |
|---|---|---|
| Revenue (w/ Δ vs prior period) | Is the business growing? | `DailySkuSnapshot` Σ |
| Ad Spend + TACoS (w/ Δ) | Is ad investment proportionate? | `SkuPpcAllocation` Σ / revenue |
| PPC sales vs Organic sales (split bar, w/ Δ) | Is growth bought or earned? | E3 methodology |
| Contribution Margin (w/ Δ) | Are we making money? | `DailySkuSnapshot` costs |

| Attention counter (clickable → filters table) | Definition (deterministic) |
|---|---|
| 🔴 Losing money | CM < 0 in window, revenue > de-minimis floor |
| 🟠 Efficiency declining | ACOS up >20% rel. vs prior equal window AND spend > floor |
| 🟡 Opportunity | has open STI opportunity OR `scaling_opportunity` tag |

No "SKUs growing/declining" counters — the trend arrows in the table carry that;
a counter would duplicate it (rule 17: every component answers one question once).

### E2. The SKU table (answers L2 at a glance)

Decision-dense scan for 200+ rows. **12 visible columns**, grouped:

| Group | Columns | Notes |
|---|---|---|
| Product | SKU (+ASIN under it), Product name | one cell, two lines — saves a column |
| Business | Revenue **with Δ% arrow**, Units, CM% **with Δ arrow** | Δ vs prior equal window |
| PPC | Ad spend **with Δ**, ACOS **with Δ**, ROAS | spend from SkuPpcAllocation |
| Mix | **PPC-dependency bar** (PPC share of revenue, 0–100% mini-bar) | the "bought vs earned" signal |
| Status | Status chips (see E4), Confidence dot (see K) | |
| | (▸ expander) | opens the SKU panel (F) |

Dropped from the candidate list and why: separate PPC-sales and organic-sales
columns (the dependency bar + drill covers it; two more number columns hurt
scanning); orders (units suffices at SKU level); sparkline-per-row at 200 rows
(costly, low marginal value over Δ arrows — sparkline lives in the expanded panel).

Filters (reuse existing patterns): marketplace · period presets + custom dates ·
brand · status chip · attention filter (from counters) · compound numeric
filters (`acos > 40`, `spend >= 100 and orders = 0` — Optimizer's `_parse_conds`
pattern) · text search. Excel export honouring filters (Optimizer's `mkt_export`
pattern). Default sort: **ad spend desc** (money at risk first); saved sorts
replicate Leaderboards' boards.

### E4. Status chips — deterministic, no invented score (rule 6)

**No composite health score.** Not enough independent signals to make one
honest, and a black-box number erodes trust. Instead, explicit statuses, each
computed from existing metrics with visible thresholds (thresholds configurable
in one settings dict, not per-user):

| Chip | Rule (window vs prior equal window) | Data |
|---|---|---|
| `LOSING` | CM < 0 and spend > $25 | DailySkuSnapshot + SkuPpcAllocation |
| `ACOS↑` | ACOS worsened >20% relative, spend > $25 | same |
| `PPC-DEPENDENT` | PPC share of revenue > 70% | E3 split |
| `ORGANIC↓` | organic revenue down >20% while PPC revenue flat/up | E3 split |
| `SCALING` | ACOS < target and revenue Δ > +15% | same |
| `CAPPED` | ≥30% of days budget-capped on a driver campaign | CampaignBudgetUsageDaily |
| `LOW-CONF` | allocation confidence < 0.5 for >30% of spend | SkuPpcAllocation |

A SKU can carry multiple chips; table shows max 2 + "+n". Same rules power the
header counters — one implementation, two surfaces.

---

## F. SKU detail experience — Option evaluation and recommendation

| Option | Density | Nav ease | Campaign drill | 200-SKU scale | Verdict |
|---|---|---|---|---|---|
| A. Expandable row | medium | best (stay in list, compare siblings) | good (block in panel) | best (nothing loads until expand) | **✔ primary** |
| B. Side drawer | medium | good, but covers the table and its filters | good | good | ✘ steals width needed for a wide table |
| C. Full SKU workspace | highest | worst (leaves triage; back-button churn) | best | poor for triage | ✘ as default — overbuild today |
| D. Hybrid (A now, C later) | — | — | — | — | **✔ chosen path** |

**Recommendation: D — expandable row now, permalink-ready.** The expanded panel
is triage-sized (~450px). Panel content is one lazy API call keyed
`(mp, sku, period)`; the row URL (`?sku=…&period=…`) is shareable, and if a
full workspace is ever justified (P3), the same API feeds it. A drawer is the
worse version of both options; skip it.

### F2. Expanded panel layout (answers L3+L4+L7 in one view)

```
┌─ SKU header: name · ASIN · chips · confidence badge ──────────────────────┐
│ ① WHAT CHANGED  (one sentence, computed, not LLM):                        │
│    "Revenue -18% vs prior 30d. PPC sales flat; organic -31%.              │
│     Spend +12% → ACOS 34% (was 27%)."                                     │
│ ② TREND chart (one chart): daily revenue split PPC vs organic (stacked    │
│    area) + ad-spend line overlay. 90d fixed lookback.                     │
│ ③ CAMPAIGN DRIVERS  (section G — table + contribution bars)               │
│ ④ TOP/WORST SEARCH TERMS for this SKU's driver campaigns (5+5, from the   │
│    shared term service, tagged with suggested actions) → "open in         │
│    Search Terms ↗" carries filters                                        │
│ ⑤ OPPORTUNITIES strip (section J): STI + AI recs scoped to this SKU +     │
│    2 deterministic driver-mix flags                                       │
└───────────────────────────────────────────────────────────────────────────┘
```

The "what changed" line ① is deterministic template-fill from the same deltas
the chips use — no model call, always auditable, instant.

---

## G. Campaign Drivers (the critical new piece)

### G1. Table — for the selected SKU + period

| Column | Why |
|---|---|
| Campaign (name, type badge SP/SB/SD, state) | identity |
| Spend ($, and **% of SKU spend** as inline bar) | where the money goes |
| PPC sales ($, and **% of SKU PPC sales** as inline bar) | what it buys |
| ACOS · ROAS | efficiency |
| Orders / Units | volume sanity |
| Δ spend / Δ sales vs prior window | direction |
| Attribution badge (source + confidence, see K) | honesty |
| → open campaign | L5 link |

**Default sort — evaluated:** by spend puts the biggest *cost* first but hides
small efficient drivers; by PPC-sales contribution hides waste. **Recommendation:
sort by spend-share desc, but the visual (G2) makes the mismatch the headline** —
the eye finds "high spend-share, low sales-share" instantly, which is the actual
question. Secondary saved sorts: sales-share, ACOS.

### G2. Visualization — evaluated, one chosen

Bubble/waterfall/matrix rejected: they demand interpretation. **Chosen: paired
horizontal bars** — one row per campaign, two aligned bars: **% of SKU ad spend**
vs **% of SKU PPC sales**. When the top bar is much longer than the bottom, that
campaign is consuming without contributing; the reverse = underfunded winner.
Answers both target questions in one glance, renders as plain HTML divs (no
chart lib), degrades gracefully at 1 or 30 campaigns. The table rows and the
bars are the same rows — bars live inside the two share columns, so it's one
component, not a chart plus a table.

### G3. Data path

`SkuPpcAllocation` filtered `(mp, sku, date range)` grouped by `campaign_id`:
spend, weighted confidence, dominant source. Joined with
`AdsAdvertisedProductDailySnapshot` `(mp, advertised_sku, campaign_id)` for
sales/orders/units — **SP+SD by SKU; SB joined by ASIN** and badged
"SB — ASIN-level, 14d window" (an ASIN's SB sales can span child SKUs; shown,
labelled, never silently merged). Campaign names/types from `Campaign` dim.
One endpoint: `api_sku_campaigns(mp, sku, start, end)`.

---

## H. Transition to Campaign detail (L5–L6)

`campaign_detail` already answers L5/L6 (daily trend, targeting, search terms,
placements, hourly, top SKUs). Changes: **none to the page.** The link from the
driver row carries `?mp=&period=&from_sku=` — the period maps to the nearest
campaign-period preset (or custom dates pass through), and a small breadcrumb
"← back to TWL-HND-WHT-6" renders when `from_sku` is present, restoring the
hub's scroll/filter state on return (sessionStorage). That breadcrumb is the
entire integration cost — the seamlessness *is* the feature.

## I. Search/targeting transition + the three term surfaces

Roles get sharp edges instead of deletions:
- **Search Terms page = the explorer** (unchanged, deep filters, full corpus).
- **STI = opportunities** (stored runs, scoring, narratives — unchanged).
- **Optimizer = Budget & Pacing only.** Its terms tab is retired after the
  shared service ships (its Excel export + filters move to Search Terms page
  if not already equivalent — verify at build time).

One shared **search-term service** (extract from `views_marketing._search_term_rows`
+ `_tag_search_term`) feeds: Search Terms page, the SKU panel's block ④, and
campaign detail. Block ④ shows the top/worst 5 terms across the SKU's driver
campaigns; "open in Search Terms ↗" pre-fills campaign + period filters. The
user never needs to remember which page owns terms — the thread hands them over.

## J. Opportunities in context (L7)

Panel strip ⑤ merges three real sources, deduped, max 5, each with a "why"
line citing its numbers (STI already stores `evidence` for exactly this):

1. **STI:** `StiOpportunity` where `subject` matches the SKU/ASIN or whose
   product group contains it, `status='open'`.
2. **AI recs:** `AIRecommendation` where `scope_type='sku'`, `scope_id=sku`
   (verified to exist), plus campaign-scoped recs whose campaign is a driver.
3. **Deterministic driver-mix flags** (computed in `api_sku_campaigns`, from
   your examples — all computable from existing data):
   - underfunded winner: sales-share ≥ 2× spend-share and ACOS < target and campaign not budget-capped → "consider budget";
   - inefficient heavyweight: spend-share ≥ 2× sales-share and spend > floor → "inspect targets";
   - organic decline: `ORGANIC↓` chip logic → "check rank/price/content";
   - starved term: a driver campaign's term with CVR above campaign median but bottom-quartile impressions → "increase exposure".

No invented recommendations; every card links to its source (STI page, AI recs
page, or the driver row it derives from).

## K. Data integrity in the UX (non-negotiable)

- **Confidence dot** on every SKU row: ● ≥0.8 · ◐ 0.5–0.8 · ○ <0.5 (spend-weighted mean of `SkuPpcAllocation.confidence_score`). Tooltip: source mix + settlement.
- **Attribution badge** per driver row: `SP` (authoritative) / `SP~` (provisional) / `SB (ASIN, 14d)` / `SD` / `group-share` / `reconciled` — direct from `attribution_source`.
- **Settlement banner** on the panel when the window includes non-`locked` days: "Includes provisional days — figures may still move (T+3)."
- **Today:** campaign→SKU attribution is T-1; period presets for this page **end at yesterday** (same rule Campaigns already applies), so we never show a hole.
- **Organic split labelled honestly:** `organic = DailySkuSnapshot.revenue − Σ advertised sales (SP 7d + SD 7d by SKU; SB 14d by ASIN)`. Mixed windows + ASIN-level SB make it an **estimate** — the UI says "Organic (est.)" with the methodology tooltip, and clamps at ≥0 with a `⚠ ad-sales exceed total` flag when attribution windows overshoot (it happens; hiding it would be lying).

## L. Consolidation plan

| Surface | Action |
|---|---|
| `pnl_skus` (SKU Profitability) | **IMPROVE + MOVE** → becomes SKU Intelligence hub under Marketing. Old Financials URL redirects. |
| `SkuPpcAllocation`, `campaign_detail`, Campaigns list, STI, Search Terms page, Budget pacing, Placements, BA pages | **KEEP** |
| `_search_term_rows` + `_tag_search_term` | **CONSOLIDATE** → shared service, three consumers |
| Optimizer terms tab | **DEPRECATE** after service ships (page keeps budget tab; renamed "Budget & Pacing") |
| Leaderboards | **DEPRECATE** nav item → saved sorts in hub + Campaigns |
| Legacy PPC Analytics (`vi.ppc_analytics`) | **REMOVE** nav + redirect to Campaigns |
| Nav "Marketing" section | **REBUILD** per D |

## M. Implementation phases

**P0 — the spine** (each step shippable alone):
1. `api_sku_campaigns` endpoint (G3) — new, read-only; no models, no migrations.
2. Expandable row + panel ①②③ (what-changed, trend w/ organic split, drivers table+bars).
3. Move page to Marketing nav + redirect; period presets end at T-1.
4. Campaign-detail breadcrumb (`from_sku`).
*Reuses: SkuPpcAllocation, AdsAdvertisedProduct, DailySkuSnapshot, Campaign dim, campaign_detail. New backend: 1 endpoint. New data: none.*

**P1 — triage power:** header KPIs + attention counters; status chips + confidence dots (shared rules module); compound filters + Excel export (port Optimizer patterns); PPC-dependency column.

**P2 — context:** shared search-term service + panel block ④; opportunities strip ⑤ (STI/AI/deterministic flags); retire Optimizer terms tab + Leaderboards nav; settlement banner.

**P3 — future:** full SKU workspace page (same APIs) if triage panel proves insufficient; true SKU TACoS (needs ad-rev vs total-rev reconciliation per SKU); cross-marketplace SKU rollup; any Amazon write-back (explicitly out of scope now).

## Performance & scale (rule 18)

- Hub table: one aggregate endpoint, server-side, capped ~500 rows w/ limit param (existing `api_pnl_skus` already does this); Δ columns = second aggregate over prior window, same query shape. Both windows in one response.
- Panel: lazy — nothing fetched until a row expands; `api_sku_campaigns` hits indexed paths (`ix_sku_ppc_dash_idx`; `(marketplace, advertised_sku, -date)` index exists).
- Chips/counters: computed in the hub endpoint from data already in hand — no extra queries.
- Search-term block: driver campaign_ids + `(mp, campaign, date)` filter on the indexed snapshot, limit 10.
- Caching: none new in P0; if hub aggregation grows slow on Postgres, add a nightly rollup table then (decide on evidence, not in advance).
- Multi-marketplace stays one-mp-at-a-time (matches every existing page; cross-mp rollup is P3).

---

## The final recommended UX, in one paragraph

One page — **Marketing → SKU Intelligence** — opens on a 4-KPI + 3-counter
header and a 12-column, chip-annotated, Δ-arrowed table of every SKU, default
sorted by money at risk. Clicking a row expands a panel in place: one computed
"what changed" sentence, one PPC-vs-organic trend chart, the **campaign drivers
table with paired spend-share/sales-share bars**, the SKU's best/worst search
terms, and a max-5 opportunities strip — every number carrying its attribution
badge and confidence dot. One click on a driver opens the existing Campaign
detail with a breadcrumb home; one click on a term opens the existing Search
Terms explorer pre-filtered. No new score, no new dashboard, one new endpoint
per drill level, and every existing engine — attribution, profit cache, STI,
tagging — finally pulling in the same thread.
