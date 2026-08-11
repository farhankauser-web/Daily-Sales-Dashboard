# Search Intelligence Center — Design v2

Status: **PROPOSAL — supersedes `plans/search-term-intelligence.md` (v1)**
Author: Claude · 2026-08-07

v1 designed a search-term *reporting* pipeline with an action list bolted on.
This revision inverts it: the product is a ranked set of **business
opportunities with dollar values**, and every table v1 proposed is demoted to
evidence behind an opportunity. The test for every element is no longer
"is this accurate?" but **"where is the next profitable growth opportunity,
and what should we do about it?"**

All local-snapshot figures below are provisional until checked against
production (§13). Sources named per repo convention.

---

## 1. Self-review of v1 — what survives, what gets rebuilt

### Retained unchanged (the engineering spine was right)

| v1 element | why it stays |
|---|---|
| Report-run architecture (`StiReportRun`, JSON payload, provider registry) | history + diffing (§10) is impossible without stored runs |
| Two-clocks principle (daily ads vs weekly BA, join by SHA1 hash, never by date) | the timeline problem doesn't change because ambitions did |
| `ProductGroup` table (initials → campaigns, categories → ASINs) | every scope still hangs off it |
| Multi-dimensional term taxonomy + `SearchTermTag` persistence, lexicon-as-data, Arabic support | it becomes *more* central — the demand tree (§6) is built from these tags |
| Ex-VAT margin discipline, currency isolation, T-2 anchor, honesty metrics (SQP coverage %) | invariants |
| Production verification queue | extended, §13 |

### Rebuilt — v1's reporting residue, called out honestly

| v1 section | verdict | v2 disposition |
|---|---|---|
| §2 Search Demand ("queries by volume, rank movers") | **reporting** — a volume table prompts no decision | absorbed into Demand Tree (§6) + Market Share Intelligence (§7): volume only ever appears next to *our share of it* and *what capturing more is worth* |
| §3 Paid Performance (top-terms tables) | **reporting** — "top terms by revenue" is a scoreboard | demoted to evidence drawer; the decision layer is the Opportunity Score (§5) |
| §6 Brand & Competitor ("competitor ASINs on our queries") | **reporting** — a list of enemies is not a strategy | rebuilt as Competitor Opportunity Intelligence (§8): share *shifts*, vulnerability detection, conquest-worthiness scoring |
| §11 Customer Language (token frequency) | **half-reporting** — words without a dollar value | folded into Listing Quality scoring inside opportunity dependencies (§6.4) |
| §12 Executive Action List | too PPC-shaped — negatives and bids, when the data supports product and share decisions | replaced by Business Opportunities (§4) with the Ops Queue kept as a subordinate tactical list (§9) |
| v1 KPI strip | partially reporting | replaced by the eight-question Executive Screen (§3) |
| v1 wasted-spend / scaling / negatives sections | genuinely decision-driving | kept, but reframed as *Defend* opportunities and Ops Queue items rather than standalone pages |

### The unlock v1 undersold

v1 treated `BASearchQueryWeekly` as "demand context". It is much more:
Amazon reports **market-total funnel counts per query per week** —
`clicks_total`, `cart_adds_total`, `purchases_total` — alongside our ASIN
counts. That is a per-query market-share measurement, not a rank proxy.

Local snapshot, one week (2026-05-31, USA — provisional):

| query | market purchases/wk | ours | share |
|---|---|---|---|
| bath towels | 3,319 | 90 | 2.7% |
| kitchen towels | 3,830 | 242 | 6.3% |
| wash cloths | 3,158 | 131 | 4.1% |
| face towels | 4,675 | 5 | **0.1%** |
| beach towels | 2,181 | 0 | **0%** |

One week of one marketplace already surfaces a share gap ("face towels" —
the market's biggest single purchase pool in the towel set, ours to lose)
and a product-gap candidate ("beach towels"). The entire v2 market model
(§7) stands on these fields — no scraping, no third-party data.

---

## 2. Architecture v2 — the decision layer on top of the v1 pipeline

```
┌─ PRESENTATION ────────────────────────────────────────────────┐
│ 1 Executive Screen      — eight questions, one minute   (§3)  │
│ 2 Opportunity Board     — ranked, filterable, 2-D       (§4)  │
│ 3 Demand Tree Explorer  — funnels, coverage, gaps       (§6)  │
│ 4 Market & Competitors  — share, shifts, conquest       (§7,8)│
│ 5 Ops Queue             — negatives, bids, budgets      (§9)  │
│ 6 History & Learning    — diffs, outcomes, calibration  (§10) │
└──────────────────────────▲────────────────────────────────────┘
┌─ DECISION LAYER (new) ───┴────────────────────────────────────┐
│ demand-tree builder → market model → readiness model →        │
│ opportunity generator → Opportunity Score → diff engine →     │
│ AI narrative (optional, §11)                                  │
└──────────────────────────▲────────────────────────────────────┘
┌─ v1 PIPELINE (retained) ─┴────────────────────────────────────┐
│ scope (ProductGroup) → term spine → tags → BA join →          │
│ profit proxy → insight rules                                  │
└──────────────────────────▲────────────────────────────────────┘
                    all existing Pulse tables (no new syncs for v2 core)
```

New persisted tables beyond v1's three:

| table | grain | why persisted (not just payload) |
|---|---|---|
| `StiOpportunity` | one per opportunity, **stable key**, status lifecycle | survives across runs — the diff engine (§10) and outcome tracking need identity, not snapshots |
| `StiOpportunitySnapshot` | opportunity × run | how its numbers moved run-over-run |
| `StiActionOutcome` | promoted from v1 phase-2 to core | the learning system is now a headline feature, not an afterthought |

Stable key = hash(type · group · marketplace · subject), where subject is a
demand-node id, term hash, competitor ASIN, or SKU — so "Capture face
towels (USA)" is the *same* opportunity in every run until closed.

---

## 3. Executive Screen — eight questions, under a minute

Eight cards. Each card is **one item** — the top-scored member of its class
— with a dollar value, a one-line why, and a click-through to the full
opportunity. No charts on this screen. A card with nothing to say collapses
("No inventory risk this run") rather than padding.

| card | fed by | example (from local evidence, illustrative) |
|---|---|---|
| Biggest Opportunity | top `StiOpportunity` overall | "Face towels: 4.7k purchases/wk market, our share 0.1% — est. $9k/mo CM headroom" |
| Biggest Waste | Defend class | "$2.6k/mo on ASIN targets with zero orders" |
| Biggest Organic Gap | organic-push class | "'bath towels' — 2.7% purchase share on 148k weekly impressions; paid dependency 80%" |
| Biggest PPC Opportunity | scale class | "'white bath towels' ACOS 12% but capped by budget" |
| Biggest Listing Opportunity | listing class | "'hotel' appears in 3 of top-20 converting terms, absent from all group titles" |
| Biggest Product Opportunity | product-gap class | "Beach towels: 2.2k purchases/wk, we sell none" |
| Biggest Competitor Threat | threat class | "B0XXXX gained click share on 4 of our top-10 money queries, 3 weeks running" |
| Biggest Inventory Risk | readiness gate | "Scaling opportunities worth $6k/mo blocked: BTH cover 12 days" |

Below the cards: three numbers only — group revenue, TACoS, total open
opportunity value — each vs the previous run. That is the entire screen.

---

## 4. Business Opportunities (replaces the Action List)

An **opportunity** is a business case, not a task. Schema:

```
StiOpportunity
  key                stable hash (§2)
  type               capture_share · product_gap · organic_push · listing_fix
                     · scale_ppc · defend (waste/negative/bid-down)
                     · conquest · pricing_pack (future)
  title              "Capture 'face towels' demand — USA"
  why                one paragraph, deterministic, evidence-cited
  market_demand      purchases/wk (SQP totals) + volume, with as-of week
  current_share      our purchases ÷ market purchases (SQP)
  potential_revenue  $/mo — attainable-share model, §7.2
  potential_profit   $/mo CM — revenue × group margin proxy (ex-VAT)
  difficulty         1–5, from dependency count and type (§4.1)
  confidence         high/med/low — data volume + coverage + BA weeks (§5.3)
  required_actions[] concrete steps with owners' domains
                     (PPC · listing · catalog · inventory · pricing)
  timeline           rough: days (PPC) / weeks (listing, rank) / months (product)
  dependencies[]     machine-checked where possible: inventory cover
                     (InventorySnapshot), listing tokens (Product.title),
                     exact-target existence (AdsTargetingDailySnapshot),
                     margin adequacy (CampaignProfitDaily / COGS)
  status             open · in_progress · done · dismissed · expired
  score              §5
```

### 4.1 Difficulty (1–5, deterministic)

1 = PPC-only, assets exist (add negative, raise bid) ·
2 = new campaign/target needed · 3 = listing changes ·
4 = rank-building (sustained spend + time) ·
5 = new product/pack/colour required.
Each unmet machine-checked dependency adds severity; a stockout dependency
caps any spend-type opportunity at "blocked" regardless of value.

### 4.2 Ranking

Opportunities rank by **potential profit × win probability**, never by
advertising metrics. ACOS appears inside evidence, not in the ranking. The
board is 2-D: value vs difficulty, rendered as four quadrants — *Do now*
(high value, low difficulty), *Plan* (high, high), *Delegate* (low, low),
*Ignore* (low, high). The Executive Screen pulls from *Do now* first.

---

## 5. The Opportunity Score

### 5.1 Structure: dollars × probability, with gates — not a point soup

A single blended 0–100 index over ten inputs is unfalsifiable and
untunable. Instead the score **is** the expected value, in money, with two
multiplicative factors and hard gates:

```
Score ($/mo) = Headroom($) × WinProbability × MarginFactor
```

**Headroom($)** — from the market model (§7): attainable market purchases
beyond current share × ASP. Uses SQP `purchases_total`, our share, and the
attainable-share ceiling (§7.2).

**WinProbability (0–1)** — product of sub-factors, each mapped to [0.2, 1.0]
so no single factor zeroes the score silently:

| factor | signal | source |
|---|---|---|
| proof of conversion | our CVR on this node's terms vs group CVR | term spine |
| organic foothold | brand click/purchase share level | SQP |
| competitive intensity | concentration of top-3 clicked ASINs' click_share (HHI-like); fragmented market → higher | SQP `top_clicked_asins` |
| momentum | node volume trend across BA weeks | SQP (needs ≥3 wks, else neutral 0.6) |
| execution readiness | inventory cover, listing token coverage, campaign assets | InventorySnapshot · Product.title · targeting layer |

**MarginFactor** — group CM% proxy (ex-VAT), so a high-demand low-margin
pool ranks below a modest-demand high-margin one. `attribution_coverage_pct`
low → falls back to category referral-only margin and caps confidence.

**Gates (hard zeros, reported as "blocked", never hidden):** stockout on
required SKUs · margin proxy ≤ 0 · marketplace mismatch.

### 5.2 Why multiplicative

A weighted sum lets huge demand paper over zero readiness (the classic
"chase the biggest keyword" failure). Multiplication means a weak factor
drags the whole score proportionally — which is how an actual Head of
E-commerce reasons: *big market × can't win it = not an opportunity yet.*

### 5.3 Confidence is separate from score

Confidence (high/med/low) reflects **evidence quantity**: clicks/orders
behind CVR proof, BA weeks behind trend, SQP coverage of the node,
attribution coverage behind margin. Low-confidence high-score items render
as "investigate" rather than "invest". Confidence calibration is exactly
what the learning loop (§10) tunes over time.

### 5.4 Worked example (local-week numbers, illustrative)

"Face towels", USA: market 4,675 purchases/wk; our share 0.1%; attainable
ceiling from sibling nodes ("wash cloths" 4.1%, "kitchen towels" 6.3%) →
conservative 3%. Headroom ≈ 4,675 × 4.3 wk × 3% × $15 ASP ≈ **$9.0k/mo
revenue**. WinProb: conversion proof exists (washcloth terms convert),
foothold minimal (0.4), intensity unknown-neutral, readiness: product
exists (Wash Cloth SKUs answer face-towel demand) but listing tokens
missing ("face towel" absent from titles → listing_fix dependency).
MarginFactor ≈ 30% CM → Score ≈ $9.0k × ~0.45 × 0.30 ≈ **$1.2k/mo CM**,
difficulty 3, confidence low (one BA week). Board placement: *Plan*, with
"re-score when ≥3 BA weeks" attached. This is the level of reasoning every
opportunity carries.

---

## 6. Demand Tree (opportunity funnels)

### 6.1 Built from tags, not hand-drawn

The v1 taxonomy already decomposes every term into
(product_type, attributes[], room_usage, brand_class). A **demand node** is
an attribute combination over a product_type; the tree is the lattice those
combinations form, built bottom-up from actual terms — never a hardcoded
hierarchy:

```
bath_towel                                  ← every bath-towel-typed term
├─ +quality:luxury        "luxury bath towels"
│   ├─ +color:white       "luxury white bath towels"
│   │   └─ +pack:4        "luxury white bath towels 4 pack"
│   └─ +use:hotel         "luxury hotel bath towels"
├─ +color:white …
└─ +use:hotel …
```

Node metrics roll up member terms across both clocks: SQP volume + market
purchases + our share (weekly clock) · our spend/sales/CVR (daily clock).
Nodes below a materiality floor (volume and spend both negligible) collapse
into their parent to keep the tree readable.

### 6.2 Node coverage state — where funnels become decisions

Every node is machine-classified:

| state | tests | opportunity emitted |
|---|---|---|
| **Winning** | share ≥ target, ACOS ≤ target | none — protect via Ops Queue |
| **Contested** | demand + our presence, share flat/falling | capture_share |
| **Uncovered — campaign** | demand + product exists + **no exact target** (targeting-layer `expression` check) | scale_ppc (new campaign) |
| **Uncovered — listing** | demand + product exists + node's defining tokens absent from every group `Product.title` | listing_fix |
| **Uncovered — product** | demand + **no product matches the attribute set** (no 6-pack SKU, no grey variant — checked against catalog categories/titles) | product_gap (§6.3) |
| **Irrelevant** | off-category tokens | negative-keyword fodder |

"Missing keyword coverage / missing campaigns / missing listing
optimisation / product gaps" from the brief are therefore not four reports —
they are four *states of the same tree*, each emitting a typed opportunity.

### 6.3 Product Gap Intelligence

Uncovered-product nodes get a business case, not a mention: market demand
(SQP totals), attainable share (median share on covered sibling nodes —
"we hold 4–6% of adjacent pools, assume 3% here"), revenue and CM at group
margin, difficulty 5, timeline months, dependencies "catalog: new
pack/colour/product". Examples the local week already suggests: beach
towels (2.2k purchases/wk, zero presence), bathroom sets (374/wk,
bundle-shaped — cross-check `BAMarketBasketWeekly` co-purchase before
proposing). Gap opportunities feed planning conversations, not the Ops
Queue — the board's *Plan* quadrant exists for them.

### 6.4 Listing quality (absorbs v1 Customer Language)

Per group: tokens ranked by conversion-weighted frequency in *converting*
terms, diffed against group `Product.title` texts (in catalog today; bullet
points are a future sync). Output is not a word cloud — it's listing_fix
opportunities: "add 'hotel'/'face towel' to titles", each valued by the
demand pool behind the missing token. Cited example: "hotel" ranks in
converting terms while absent from all group titles (verify in prod — V8).

---

## 7. Market Share Intelligence

### 7.1 From rank to share

Search Frequency Rank survives only as a popularity tiebreaker. The
operative numbers per query/node, all from SQP fields Pulse already stores:

- **Market size**: `purchases_total` (units/wk) × ASP → $/wk market
- **Our share**: `asin_purchase_count ÷ purchases_total`
- **Funnel benchmark**: market CTR (`clicks_total/volume`) and market
  click→purchase rate vs ours — pinpoints *where* we lose (visibility vs
  click appeal vs conversion)
- **Share trend**: share per BA week (needs depth — V1)

ASP: our group ASP where we sell; for gaps, our nearest sibling's ASP.
Stated on every card. (Competitor price data would sharpen this — future
sync, §12.)

### 7.2 Attainable share, not fantasy TAM

Headroom is *not* (100% − share) × market. The ceiling is evidence-based:
the **best share we already achieve on comparable nodes** (same
product_type, similar intensity), defaulting to sibling-median when thin.
Growth potential = (ceiling − current) × market $ × margin. Conservative by
construction; the learning loop (§10) recalibrates ceilings from realised
outcomes.

### 7.3 Competitive intensity

Concentration of `top_clicked_asins` click shares per query: top-3 holding
~80% = entrenched (lower WinProb); fragmented = winnable. Persistence of
the same ASINs across weeks raises intensity further. Feeds §5 and §8.

---

## 8. Competitor Opportunity Intelligence

Raw competitor lists (v1) become four detectors over SQP top-3 arrays +
`BAItemComparisonWeekly`, each emitting typed output:

| detector | signal | output |
|---|---|---|
| **Riser** | competitor ASIN's click_share up N weeks running on our money queries | threat card (§3) + defend opportunity |
| **Fader** | click_share declining on queries we serve | capture_share opportunity: "B0XXX is fading on 'hand towels' — take the slot" |
| **Vulnerable** | high click_share in `top_clicked` but weak/absent in `top_purchased` — shoppers consider them and don't buy | conquest opportunity with high WinProb: their traffic converts poorly, ours converts |
| **Fortress** | dominant click *and* purchase share, stable | explicit anti-recommendation: conquesting here is scored low — knowing where *not* to spend is intelligence too |

Conquest worthiness = their vulnerability × our conversion proof on the
query family × margin — so conquest campaigns are proposed only where the
economics support them, replacing v1's flat "competitor-branded term
economics" table. `BAItemComparisonWeekly` adds the comparison-shopping
axis: who Amazon says shoppers weigh us against, week over week.

Honesty note: SQP top-3 arrays see only the top 3 ASINs per query — share
shifts below rank 3 are invisible. Stated on the page; full competitor
tracking is a future source (§12).

---

## 9. Ops Queue — the tactical layer, kept but demoted

v1's genuinely decision-driving PPC output survives intact as a work queue
under the board, not as the product: negative candidates (grouped by
leaking campaign, paste-ready), bid-downs, budget shifts, keyword→term
drift fixes, cross-contamination cleanups. Every Ops item links up to the
opportunity it defends. v1's insight rules (WASTE/NEG/SCALE/LISTING/ORG/
COMP/DRIFT/XCON, thresholds as named constants) remain the generation
mechanism, now emitting into either the board or the queue by weight.

---

## 10. History & Learning (the run store pays off)

Because opportunities have stable keys and runs are persisted:

- **Run diff**: new opportunities · value moved (share shifted, demand
  grew) · disappeared (demand faded or captured — distinguished by which
  input moved) · expired (N runs without action, demand gone).
- **Outcome tracking**: on done, snapshot trailing-30d metrics; re-measure
  +14d/+30d; verdict improved/flat/worsened with the deltas shown.
- **Scoreboard**: realised $ from completed opportunities vs estimated —
  the credibility metric for the whole system, shown on the History page.
- **Calibration**: realised-vs-estimated feeds back into WinProbability
  sub-factor weights and attainable-share ceilings (§7.2). Manually
  reviewed adjustments in v2 (a config change with evidence attached);
  automated tuning only after enough outcomes exist.
- **Failure honesty**: recommendations that failed stay visible with their
  post-mortem numbers. A system that hides its misses teaches nothing.

This is the moat over Helium 10 / Pacvue-class tools: they recommend;
none of them remember whether they were right, because they don't hold the
P&L. Pulse does.

## 11. AI layer — narrator, never author

Deterministic engine produces every number, rank, and recommendation. The
AI (existing `ai_insights` pipeline + `AIRecommendation` workspace, source
tag `sti`) does three things only:

1. **Explains why** — turns run-diff + evidence into prose: "Share on
   'bath towels' fell 0.8pt; the drop coincides with B0XXX's click-share
   rise and our 12-day BTH stock dip" — citing payload fields, hard-fail
   if a cited field is absent (schema-checked, the guard against invention).
2. **Sequences** — do-now vs can-wait, from score, difficulty, dependency
   readiness, inventory urgency.
3. **Briefs** — the one-paragraph executive summary on card 0.

Prompt receives the payload and may reference only it; responses are stored
beside the run per the existing `AIRecommendation` audit pattern.

---

## 12. Long-term: the decision engine, staged

The Center becomes Pulse's growth brain by adding *inputs* to the same
opportunity pipeline — the provider registry and score factors absorb each
without redesign:

| stage | source | unlocks |
|---|---|---|
| now (v2) | everything above | share, gaps, funnels, learning loop |
| +1 | per-ASIN Business Reports (sessions, USP) | true traffic-side conversion; organic funnel per product — the highest-value missing sync |
| +1 | listing content sync (bullets, A+) | full listing-quality scoring beyond titles |
| +2 | pricing/Buy Box | price-driven share loss detection; pricing_pack opportunities |
| +2 | reviews/VoC | language mining, defect-driven CVR fixes |
| +2 | AMS hourly | dayparting; intraday budget defense |
| +3 | competitor tracking (beyond SQP top-3) | full-market share model |

What makes it structurally stronger than Helium 10 / DataDive / Pacvue /
Perpetua / Quartile in one sentence each: they see keywords, not **your
margins** (Pulse prices every opportunity in CM, ex-VAT); they see clicks,
not **your inventory** (Pulse blocks spend into stockouts); they see one
marketplace lens, not **four with clean currency walls**; they recommend
and forget — Pulse **keeps score on itself** (§10).

---

## 13. Verification queue (v1's V1–V6 stand, plus)

| id | question | consequence |
|---|---|---|
| V7 | Are `purchases_total` / `clicks_total` / `cart_adds_total` populated and sane across prod weeks and all 4 marketplaces? | the market model (§7) rests on them; local week says yes for USA |
| V8 | `Product.title` completeness per marketplace (listing-token checks) | listing_fix opportunity quality |
| V9 | Do `top_clicked_asins` arrays carry `brand` + `click_share` consistently in prod? | competitor detectors (§8) |
| V10 | SQP week depth per marketplace (sharpened V1): ≥12 weeks → trends + momentum on; 3–11 → trends, no seasonality; <3 → §7 ships share-only, momentum neutral | degradation ladder is designed, not improvised |

## 14. Open decisions for review

1. **Attainable-share ceiling** default (sibling-median, §7.2) — sign off or
   set house numbers per group.
2. **Opportunity expiry** — N runs without action → expired. Propose N=6.
3. **AI narrative in v2 or v2.1** — engine is complete without it; prose
   layer is additive.
4. **v2 cut line** — propose: Executive Screen + Opportunity Board + Demand
   Tree + Ops Queue + run diff ship first; outcome scoreboard (§10) follows
   two weeks later (it needs elapsed time to measure anything anyway);
   §8 detectors gated on V9/V10.
5. **v1 document** — marked superseded, kept for the engineering detail
   (queries, taxonomy, KPI formulas) that v2 builds on rather than repeats.
