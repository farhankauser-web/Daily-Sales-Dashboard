# Search Intelligence Center

files: `apps/dashboard/views_sti.py`, `apps/dashboard/sti/` (15 modules),
       `apps/dashboard/management/commands/seed_product_groups.py`,
       `templates/dashboard/sti_center.html`
verified against: `77fd873` · 2026-08-07

Where the next profitable growth opportunity is, and what to do about it. The
one page in Marketing that ends in decisions priced in money rather than in
advertising metrics.

## Purpose

Every other Marketing page reports: this campaign spent that much, this term
converted at this rate. Someone still has to read the tables and work out what
to do. This page does that step, and states its answer as a ranked list of
**business opportunities**, each carrying an expected contribution margin per
month, a difficulty, a confidence, and the actions that would realise it.

Without it, the search-term fact table is 100,000 rows a day that nobody has
time to read, and the decisions that matter — where to push rank, which
listing to rewrite, which product we do not sell but should — are made on
instinct.

## Data source

The Center creates no data. It joins what Pulse already syncs, and its value
comes from the join.

| Source | Grain | Authoritative for |
|---|---|---|
| Search-term daily rows | marketplace × day × campaign × term | spend, clicks, ad sales, orders |
| Advertised-product daily rows | marketplace × day × ad group × ASIN | which product group an ad group's spend belongs to |
| Brand Analytics search-query weekly | marketplace × week × ASIN × query | **market-total** purchases and clicks, our share of them |
| Per-SKU daily snapshot | marketplace × day × SKU | group revenue, units, average selling price, **and the contribution-margin rate** |
| Catalog | ASIN | which product types we sell, and the listing titles |
| Inventory snapshot | ASIN × day | stock cover — the gate on every spend recommendation |

**Sources keep their own clocks, by design** (`MKT-D-012`). Advertising is daily
at T-2; Brand Analytics is weekly and lands after its week closes; settled
per-SKU revenue is daily but further behind; inventory is a snapshot with no
period at all. Nothing is forced onto a common window. Brand Analytics joins to
advertising on the search-term text, never on the date, and every figure carries
the period it came from.

## Scope

Covers the report framework, the opportunity model and its score, and the
executive screen.

**Not covered:**
- per-term tables and signal filters — [search-terms.md](search-terms.md)
- campaign-level profit — [campaigns.md](campaigns.md)
- how search-query share is synced — [../brand-analytics/search-queries.md](../brand-analytics/search-queries.md)
- spend-to-SKU attribution — [sku-allocation.md](sku-allocation.md)

## Business workflow

```
Product group + marketplace + date range → Generate
   → catalog ASINs → ad groups that advertised them (weighted)
   → classify terms → join market share → price opportunities
   → ranked board + executive screen → act → status recorded
```

A report is **generated**, not browsed. The user picks one product group, one
marketplace and a date range; Pulse computes the whole report and stores it.
Stored runs are what make the history meaningful: the same opportunity keeps
one identity across runs, so it can be tracked from first appearance to done.

## Business rules

1. **This is a decision-support module, not a reporting one.** It does not
   reconcile revenue. It answers where we are growing organically, where we are
   losing share, which opportunities deserve investment, which PPC actions to
   take, and which products to improve or launch. Financial efficiency belongs
   in Financials and Reporting; `MKT-D-012`.
2. **Each metric uses the cadence its question needs**, not the cadence other
   metrics use — advertising at T-2, organic share on the latest completed
   Brand Analytics period, opportunity valuation on a long-run settled margin
   and average selling price, inventory on the latest snapshot. The obligation
   is transparency: every significant metric states its **source**, its
   **reporting period** and its **Data As Of** date. `MKT-D-012`
3. **Decision quality outranks reporting accuracy.** A recommendation supported
   by older market data is shown with its period stated, not suppressed because
   another dataset is newer. `MKT-D-013`
4. **The opportunity is the product.** Every element either sharpens an
   opportunity, prices one, or gets out of the way. Anything that reports
   without influencing a decision becomes evidence, moves to a drill-down, or is
   removed; the first screen stays executive and detail lives inside the
   opportunity. AI may explain and rank, never invent. New data sources
   strengthen opportunities rather than adding sections. `MKT-D-014`
5. **One marketplace per report.** Currencies never mix, and margins are
   measured on revenue ex-VAT while displayed revenue stays gross.
6. **The report runs on an Amazon reporting period** — a Sunday-start week under
   Amazon's numbering, or a calendar month — not a rolling range. Brand
   Analytics is read for that period only, so advertising and market data
   describe the same days by construction. A named period is also fixed forever,
   which is what makes "we acted in Week 30, did Week 31 improve?" answerable.
   Only completed periods are offered, each labelled with its day coverage and
   whether market data exists. `MKT-D-015`
7. **A ratio never divides across different spans**, and trend or causal claims
   stay gated on having enough comparable periods. These are the two guards that
   survive from alignment thinking; they are arithmetic and honesty, not
   reconciliation. `MKT-D-013`
8. **A report is scoped by SKU/ASIN, from the product catalog.** A group is a
   set of `Product.category` values; the advertising that belongs to it is
   whichever ad groups advertised those ASINs. Campaign naming is for reading a
   report and never scopes one.
9. **Where an ad group serves more than one product group its spend is
   apportioned by ASIN share**, so group totals still sum exactly to the spend
   the catalog can place. Where it serves one — the norm — the weight is 1 and
   the arithmetic is a plain filter.
10. **Opportunities are ranked in money, never in advertising metrics.** The
   ranking key is expected contribution margin per month, so a negative-keyword
   saving and a new product line compete on one list.
11. **The score is a product, not a sum** — headroom × win probability × margin
   rate. A weighted sum would let a huge market outvote the fact that we cannot
   win it; multiplying means a weak factor drags the whole case down.
12. **Attainable share is evidence, not ambition.** The ceiling on any share
   opportunity is the best share we already hold on a comparable demand pool,
   never the whole market.
13. **Confidence is separate from value.** Confidence measures how much evidence
   stands behind the estimate. A high-value, low-confidence item is an
   investigation, not an investment.
14. **Stock cover blocks spend recommendations.** An opportunity that would push
   more spend into a product about to run out is shown as blocked, with its
   value stated, rather than recommended or silently dropped.
15. **An empty report must explain itself.** A blank page cannot be told apart
   from a broken pipeline, so the report names the reason it found nothing.

## Opportunity types

| Type | Fires when | Decision |
|---|---|---|
| Defend | spend with no orders | negative keyword, lower bid, or tighter targeting |
| Scale PPC | converting well inside the group's ACOS | bid up, or promote to an exact target |
| Listing fix | shoppers click and then do not buy | rewrite the listing, not the bid |
| Organic push | a large pool we convert but barely rank in | build rank instead of renting the demand |
| Capture share | present but not winning a pool | close the gap to the market's conversion rate |
| Product gap | real demand for something the catalog does not sell | evaluate a new product line |
| Conquest | our competitor-ASIN targeting is clearly better or worse than group average | expand it, or cut it and redeploy |

## User actions

| Action | Who | Result |
|---|---|---|
| Choose group, marketplace and range | anyone | defines the report |
| Generate | anyone | computes and stores a run |
| Filter the board by type or priority | anyone | narrows to one kind of decision |
| Open an opportunity | anyone | evidence, score breakdown, actions, dependencies |
| Start / done / dismiss / reopen | anyone | records what the team acted on |
| Open an earlier run | anyone | the report exactly as it stood |
| Open the outcome scoreboard | anyone | whether acted-on opportunities worked, and the hit rate |
| Export outcomes to CSV | anyone | every column the verdict rests on, for checking outside Pulse |
| Curate a product group | anyone | which catalog categories and ASIN overrides define it |
| Ask for a briefing | anyone | prose explaining the report — on demand, because it costs money |

## System behaviour

- A run is computed **on demand**; nothing is scheduled. There is no cron
  dependency and no overnight job to miss.
- Terms are **classified once and reused**. A term's classification persists,
  so a report re-reads it rather than re-deriving it.
- Opportunities are **upserted by a stable identity**, so a run updates the
  ones it still finds, counts the ones it no longer finds, and expires them
  after several consecutive absences.
- A generator that fails is **logged and skipped**; the rest of the report
  still renders. A report with six of eight cards is useful, a traceback is not.
- Every dataset carries a **Data As Of** stamp — its source, the period the
  report used, and the newest data it holds. Availability is *measured* at run
  time rather than assumed from a declared lag, because the production schedule
  is hand-maintained and cannot be relied on (`INFRA-001`).
- **Valuation is deliberately off the report window.** Contribution margin and
  average selling price come from a 90-day trailing window of settled per-SKU
  data. They are structural properties — across three months the USA rate moved
  30.3% → 30.1% → 28.7% — so pricing an opportunity uses the group's margin, not
  the selected period's. Tying them to the window made a 7-day report fall back
  silently to a pessimistic constant.
- **Paid share is withheld rather than approximated.** It divides ad sales by
  settled revenue, and those sources cover different spans, so it uses only the
  overlapping days — and says nothing at all when the overlap is under a week. A
  14-day window once overlapped a single selling day and produced a 2074%
  "paid share".
- **The first screen is executive.** The decision total, four framing figures and
  the eight cards. Full performance figures, the intent breakdown and data
  quality live in drawers beneath the board (`MKT-D-014`).
- **The briefing explains; it never authors.** The narrator receives the stored
  payload and nothing else, is told to reference only opportunities that already
  exist, and its answer is checked back against them — anything it names that the
  engine did not produce is flagged in the UI rather than quietly shown as a
  recommendation. It is generated on demand and cached on the run, so re-opening
  a report does not re-bill the call. A narrator failure never affects the
  report (`MKT-D-014`).
- **Acting on an opportunity is measured.** Marking one done records the period
  it was acted in; the following period is the test. What counts as success
  differs by type and is stated per row — a negated term succeeds when its spend
  falls, a scaled one when sales rise. Volume metrics are compared **per day**,
  because a result period with fewer days of data would otherwise read as a
  decline. Organic-share outcomes need Brand Analytics for both periods and say
  "awaiting data" rather than showing a flat line.
- **Each run is compared to the last one** for the same group and marketplace:
  what is new, what moved by more than 10%, and what is no longer found. When
  the two runs cover different windows — or rest on different Brand Analytics
  weeks — the comparison is **withheld with the reason**, because a period
  change reported as a score change is worse than no comparison (`MKT-D-012`).

## Observations — not gaps

- **Ad sales can exceed group revenue.** Ad sales are attributed to the click
  date over a seven-day window and include halo sales of other SKUs, while
  group revenue counts only this group's SKUs on the order date. A paid share
  above 100% is an attribution property, not an error, and the report says so.
- **Long-tail waste dwarfs any single wasteful term.** In the USA Bath Towels
  group, 8,303 terms had clicks and no orders, together far exceeding every
  individually-actionable term. No negative keyword reaches them; it is a
  targeting-structure finding, and it is reported as one.
  *Source: dev snapshot; provisional.*
- **Empty inventory tables locally are expected.** The laptop runs no scheduled
  jobs. Absent cover data means the sync has not run here.
- **Competitor share movement cannot be observed, and never will be from Brand
  Analytics.** The detectors originally designed — which competitors are gaining
  or losing share — rested on per-query top-3 ASIN arrays. Amazon does not
  publish those at query level (`ba_reports.py` writes them empty by design),
  and `GET_BRAND_ANALYTICS_ITEM_COMPARISON_REPORT`, which could have supplied
  them, is **deprecated as of 2026**. This is an external data limit, not a
  defect. What *is* measurable is our own conquest spending, so the Conquest
  opportunity reasons about that instead.
- **An unmeasurable score factor is not a penalty.** Competitive intensity was
  derived from those same empty arrays, so it was unknown on every opportunity
  and was quietly applying a 0.6 multiplier — a 40% haircut across 103
  opportunities for a signal carrying no information. Unknown sub-factors are
  now dropped from the product rather than defaulted.
- **TACoS is deliberately absent.** Ad spend over total revenue is a
  financial-efficiency ratio that answers none of this module's questions, and
  it already has a home on Daily P&L and the Management P&L. It was also the
  metric that motivated an alignment engine this module does not need
  (`MKT-D-012`, `MKT-D-014`).
- **Campaign names are missing for UAE and KSA.** The campaign dimension has no
  rows there, so the "where it happens" list falls back to campaign ids. The
  report itself is unaffected — scoping does not read that table.
  *Source: dev snapshot; provisional.*

## Known gaps

| ID | Title | Priority | Classification |
|---|---|---|---|
| `MKT-STI-004` | Brand Analytics ingestion may not be scheduled in production | P2 | configuration |
| `MKT-STI-005` | Campaign names are unavailable for UAE and KSA | P3 | configuration |

`MKT-STI-001`, `MKT-STI-002` and `MKT-STI-003` were closed by the move to
catalog scoping — see the Closed table in [gaps.md](gaps.md).

Full entries in [gaps.md](gaps.md).

## Related

- Design of record: `plans/search-intelligence-center.md`
- [search-terms.md](search-terms.md) — the per-term page this one sits above
- [../brand-analytics/search-queries.md](../brand-analytics/search-queries.md) — where market share comes from
