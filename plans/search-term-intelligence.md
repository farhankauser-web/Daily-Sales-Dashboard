# Search Term Intelligence Dashboard — Design Document

Status: **SUPERSEDED by `plans/search-intelligence-center.md` (v2)** — kept for
the engineering detail v2 builds on (queries, taxonomy, KPI formulas,
two-clocks design, verification queue V1–V6).
Author: Claude · 2026-08-07
Location note: lives in `plans/`, not `docs/`, deliberately — `docs/` states the
business machine as built and is swept by `check_docs.py`. When this is approved
and implemented, the durable architecture moves into `docs/marketing/` per
convention and this file becomes history.

Evidence base: Django models in `apps/dashboard/models.py`, docs leaves
(`docs/marketing/search-terms.md`, `docs/brand-analytics/search-queries.md`),
and the local dev snapshot. Every row-count below is **local-snapshot evidence,
provisional until checked against production** — the queue is in §12.

---

## 0. The one question this answers

> "What actions should I take today to improve my Amazon business,
> based on search term performance?"

Everything below is subordinate to that. Each section of the dashboard must
end in a decision, and the report must end in a ranked action list. A chart
that prompts no decision does not ship.

---

## 1. Overall architecture

### 1.1 Shape: a generated report, not a live page

The dashboard is a **report run**: the user picks (product group ×
marketplace × date range), clicks Generate, and Pulse computes a complete
JSON payload which is stored and then rendered. Not a live-querying page.

Why:

- The pipeline joins a ~10M-row fact table with weekly BA data, profit
  proxies, tagging, and a rules engine. That is seconds of work, not
  milliseconds — unacceptable per page-load, fine per explicit run.
- A stored run gives history ("what did this look like when I acted?"),
  shareability, and a natural audit trail for the action feedback loop (§10).
- It preserves the skill's proven architecture (data JSON → template) while
  swapping the data source from xlsx to ORM. The offline-HTML export becomes
  a free by-product if ever wanted.

### 1.2 Three layers

```
┌─ Presentation ─────────────────────────────────────────────┐
│ /dashboard/search-term-intelligence/                       │
│   run picker (group · marketplace · range) + run history   │
│   report template: 12 sections, § each = one payload key   │
└──────────────────────────▲─────────────────────────────────┘
                           │ StiReportRun.payload (JSON)
┌─ Intelligence pipeline (one function per section) ─────────┐
│ scope → enrich → aggregate → insight rules → action engine │
└──────────────────────────▲─────────────────────────────────┘
                           │ ORM only — no new syncs for v1
┌─ Data (all existing) ──────────────────────────────────────┐
│ AdsSearchTermDailySnapshot   CampaignSearchTermSummary     │
│ AdsTargetingDailySnapshot    AdsAdvertisedProductDaily     │
│ Campaign (dim)               CampaignProfitDaily           │
│ BASearchQueryWeekly          BAItemComparisonWeekly        │
│ BABrandShareWeekly           BAMarketBasketWeekly          │
│ Product                      DailySkuSnapshot              │
│ DailyMetric                  InventorySnapshot             │
└────────────────────────────────────────────────────────────┘
```

### 1.3 New tables (three, plus one optional)

| table | grain | purpose |
|---|---|---|
| `ProductGroup` | one per group | the required filter's backbone (§3) |
| `SearchTermTag` | marketplace × search_term_hash | persisted multi-dimensional classification (§5) |
| `StiReportRun` | one per generation | params + payload JSON + status + generated_at |
| `StiActionOutcome` *(optional, phase 2)* | one per acted-on recommendation | feedback loop (§10) |

No new Amazon syncs are required for v1. Every input already flows into
Pulse on production cron.

### 1.4 The two-clocks principle (the timeline problem, addressed head-on)

Ads data is **daily**, settled ~T-2 (spend/clicks final; `sales_7d` still
accrues for up to 7 days — see §7 caveats). Brand Analytics SQP is **weekly**
(Amazon's week, one row per week × ASIN × query) and arrives ~T-3 after the
week closes. These clocks cannot be date-aligned, so the design never tries:

1. **Each dataset keeps its own clock.** The report header shows two as-of
   stamps: *"Ads: 1–31 Jul (daily)"* and *"Brand Analytics: 4 complete weeks
   ending 26 Jul"*. Every BA-fed widget carries the weekly stamp.
2. **The BA window is derived, not matched**: the latest K complete weeks
   whose `week_end` ≤ the ads range end, K defaulting to ceil(range/7),
   clamped to what exists. A 7-day ads range gets 1–2 BA weeks; a 30-day
   range gets 4–5.
3. **Joins are by text, never by date.** `BASearchQueryWeekly.search_query_hash`
   and `AdsSearchTermDailySnapshot.search_term_hash` are both
   SHA1(lower(text)) — verified identical recipes in the model definitions —
   so a query-level join needs no date alignment at all.
4. **Trends stay within one clock.** WoW demand trends come from BA weeks
   compared to BA weeks; daily spend trends from ads days. No section
   computes a ratio whose numerator and denominator tick differently.
5. **Coverage is displayed, not hidden.** SQP only reports queries where our
   ASINs surfaced, and only for weeks that arrived. Each BA widget shows
   *"SQP visibility: X% of ad spend in this report has BA data"* so a thin
   BA week reads as thin, not as zero demand.

Local snapshot holds **one** SQP week (940 rows, week of 2026-05-31) —
enough to prove ingestion, useless for trends. Production depth is unknown
and is verification item **V1** (§12). Every BA section must degrade
gracefully to "insufficient weeks" rather than render a two-point trend.

---

## 2. Filters (the report's identity)

| filter | required | values | default |
|---|---|---|---|
| Product group | yes | from `ProductGroup` | none — must pick |
| Marketplace | yes | usa · uk · ae · sa (one only) | none — must pick |
| Date range | yes | presets + custom | last 30 days ending **T-2** |

- **One marketplace per run.** Currency and VAT never mix. The payload
  stores the currency symbol and the marketplace's `net_factor` once.
- **T-2 anchor.** All presets end at T-2 (yesterday-minus-one), per the
  business rule that data is settled by then. Presets: T-2 single day ·
  last 7 · last 14 · last 30 · custom. Custom ranges ending later than T-2
  are allowed but the header warns "includes unsettled days".
- Range length is capped (90 days, tunable) to bound query cost.

---

## 3. Product groups

### 3.1 Why a new table

Pulse has two partial notions of "product": `Product.category` (clean,
populated — e.g. *Bath Towels* ×61, *Hand Towel Pack 6* ×20, *Wash Cloth
pack 12* ×20 in the local catalog) and `Campaign.initials` (clean, 17 codes
— BTH 88 · KTH 70 · BS 45 · WCPK 42 · HNDTWL 34 …; 22 campaigns blank).
`Campaign.product_family` is **not usable** — the parser has produced values
like "Group C (Broad)" and "Remarketing", and 132 blanks. Neither notion
alone scopes both the ads side (campaigns) and the BA side (ASINs), so the
group table binds them:

```
ProductGroup
  name             "Bath Towels 4 Pack"
  slug             bath-towels-4-pack
  initials         ["BTH"]            ← routes campaigns in
  categories       ["Bath Towel Pack 4"]  ← routes ASINs in via Product.category
  extra_asins      []                 ← manual override, additive
  excluded_asins   []                 ← manual override, subtractive
  term_lexicon_key "bath_towel"       ← which intent lexicon applies (§5)
  active           bool
```

Seeded by a management command from the existing catalog + campaign dim
(dry-run by default, `--apply` to write, per repo convention), then curated
in a small Settings page. Groups are marketplace-agnostic (SKU logic is
region-blind in Pulse; membership resolves per-marketplace at query time
through `Product.marketplace`).

### 3.2 Scoping a report to a group — two routes, both kept

- **Campaign route** (spend-complete): search-term rows whose `campaign_id`
  belongs to campaigns with `initials ∈ group.initials`. Catches every
  dollar those campaigns spent — including spend leaking onto *other*
  products' terms, which is itself a finding.
- **Semantic route** (demand-complete): terms whose classified
  `product_type` (§5) matches the group — regardless of which campaign
  spent on them. Catches e.g. bath-towel searches that a kitchen-towel
  campaign is winning.

The report body uses **campaign route** as the money spine (it sums to real
spend) and overlays the semantic flag. The difference between the routes
feeds two dedicated findings: *cross-contamination* (group campaigns buying
foreign terms) and *captured-elsewhere* (group demand served by foreign
campaigns). The 22 initials-blank campaigns are surfaced in a data-quality
footer, not silently dropped — verification item **V2**.

BA scope: the group's ASINs (via `Product.category` + overrides) select
`BASearchQueryWeekly` rows (which are ASIN-scoped by construction).

---

## 4. Data flow per run

```
params (group, marketplace, range)
   │
   ├─ resolve group → campaign_ids (initials) → asins (categories)
   │
   ├─ ADS SPINE (daily clock)
   │    AdsSearchTermDailySnapshot [mkpl, date range, campaign_ids]
   │      → per-term aggregate (spend, sales, clicks, imp, orders, units)
   │      → derived ACOS/ROAS/CTR/CVR/CPC (recomputed from sums, never
   │        averaged from the stored per-day ratios)
   │    AdsTargetingDailySnapshot   → target/keyword layer (bid actions)
   │    CampaignProfitDaily         → per-campaign margin_pct + coverage
   │      → per-term est. profit = term sales × campaign margin − term spend
   │        (proxy, labeled; MKT-D-011 precedent)
   │
   ├─ ENRICHMENT
   │    SearchTermTag lookup (miss → classify now, persist)  (§5)
   │    join by hash → BASearchQueryWeekly rollup            (weekly clock)
   │
   ├─ BA LAYER (weekly clock, group ASINs, latest K complete weeks)
   │    BASearchQueryWeekly    → demand volume, rank, our shares, top-3 ASINs
   │    BAItemComparisonWeekly → who shoppers compare us against
   │    BABrandShareWeekly     → brand share trend
   │    BAMarketBasketWeekly   → co-purchase (phase-2 section)
   │
   ├─ BUSINESS CONTEXT
   │    DailySkuSnapshot [group SKUs] → total group revenue/units/CM
   │      → paid share of revenue, group TACoS
   │    InventorySnapshot [group SKUs] → cover days (action gating, §9)
   │
   ├─ INSIGHT RULES (§8) → findings[]
   ├─ ACTION ENGINE (§9) → ranked actions[]
   │
   └─ StiReportRun.payload = { meta, kpis, sections…, findings, actions }
```

### 4.1 Representative queries (design sketches, not code)

Term spine (the one expensive query — served entirely by the existing
`(marketplace, campaign_id, -date)` index):

```
AdsSearchTermDailySnapshot.objects
  .filter(marketplace=m, date__range=(d0, d1), campaign_id__in=group_campaigns)
  .values('search_term_hash', 'search_term', 'match_type')
  .annotate(spend=Sum('spend'), sales=Sum('sales_7d'), clicks=Sum('clicks'),
            impressions=Sum('impressions'), orders=Sum('orders_7d'),
            units=Sum('units_7d'), campaigns=Count('campaign_id', distinct=True))
```

Scale check (local snapshot, provisional): USA holds 86,400 rows over ~12
weeks all-campaign; a single group over 30 days is a few-thousand-row
aggregate. Well within on-demand budget.

BA rollup for the same terms (weekly clock; one row per query after
summing our per-ASIN counts and max-ing the query-level volume — the
volume repeats per ASIN row and must **not** be summed):

```
BASearchQueryWeekly.objects
  .filter(marketplace=m, asin__in=group_asins, week_end__lte=d1,
          week_start__gte=ba_window_start)
  .values('search_query_hash', 'search_query', 'week_start')
  .annotate(volume=Max('search_query_volume'), rank=Min('search_query_score'),
            our_clicks=Sum('asin_click_count'), our_purchases=Sum('asin_purchase_count'),
            click_share=..., purchase_share=...)   # share = our Σ / totals
```

Group revenue & TACoS: `DailySkuSnapshot` filtered to group SKUs, summed
over the range. Organic sales = group revenue − ad-attributed sales
(`AdsAdvertisedProductDailySnapshot` for the same campaigns/ASINs) — an
approximation, labeled as such (attribution windows differ).

Profit proxy: `CampaignProfitDaily.margin_pct` per campaign over the range,
weighted by revenue; `attribution_coverage_pct` carried through as the
confidence input (§9).

---

## 5. Search intent taxonomy

### 5.1 Design: dimensions, not buckets

The skill's single-bucket model ("High Intent - Hydrogen Product") conflates
orthogonal facts. Replace with **multi-dimensional tags** per term, from
which a single displayed *intent tier* is derived per group:

| dimension | values (examples) | source |
|---|---|---|
| `product_type` | bath_towel, hand_towel, washcloth, kitchen_towel, bath_sheet, bath_mat, bedsheet, pillowcase, tea_towel, beach_towel, generic_towel, non_towel | lexicon |
| `attributes[]` | color:white, material:cotton, size:extra_large, pack:12, quality:luxury, feel:soft, feature:quick_dry, gsm:600, use:decorative | lexicon |
| `room_usage` | bathroom, kitchen, gym, hotel, spa, salon, baby, boat | lexicon |
| `brand_class` | our_brand · competitor_brand · unbranded | lexicon |
| `is_asin` | bool (`^B0[A-Z0-9]{8}$` — keep the skill's rule) | regex |

Derived **intent tier**, computed *relative to the selected group*:

1. **Branded** — our_brand (infinitee, infinitee xclusives, misspellings)
2. **Competitor** — competitor_brand or is_asin
3. **High intent — this product** — product_type matches group + ≥1 attribute
   ("white bath towels 4 pack")
4. **Product match** — product_type matches group, no qualifier ("bath towels")
5. **Generic category** — generic_towel / head terms ("towels", "towel set")
6. **Adjacent product** — product_type is another of our groups
   ("kitchen towels" inside a Bath Towels report → cross-contamination fuel)
7. **Off-category** — non_towel or no match ("paper towels" — the negative
   keyword goldmine)

### 5.2 Lexicon is data, not code

Patterns live in versioned per-marketplace lexicon files (or rows) keyed by
`term_lexicon_key`, **not** hardcoded regexes in a script — the skill's
hydrogen-bottle hardcoding is precisely the failure mode being retired.
Marketplace-specific entries handle language: AE/SA searches include Arabic
terms (مناشف — towels); English-only patterns would dump the AE/SA long
tail into Off-category and poison the negative-keyword section. Seeding the
Arabic lexicon is verification item **V3** (sample prod AE/SA terms first).

### 5.3 Persistence and incremental classification

`SearchTermTag` (marketplace × search_term_hash, tags JSON + lexicon
version): a nightly job classifies only new hashes; a lexicon version bump
triggers full reclassification (idempotent, off-peak). Report runs then
join, never regex-scan 100k terms inline. Report footer shows "N terms
(X%) unclassified" as a lexicon-health signal.

---

## 6. Dashboard layout — 12 sections, each ending in a decision

Ordering principle: *health → demand → performance → opportunities →
threats → language → actions*. Every section states the decision it exists
to prompt; that is the acceptance test for its design.

| # | section | contents | decision prompted |
|---|---|---|---|
| 1 | **Executive Summary** | KPI strip (§7) + sparkline vs prior equal period + the top-3 actions inlined from §12 | "Do I need to read further today?" |
| 2 | **Search Demand** *(weekly clock)* | group queries by SQP volume; rank movers; volume WoW trend; needs ≥3 BA weeks else collapses to a single-week table with a banner | which demand pools we must show up in |
| 3 | **Paid Performance** | top terms by revenue & by est. profit; match-type economics; intent-tier mix of spend; keyword→term drift (target `expression` vs terms it matched) | where the ad money actually works |
| 4 | **Organic vs Paid** | paid share of group revenue; group TACoS; per-query: our ad orders vs SQP `asin_purchase_count` → paid-dependency flag | which queries we own vs rent |
| 5 | **Organic Opportunity** | queries with high SQP volume + proven ad CVR + low `brand_purchase_share`; queries we buy but rank weakly in | where SEO/rank push beats more spend |
| 6 | **Brand & Competitor Intelligence** *(weekly clock)* | `BABrandShareWeekly` trend; competitor ASINs from SQP `top_clicked_asins` on our money queries; `BAItemComparisonWeekly` most-compared rivals; competitor-branded term economics (conquesting ROI) | who is taking share, and whether conquesting pays |
| 7 | **High Spend — No Sales** | prioritised zero-order terms, spend ≥ threshold, with campaign + match type attached (executability requirement) | bid down or exclude, today |
| 8 | **Scaling Opportunities** | low-ACOS/high-CVR terms with headroom (CTR strong, impressions modest vs SQP volume) — **inventory-gated** (§9) | where the next dollar goes |
| 9 | **Low-Hanging Fruit** | high CTR + low CVR (listing problem, not bid problem); terms converting broad-match only → exact-campaign candidates; near-threshold ACOS terms one bid-step from profitable | cheap wins this week |
| 10 | **Negative Keyword Candidates** | Off-category + Adjacent tiers with spend, zero/near-zero orders, grouped by the campaign that leaked | the negative list to paste in |
| 11 | **Customer Language** | token/bigram frequency over converting vs non-converting terms (weighted by orders); gaps vs current listing copy flagged for review | which words belong in titles/bullets |
| 12 | **Executive Action List** | ranked actions, High/Medium/Low, each with why · data · expected impact · confidence (§9) | the to-do list — the report's actual product |

Cross-contamination and captured-elsewhere findings (§3.2) surface inside
§10 and §8 respectively rather than as separate sections.

---

## 7. KPI definitions

All money in the marketplace currency, displayed **gross**; profit measures
ex-VAT via `net_factor` per the margin invariant. Every ratio recomputed
from summed numerators/denominators — never averaged from stored per-day
ratios.

| KPI | formula | source | notes |
|---|---|---|---|
| Ad Spend | Σ spend | term spine | |
| Ad Sales | Σ sales_7d | term spine | gross; 7-day attribution |
| Group Revenue | Σ revenue | DailySkuSnapshot (group SKUs) | gross |
| Paid Share | Ad Sales ÷ Group Revenue | both above | approximation, labeled |
| Group TACoS | Ad Spend ÷ Group Revenue | both above | |
| ACOS / ROAS | spend÷sales · sales÷spend | term spine | |
| CTR / CVR / CPC | clicks÷imp · orders÷clicks · spend÷clicks | term spine | |
| Est. Profit / term | sales×campaign margin_pct − spend | + CampaignProfitDaily | proxy; carries `attribution_coverage_pct` |
| Wasted Spend | Σ spend where orders=0 ∧ clicks≥5 ∧ spend≥2×avg CPC | term spine | click floor stops one-click noise inflating the number |
| SQP Volume / Rank / Shares | as reported | BASearchQueryWeekly | weekly clock, own as-of stamp |
| SQP Coverage | Σ spend on terms with BA rows ÷ Σ spend | join | honesty metric, always shown |

**Stated caveats carried on the report itself** (not buried in docs):
`sales_7d` for the last ~5 days of any range is still accruing (7-day
attribution), so ranges ending at T-2 slightly understate recent
conversions — the T-2 anchor is a business rule accepting this; SB rows
exist alongside SP (both included, split available); SD has no search-term
data at all (Amazon does not provide it — documented business rule).

---

## 8. Insight generation logic

Deterministic rules, each producing a typed finding:
`{rule_id, severity, message, evidence{}, decision_hint}`. Thresholds are
named constants in one module — business judgements, reviewed with you, not
buried magic numbers. Starting set (thresholds illustrative, for review):

| rule | trigger (within the run's scope) | feeds section |
|---|---|---|
| WASTE-1 | term spend ≥ 3× group avg CPO proxy, orders = 0 | §7, §12 |
| NEG-1 | tier ∈ {Off-category} ∧ spend ≥ $10 ∧ orders = 0 | §10, §12 |
| NEG-2 | tier = Adjacent ∧ ACOS > 2× group ACOS | §10 |
| SCALE-1 | ACOS ≤ 0.6× group ACOS ∧ orders ≥ 5 ∧ impressions < 20% of SQP volume | §8, §12 |
| SCALE-2 | converting on broad/phrase only, no exact target exists (targeting layer check) | §9 |
| LISTING-1 | CTR ≥ 1.5× group avg ∧ CVR ≤ 0.5× group avg ∧ clicks ≥ 30 | §9, §11 |
| ORG-1 | SQP volume top-quartile ∧ brand_purchase_share < 5% ∧ ad CVR ≥ group avg | §5, §12 |
| ORG-2 | paid-dependency: ad orders ≥ 80% of SQP purchases on a top query | §4 |
| COMP-1 | competitor ASIN appears in top_clicked on ≥2 of our top-10 money queries | §6 |
| COMP-2 | conquesting terms (Competitor tier) with ACOS > 3× group | §6, §10 |
| DRIFT-1 | ≥30% of a target's spend lands on terms whose tier ∉ {Branded, High, Product match} | §3 |
| XCON-1 | group campaigns spent ≥ $X on another group's product_type | §10 |
| TREND-1/2 | term spend or SQP volume ±50% vs prior equal window (own clock each) | §2 |
| LANG-1 | token enriched ≥2× in converting terms vs non-converting, absent from listing copy *(listing copy source: phase 2)* | §11 |

Rules read only the payload aggregates — pure functions, unit-testable
against fixture payloads without a database.

## 9. Action recommendation engine

Findings are evidence; actions are the product. The engine maps findings →
concrete, executable actions, then scores and ranks.

**Action schema:**

```
{ action_type,            # negative_keyword · bid_down · bid_up · new_exact_campaign
                          # · listing_update · organic_push · budget_shift
  title,                  # "Add negative exact 'paper towels' to 12KTH-SP-AUTO"
  why,                    # one sentence, from the finding's message
  evidence,               # the numbers: spend, orders, ACOS, SQP rank…
  expected_impact_value,  # $/month, marketplace currency (basis stated)
  confidence,             # high · medium · low
  priority,               # high · medium · low (impact × confidence matrix)
  scope }                 # campaign_id(s), term, match type — executable as-is
```

- **Expected impact** is honest arithmetic, labeled: negative keyword →
  observed wasted spend/month; bid-up → headroom × current CVR × margin
  proxy; listing fix → clicks × (group CVR − term CVR) × AOV × margin.
- **Confidence** is data-driven, not vibes: click/order volume behind the
  finding, number of BA weeks available, `attribution_coverage_pct` of the
  campaigns involved. Thin evidence caps confidence at low regardless of
  effect size.
- **Inventory gate** (improvement over the brief, data already in Pulse):
  any bid_up / new_campaign / budget_shift action checks
  `InventorySnapshot` cover-days for the group's SKUs; low cover downgrades
  to "hold — restock first" instead of recommending spend into a stockout.
- **Dedupe & conflict**: one term appearing in multiple findings yields one
  action (highest-priority rule wins); bid_up and negative on the same term
  is impossible by construction (tier partitions).
- **Persistence**: v1 actions live in the report payload. Phase 2 promotes
  them into the existing `AIRecommendation` ack/dismiss/snooze workspace
  (source tag `sti`) rather than inventing a parallel workflow — decision
  point for review.
- **AI narrative (optional, off by default)**: the deterministic output can
  be handed to the existing `ai_insights` pipeline for prose summarisation.
  The numbers and ranking never come from the model; it only narrates.

---

## 10. Feedback loop (phase 2, designed now)

When an action is marked done, snapshot the term's trailing-30d metrics;
re-measure at +14d and +30d; store delta in `StiActionOutcome`. The report
gains a "previous actions scoreboard", and thresholds in §8 get tuned by
evidence instead of intuition. This is the cheapest genuinely
differentiating feature in the whole design — most PPC tools recommend and
never look back.

---

## 11. Future extensibility

**Section-provider registry.** Each report section is a provider:
`provider(params, ctx) → {section_key: payload}` registered in an ordered
list. The template renders known keys and ignores unknown ones; old stored
runs stay renderable (payload carries `schema_version`). Adding a data
source = adding a provider + template partial. No redesign for:

| future source | lands as | note |
|---|---|---|
| per-ASIN Business Reports (sessions, USP) | upgrade to §4/§5 | **known gap**: Pulse stores only account-level sessions (`DailyMetric`); per-ASIN traffic report ingestion is the single most valuable future sync for this dashboard |
| Buy Box / pricing | gate + annotate §8 | don't scale a lost buy box |
| Reviews / VoC | §11 enrichment | language mining from reviews |
| AMS / hourly | dayparting section | `PPCCampaignHourlySnapshot` partly exists |
| Market basket | bundle section from `BAMarketBasketWeekly` | data already synced |
| Competitor tracking | §6 enrichment | |

---

## 12. Production verification queue

Per convention: identified during design, run at implementation start,
**nothing here blocks the plan review**.

| id | question | query sketch | design consequence |
|---|---|---|---|
| V1 | How many SQP weeks exist per marketplace in prod? | `BASearchQueryWeekly.values('marketplace').annotate(weeks=Count('week_start', distinct=True), lo=Min(...), hi=Max(...))` | whether §2/§6 trends ship in v1 or start collapsed |
| V2 | How much spend flows through initials-blank campaigns? | join blank-initials campaigns to term spend, per marketplace | how loud the data-quality footer must be; whether curation precedes launch |
| V3 | What do AE/SA search terms look like (script/language mix)? | top-200 terms by spend per marketplace | Arabic lexicon scope for §5 |
| V4 | `CampaignProfitDaily` coverage across group campaigns/dates? | coverage % per campaign over trailing 30d | how often est.-profit falls back to spend-only |
| V5 | SP vs SB term-row mix in prod? | count by `source_ad_type` | whether SB gets its own split in §3 |
| V6 | Prod row counts / date span of the term fact table | as run locally | query-cost validation for the 90-day cap |

---

## 13. Open decisions for review

1. **Product group management** — Settings page (proposed) vs fixture file.
2. **Action workspace** — payload-only in v1, promote to `AIRecommendation`
   in phase 2 (proposed) vs new dedicated table from day one.
3. **Threshold review** — the §8 numbers are proposals; they are business
   judgements and need your sign-off before implementation.
4. **v1 cut line** — proposed v1: sections 1, 3, 4, 7, 8, 9, 10, 12 + tags +
   report runs (pure ads + catalog + profit, no BA dependency). BA sections
   (2, 5, 6) land the moment V1 confirms production depth; 11 follows.
   Rationale: the ads-side value doesn't wait on BA history, and the BA
   sections degrade gracefully by design either way.
5. **Skill's fate** — the in-repo skill (`.claude/skills/search-term-dashboard/`)
   stays as an offline xlsx fallback, or is retired once the native page
   ships. Proposed: retire; keep the HTML export idea via `StiReportRun`.
