# Audit — SKU → Campaign → Target Intelligence (Pulse marketing/PPC)

**Purpose:** Understand what Pulse already does before building a Pacvue-level
SKU→Campaign intelligence workflow. No code written. Recommendation at the end.
**Date:** 2026-08-17 · read-only audit of `infinitee_app(1)`.

**Headline:** Pulse already has ~90% of the *data* and *engines* Pacvue would
need — including a genuinely sophisticated SKU-level PPC attribution engine and
a Pacvue-grade campaign centre. The gap is **connective tissue and
consolidation**, not new analytics. The single most valuable missing piece is a
**SKU → Campaign → Target drill-down**. The biggest cleanup is **three separate
search-term surfaces** and **SKU performance split across three pages**.

---

## 1. Existing functionality map

| Feature | Exists? | Where | Data source | Current UX | Reusable? | Needs work? |
|---|---|---|---|---|---|---|
| Account daily P&L / KPIs | ✅ | `DailyMetric`, `pnl_daily` | SP-API + Ads API | Daily/Historical dashboards | ✅ core | No |
| Per-SKU P&L | ✅ | `pnl_skus` + `api_pnl_skus`, `DailySkuSnapshot` | SP-API sales + COGS + `SkuPpcAllocation` | Flat table, brand+tag filters, ACOS/ROAS/TACoS/CM, tags | ✅ | IMPROVE — flat, no drill, lives under *Financials* |
| **SKU-level PPC attribution** | ✅ | `SkuPpcAllocation` + `ppc_allocator.py` | spAdvertisedProduct (authoritative) → SB/SD rev-share → group fallback → cold-start → per-campaign reconcile; EMA smoothing; confidence + settlement state | Backend only (feeds pnl_skus) | ✅ **crown jewel** | Surface it |
| Campaign performance centre | ✅ | `campaigns_list` + `api_campaigns_list`, `CampaignProfitDaily` | Ads API + profit cache | KPIs (spend, ad_rev, profit, margin, TACoS, ACOS, ROAS), rows w/ contribution %, attribution coverage %, SKU count | ✅ **Pacvue-like** | KEEP |
| Campaign detail drill | ✅ | `campaign_detail` + top-SKUs / targeting / daily / hourly APIs | `AdsAdvertisedProductDailySnapshot`, `AdsTargetingDailySnapshot`, hourly snapshots | Campaign → SKU, → targeting, → daily, → hourly | ✅ | KEEP |
| Campaign → SKU | ✅ | `api_campaign_top_skus` | `AdsAdvertisedProductDailySnapshot` | Per-SKU rollup inside campaign detail | ✅ | KEEP |
| **SKU → Campaign** (reverse) | ❌ | — | Data exists (`SkuPpcAllocation.campaign_id`) | **None** | data ✅ / UI ❌ | **NEW (P0)** |
| Search-term analytics | ✅✅✅ | (a) `search_terms`, (b) STI `search_intelligence`, (c) `marketing_optimizer` terms tab | `AdsSearchTermDailySnapshot` (all three) | 3 separate tables + 3 tag/action impls | overlap | CONSOLIDATE |
| Search-term → tags/actions | ✅ | `_tag_search_term`, `_ST_ACTION` | same fact table | negate / scale / fix listing labels | ✅ | consolidate logic |
| Budget pacing (capped days) | ✅ | `marketing_optimizer` budget tab | `CampaignBudgetUsageDaily` (exact) else hourly-curve estimate | Cap-rate + raise/fix/watch action | ✅ | KEEP (recent) |
| Targeting/keyword performance | ✅ | `api_campaign_targeting`, `AdsTargetingDailySnapshot` | Ads detail reports | Inside campaign detail | ✅ | KEEP |
| Placement analytics | ✅ | `placements`, `AdsPlacementDailySnapshot` | Ads detail | Standalone page | ✅ | KEEP/MOVE |
| Leaderboards | ✅ | `leaderboards` (6 boards) | `CampaignProfitDaily`, search-term snapshot | SKU/campaign/term rankings | partial overlap | CONSOLIDATE into hubs |
| Search Intelligence (opportunities) | ✅ | STI subsystem (`sti/`, 3.7k LOC) | fact tables + Brand Analytics | Stored-run opportunity engine per ProductGroup, scoring, narrative, opportunity map | ✅ powerful | KEEP; needs seeding |
| Brand Analytics | ✅ | `ba_*`, `BA*Weekly`, `SQP*` | BA reports + SQP | Search queries, baskets, market share, share trend | ✅ | KEEP |
| Hourly patterns / dayparting | ✅ | `hourly_patterns`, `Hourly*Snapshot`, AMS stream | AMS + manual upload | Hourly spend/sales, per-group | ✅ | KEEP |
| AI recommendations | ✅ | `ai_recommendations`, `AIRecommendation` | LLM over metrics | Recommendation feed | ✅ | KEEP |
| Organic vs PPC split (account) | ✅ | `DailyMetric.ppc_sales` vs `revenue` | SP-API | Charts | ✅ | No |
| **Organic vs PPC split (SKU)** | ⚠️ | derivable: `DailySkuSnapshot.revenue` − advertised sales (`AdsAdvertisedProductDailySnapshot.sales_7d`, SP only) | — | **Not surfaced** | data ~✅ | NEW (P1) |
| Exports (Excel/CSV) | ✅ | `mkt_export`, STI CSV, others | — | Per-page | ✅ | reuse pattern |

---

## 2. Current user workflow (how a PPC manager works today)

```
Daily Dashboard (account KPIs)
   ├─→ SKU Profitability  (Financials)   — WHICH skus win/lose (flat table)
   │        ↓ (dead end — no drill)
   ├─→ Campaigns → Campaign detail       — campaign → SKU / targeting / hourly
   ├─→ Search Terms                       — term table + tags
   ├─→ Search Intelligence (STI)          — opportunity engine (needs groups seeded)
   ├─→ Optimizer & Budget                 — term actions + budget pacing
   ├─→ Placements / Leaderboards          — rankings
   └─→ AI Recommendations
```

The manager can answer the questions — but by **hopping between 7 marketing
pages plus a SKU page filed under Financials**, and holding the SKU↔campaign
link in their head because no page threads it.

| Question | Answerable today? | Where | Friction |
|---|---|---|---|
| 1. Which SKUs win/lose? | ✅ | SKU Profitability | Under *Financials*, not *Marketing*; flat |
| 2. **Why** is a SKU performing this way? | ⚠️ partial | SKU Profitability tags | No PPC-vs-organic split per SKU, no trend, no driver |
| 3. Which campaigns drive the SKU? | ❌ | — | **No SKU→campaign view** (data exists) |
| 4. Which campaigns waste spend? | ✅ | Campaigns / Optimizer | Separate page from the SKU |
| 5. Which deserve more budget? | ✅ | Optimizer budget tab | Separate again |
| 6. Which terms/targets drive the campaign? | ✅ | Campaign detail / Search Terms | Fine |
| 7. What next? | ⚠️ | STI / AI recs | Disconnected from the SKU you were on |

**Where it breaks:** between Q1 and Q3. You find a losing SKU, then you must
*leave*, open Campaigns, and manually hunt for the ones tied to it. That single
broken link is the whole reason it doesn't feel like Pacvue.

---

## 3. Problems

1. **No SKU→Campaign→Target spine.** The reverse drill doesn't exist; the manager re-derives it by hand every time.
2. **Search terms live in 3 places** (Search Terms, STI, Optimizer) with 3 tag/action implementations over the *same* `AdsSearchTermDailySnapshot`.
3. **SKU performance is split 3 ways** (SKU Profitability under Financials, campaign→top-SKUs inside Campaigns, Leaderboards) with no single SKU hub.
4. **SKU-level TACoS is an approximation** (`api_pnl_skus` sets SKU TACoS = SKU ACOS — code comment acknowledges it).
5. **No PPC-vs-organic split at SKU level** — the key "why" signal is derivable but not surfaced.
6. **7 flat marketing nav items** with no hierarchy → navigation is a list, not a workflow.
7. **STI ships empty** — requires `seed_product_groups --apply` before it renders (the message you saw).

---

## 4. Duplication (must consolidate, not add)

| Overlapping surfaces | Same underlying data | Recommendation |
|---|---|---|
| Search Terms + STI + Optimizer-terms | `AdsSearchTermDailySnapshot` + `_tag_search_term` | One shared search-term service; STI keeps *opportunities*, Search Terms keeps the *explorer*, remove the Optimizer terms tab (keep its budget tab) |
| SKU Profitability + Campaign top-SKUs + Leaderboards SKU board | `DailySkuSnapshot` + `SkuPpcAllocation` | Promote SKU Profitability to the **SKU Intelligence hub**; Leaderboards' SKU board becomes a view of it |
| Campaign KPIs in Campaigns + Leaderboards campaign board | `CampaignProfitDaily` | Leaderboards campaign board → a sort/preset inside Campaigns |

**Net:** the new experience should be built by *wiring together and consolidating*
existing pieces. Almost no new analytics are required.

---

## 5. Data & attribution assessment

```
Amazon (SP-API · Ads API · AMS stream · Brand Analytics/SQP)
        ↓ ingestion (sync.py · ams_consumer · ads_detail_reports · ba_reports)
Fact tables (DailyMetric · DailySkuSnapshot · PPCCampaignSnapshot ·
             Ads{AdGroup,Targeting,SearchTerm,Placement,AdvertisedProduct}DailySnapshot ·
             Hourly* · CampaignBudgetUsageDaily · BA*Weekly · SQP*)
        ↓ engines
  SkuPpcAllocation  (SKU-level PPC $, 2-pass attribution + confidence)   ← source of truth for SKU PPC
  CampaignProfitDaily (campaign spend/ad-rev/CM cache)                    ← source of truth for campaign profit
  CampaignSearchTermSummary (term rollup cache)
        ↓
Pages (pnl_skus · campaigns · search_terms · STI · optimizer · leaderboards)
```

**Source of truth per metric**
- Account sales/units/orders/sessions/organic-vs-PPC → `DailyMetric`
- SKU sales/COGS/fees/CM → `DailySkuSnapshot`
- **SKU PPC spend → `SkuPpcAllocation.sku_ppc_spend`** (authoritative where spAdvertisedProduct exists; provisional otherwise — carries `confidence_score` + `settlement_state`)
- Campaign spend/ad-rev/profit → `CampaignProfitDaily`
- Campaign→SKU sales/units → `AdsAdvertisedProductDailySnapshot`
- Search terms → `AdsSearchTermDailySnapshot`
- Budget caps → `CampaignBudgetUsageDaily` (exact) or hourly-curve estimate

**Risks / caveats to respect (do not "fix" silently)**
- SKU TACoS = SKU ACOS approximation (see Problem 4).
- Attribution has cadence: campaign→SKU is **T-1** (today shows a banner); allocation has provisional→settling→locked states — any SKU intelligence UI must display `confidence_score`/`settlement_state`, not hide it.
- SB/SD SKU attribution is revenue-share (weaker than SP's authoritative report) — surface the `attribution_source`.
- Possible double-count guard: SKU PPC is reconciled per campaign (`Σ SKU spend = campaign spend`) — good, keep using the reconciled figure, never re-sum raw snapshots.

---

## 6. Proposed workflow (the spine)

```
SKU Intelligence (hub — replaces flat SKU table, moved into Marketing)
        ↓ click a SKU  →  SKU drawer
   Sales trend · PPC vs Organic split · ACOS/TACoS/ROAS/CM · confidence
        ↓ "Campaign drivers"  (SkuPpcAllocation grouped by campaign for this SKU)
   Ranked campaigns driving this SKU (spend, ad-rev, ACOS, share)
        ↓ click a campaign  →  existing Campaign detail
   Targeting · Search terms · Placements · Hourly
        ↓  →  Opportunity (STI) / Suggested action (Optimizer tags)
```

Minimum clicks: **SKU → driver campaign → target → action** in 3 clicks, all
reusing pages that already exist. The only genuinely new surface is the **SKU
drawer + its "campaign drivers" endpoint**.

---

## 7. Proposed GUI

- **Main (SKU Intelligence):** the existing `pnl_skus` table, moved into Marketing, with columns trimmed to decision-grade: SKU · product · revenue · units · **PPC sales / organic split** · ad spend · ACOS · TACoS · ROAS · CM% · contribution% · confidence chip · trend sparkline · status tag. Filters that matter: marketplace, brand, period (incl. custom dates), tag (losing/scaling/high-profit), numeric filters (reuse the Optimizer's compound-filter pattern).
- **SKU drawer (click a row):** header KPIs; one trend chart (revenue vs ad-spend, or PPC vs organic); **Campaign drivers table** (campaigns driving this SKU, sorted by spend, each linking to Campaign detail); the SKU's worst/best search terms (reuse search-term service); any STI opportunity or AI rec tagged to this SKU.
- **Campaign detail:** unchanged (already strong) — just make the SKU drawer link into it.
- **Charts that earn their place:** SKU trend (spend vs sales), PPC-vs-organic area, campaign-driver bar. Skip vanity charts.
- **Opportunities:** surfaced *inside* the SKU drawer (contextual), not only on the standalone STI page.

---

## 8. Feature classification

| Existing feature | Recommendation | Why |
|---|---|---|
| SKU Profitability (`pnl_skus`) | **IMPROVE + MOVE** | Make it the SKU Intelligence hub; move from Financials → Marketing; add drawer + drill |
| `SkuPpcAllocation` engine | **KEEP** | Crown jewel; just expose it |
| Campaigns list + detail | **KEEP** | Already Pacvue-grade |
| `api_campaign_top_skus` | **KEEP** | Powers campaign→SKU |
| Search Terms page | **KEEP (as explorer)** | Fold shared logic into a service |
| STI opportunities | **KEEP** | Unique value; surface contextually + seed groups |
| Optimizer — budget tab | **KEEP** | Recent, exact budget pacing |
| Optimizer — search-term tab | **CONSOLIDATE/REMOVE** | Duplicate of Search Terms |
| Leaderboards | **CONSOLIDATE** | Boards become presets inside the SKU/Campaign hubs |
| Placements | **KEEP/MOVE** | Fine; can live under campaign detail |
| PPC Analytics (legacy) | **REMOVE/REDIRECT** | Superseded by Campaigns + SKU hub |
| SKU→Campaign drill | **NEW (P0)** | The missing spine |
| SKU PPC-vs-organic split | **NEW (P1)** | The "why" signal |
| Marketing nav hierarchy | **NEW (P1)** | Turn 7 flat links into a workflow |

---

## 9. Prioritized build plan

### P0 — Critical (creates the spine)
- **SKU drawer + `api_sku_campaigns` endpoint.** Groups `SkuPpcAllocation` by campaign for one SKU/period → ranked campaign drivers, each linking to existing `campaign_detail`. *Reuses:* SkuPpcAllocation, Campaign dim, campaign_detail. *New backend:* one read-only endpoint. *New data:* none. *Complexity:* low–medium. *Dependency:* none.
- **Elevate `pnl_skus` to Marketing → SKU Intelligence** (nav move + the drawer). *Reuses:* the whole existing page. *Complexity:* low.

### P1 — High value
- **SKU PPC-vs-organic split column + chart.** Derive organic = SKU revenue − advertised sales (`AdsAdvertisedProductDailySnapshot`). *New backend:* a join in `api_pnl_skus`. *New data:* none. *Complexity:* medium. *Caveat:* SP-only advertised sales; label SB/SD.
- **Marketing nav hierarchy** (Overview · SKU Intelligence · Campaigns · Search Intelligence · Opportunities). *Complexity:* low.
- **Consolidate search terms** into one service; retire the Optimizer terms tab. *Complexity:* medium.

### P2 — Enhancement
- Contextual opportunities/AI-recs inside the SKU drawer. *Reuses:* STI + AIRecommendation. *Complexity:* medium.
- Confidence/settlement chips + attribution-source tooltip across SKU views. *Complexity:* low.
- Leaderboards → presets inside hubs; deprecate the standalone page. *Complexity:* medium.

### P3 — Future
- Proper SKU-level TACoS (needs SKU-level total vs ad revenue reconciliation). *Complexity:* medium–high.
- Cross-marketplace SKU roll-up (same SKU across US/CA/UK/EU). *Complexity:* high.
- Write-back actions to Amazon Ads (currently read-only by design — out of scope until you decide to push).

---

## 10. Recommended final architecture

```
Marketing
├── Overview                (light: account PPC KPIs + top opportunities — reuse existing widgets)
├── SKU Intelligence        (pnl_skus PROMOTED here + SKU drawer)   ← P0 spine
│      └── SKU → Campaign drivers → Campaign detail → Target/Search term → Opportunity
├── Campaign Intelligence   (campaigns + detail — KEEP, receives drawer links)
├── Search Intelligence     (STI opportunities + Search Terms explorer — CONSOLIDATED)
├── Budget & Pacing         (optimizer budget tab — KEEP)
└── Opportunities           (STI opportunities + AI recs — surfaced contextually too)
```

- **Enhance, don't add:** the only new page-level surface is the SKU drawer; everything else is a move/merge.
- **Retire:** Optimizer search-term tab, standalone Leaderboards, legacy PPC Analytics.
- **Move:** SKU Profitability from Financials → Marketing.
- **Reuse verbatim:** SkuPpcAllocation, CampaignProfitDaily, campaign_detail, search-term service, STI.

**Guiding principle honoured:** fastest path from *what's happening at SKU level*
→ *why* → *which campaigns* → *which targets* → *what to do*, with the fewest new
parts. One new endpoint + one drawer turns a pile of strong-but-disconnected
pages into one connected intelligence system.

---

### Open decisions for you before any build
1. Confirm the **SKU drawer inside SKU Intelligence** is the right home for the spine (vs a brand-new page).
2. OK to **move SKU Profitability out of Financials** into Marketing?
3. OK to **retire** the Optimizer search-term tab, standalone Leaderboards, and legacy PPC Analytics (consolidate, not delete data)?
4. Priority call: build **P0 spine first** (SKU→campaign drill), then P1 — agreed?
