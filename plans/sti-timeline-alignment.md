# Search Intelligence — reporting timeline abstraction

Status: **WITHDRAWN — superseded by `plans/sti-marketing-timeline-review.md`.**
The alignment engine proposed here applies an accounting rule (two numbers may
not be compared unless they cover the same period) to a module whose objective
is marketing decisions. It would have suppressed valid signals — the whole USA
`organic_push` set — to satisfy a reconciliation requirement this page does not
have. Kept for the investigation in §1, which stands and is still cited: the
four cadences, the TACoS defect, the Sunday/Monday help_text bug, the absence of
monthly Brand Analytics, and the missing Sales & Traffic sync.
Author: Claude · 2026-08-07
Extends `plans/search-intelligence-center.md` §1.4 (the "two clocks" principle),
which this replaces with a general mechanism.

All measurements below are from the local dev snapshot, reference date
2026-08-06, and are **provisional until re-run on production**. The *shape* of
each problem is structural and holds regardless.

---

## 1. What the investigation found

### 1.1 There are not two clocks. There are at least four.

| Source | Cadence | Latest | Actual lag | Used for |
|---|---|---|---|---|
| Search-term / advertised-product / targeting | daily | 2026-08-04 | **2 days** | spend, clicks, ad sales |
| `CampaignProfitDaily` | daily | 2026-08-04 | 2 days | (no longer used) |
| `DailySkuSnapshot` | daily | 2026-07-25 | **12 days** | group revenue, ASP, **margin** |
| `DailyMetric` (sessions) | daily | 2026-07-25 | 12 days | future organic funnel |
| `BASearchQueryWeekly` + 4 siblings | **weekly** (Sun–Sat) | 2026-06-06 | **61 days** | market size, our share |
| `InventorySnapshot` | **snapshot** (no period) | — | — | the spend gate |

The design assumed ads and Brand Analytics. The source that actually breaks
things is `DailySkuSnapshot`, which I introduced last session as the margin
source without checking its lag.

### 1.2 A live, quantified defect in the shipped code

The report applies **one window to every source**. For the 30-day window
2026-07-07 → 2026-08-05:

| | days covered | value |
|---|---|---|
| ad spend | 27 | $75,994 |
| SKU revenue | **14** | $198,968 |
| **TACoS as reported** | mismatched | **38.2%** |
| **TACoS on aligned days** | 14 v 14 | **21.2%** |

TACoS is overstated by 80% because the numerator spans 27 days and the
denominator 14. `paid_share` has the identical fault.

Worse, for **Last 7 days** (2026-07-30 → 2026-08-05) `DailySkuSnapshot` has
**zero rows**. Group revenue reads 0, TACoS is null, and the margin rate
silently falls back to the pessimistic constant — so every opportunity value on
a 7-day report is computed from a fallback while the page displays no warning
that the window is the reason.

This is not a local-data artefact in kind. Any lag difference between the two
sources produces it; the snapshot merely makes it large enough to see.

### 1.3 No Brand Analytics week overlaps any current ads window

BA holds one week, 2026-05-31 → 2026-06-06. The 30-day ads window starts
2026-07-07. **Overlap: none.**

Yet **100 of 439 opportunities (23%)** are BA-derived, and on USA they include
the entire `organic_push` set — items whose reasoning is *"we already convert
this demand through ads, so the gap is visibility"*. That sentence compares a
**May** market share against **July–August** advertising behaviour. The current
build labels the staleness but still ranks the opportunity.

### 1.4 The week boundary is Sunday, and the model says Monday

`BASearchQueryWeekly.week_start` carries `help_text='Monday (start of week)'`.
The data is **Sunday-start, Saturday-end** — which matches
`docs/brand-analytics/search-queries.md` ("the week is Amazon's week, Sunday to
Saturday") and contradicts the field. Any timeline layer that reaches for
`isocalendar()` or assumes Monday will misalign every window by one day. Fix
the help_text; never derive the boundary from the calendar.

### 1.5 The schedule cannot be known from here — which is why the layer must measure

`sync_daily_metrics` is scheduled daily at 10:00, so `DailySkuSnapshot` should
be near-current in production and the 12-day gap above is the expected local
condition (the laptop runs no jobs).

But `ingest_brand_analytics` **is not in `deploy/crontab.txt` at all**, and
`INFRA-001` (P1) records that the template cannot be installed on EC2 and the
server's crontab is hand-maintained and has drifted. So neither the BA refresh
cadence nor the true `DailySkuSnapshot` lag is knowable from this machine.

That is the strongest argument for the design in §2.1: **availability is
measured per source at run time, never inferred from a declared lag.** A
constant would encode an assumption about a schedule nobody can currently
verify, and would fail silently the day a job stops.

It also raises the stakes on §5.1: if BA ingestion is manual in production,
stale market data is the normal case rather than a local artefact, and a design
that withholds every BA-derived opportunity when unaligned would disable 23% of
the feature permanently. The per-metric rule in §2.3 is what prevents that —
BA-only metrics stay valid within their own week.

### 1.6 Monthly Brand Analytics does not exist in Pulse

All five BA models are weekly. There is no monthly BA table, and nothing syncs
one. The abstraction should define the cadence so a monthly source can be
registered later, but there is nothing to wire today — building a monthly
mapping path now would be speculative code with no data behind it.

Per-ASIN Business Reports are also not synced; only account-level daily
sessions exist on `DailyMetric`.

---

## 2. The proposed abstraction

### 2.1 Shape

A new module, `apps/dashboard/sti/timeline.py`, owning **all** date logic. No
other module computes a date range, a lag, or a period boundary.

```
CADENCE = daily | weekly | monthly | snapshot

SourceSpec
  key              'ads' · 'sku_revenue' · 'ba_sqp' · 'inventory'
  cadence          one of the above
  nominal_lag_days what we expect
  boundary         for weekly: the observed first-day-of-week, READ FROM DATA
  availability()   MEASURED max(date) per marketplace — never assumed

Period      start · end · grain · complete · source_key
ReportingWindow
  requested        what the user asked for
  per_source       {key: Period}      each source on its own clock
  aligned          Period | None      the span every required source covers
  alignment        exact | partial | none
  notes            why it came out that way, in plain words
```

**Availability is measured, not declared.** The 12-day `DailySkuSnapshot` lag is
exactly what a nominal-lag constant would have hidden. `nominal_lag_days` is
used only to explain a gap, never to compute a window.

### 2.2 The rule that makes comparisons valid

**Every metric declares the sources it needs. The timeline layer decides
whether it can be computed.**

- **Single-source metric** → runs on that source's own period, labelled with it.
- **Multi-source metric** → requires a non-null `aligned` period, and is
  computed *only* over that period. If there is no overlap, the metric is
  **withheld with a stated reason**, never approximated.

That single rule fixes §1.2 and §1.3 together, and it is what the user asked
for: alignment enforced by the engine, not by each author remembering.

### 2.3 Alignment is per METRIC, not per section

This is the distinction that keeps the feature useful instead of blanking it.

| Metric | Sources | Needs alignment? |
|---|---|---|
| Wasted spend, ACOS, CTR, CVR | ads only | no — ads period |
| Group revenue, ASP | sku_revenue only | no — its own period |
| **TACoS, paid share** | ads + sku_revenue | **yes** |
| **Margin rate** | sku_revenue only | no — but must be labelled with its period |
| Market size, our share, rank | ba_sqp only | no — internally consistent within its week |
| **"We convert this demand"** (organic_push) | ads + ba_sqp | **yes** |
| Product gap | ba_sqp + catalog | no — catalog is timeless |
| Momentum / share trend | ba_sqp across weeks | no, but needs ≥3 weeks |

So under today's data, `product_gap` survives (both numbers come from one BA
week) while `organic_push` is withheld (it mixes a May market read with August
ad behaviour). That is a sharper and more defensible outcome than suppressing
the whole Brand Analytics section.

### 2.4 Window resolution, worked

**Last 7 days.** Anchor T-2. Ads period = the 7 days to T-2. `ba_sqp` period =
the latest *complete* Sun–Sat week whose end ≤ T-2. `sku_revenue` period = the
7 days to its own measured availability. `aligned` = intersection of ads and
sku_revenue (and BA where a market metric is requested). Header states all
three, and the BA week by name.

**Last 30 days.** Ads = 30 days to T-2. BA = the complete weeks that
**overlap** that span — aggregated, not "the latest N weeks". If zero overlap:
`alignment = none`, market metrics withheld, market *context* still shown with
its own date and a plain statement that it predates the window.

Aggregating overlapping BA weeks (rather than picking one) is the correct
answer to "Last 30 days" while Pulse has no monthly BA. When a monthly BA
source is registered, the layer picks whichever cadence covers the request with
the least distortion, and records which it chose in `notes`.

**A validation the design gets for free:** when the aligned period is whole
weeks, the two monthly normalisations agree — ads `DAYS_PER_MONTH / days` and
BA `WEEKS_PER_MONTH / weeks` converge (28 days → 1.087 vs 4 weeks → 1.0875).
Divergence beyond rounding means the window was resolved wrongly, so it is
worth asserting in a test.

---

## 3. Conflicts this creates, and how each resolves

### 3.1 `StiReportRun` stores one date pair — CONFLICT

It has `date_from` / `date_to` and nothing else. A run whose sources cover
different periods cannot be described by one pair, and a stored run that cannot
describe its own basis is not auditable.

**Resolution:** add a `windows` JSON field holding the serialised
`ReportingWindow`, and keep `date_from`/`date_to` as the *requested* range for
indexing and display. Migration required. No data loss — existing runs were
cleared last session.

### 3.2 The spine is aggregated over one window — CONFLICT

`spine.build()` returns one aggregate. Aligned metrics need a second aggregate
over a shorter span, and re-querying doubles the expensive query.

**Resolution:** aggregate the spine once by `(term, ad_group, week_bucket)` and
roll up in Python to whichever period a metric declares. One query, both
answers. Cost: roughly 3–4× the grouped row count; the USA worst case is
currently 466 ms, so the sub-second budget survives. Verify, do not assume.

### 3.3 Score normalisation is hardcoded — CONFLICT

`config.DAYS_PER_MONTH / scope.days` and `WEEKS_PER_MONTH` are constants
applied by the generators. Under per-metric periods the divisor differs by
metric.

**Resolution:** the monthly factor comes from the `Period` a metric was
computed over, supplied by the timeline layer. `config.py` keeps the constants;
the generators stop choosing which to apply.

### 3.4 Historical comparison and the diff engine — CONFLICT

Two runs of "Last 30 days" a week apart can resolve to different aligned
windows as new BA weeks land. A score movement between them would then be a
**period change masquerading as a business change** — the exact failure the
learning loop exists to avoid.

**Resolution:** `StiOpportunitySnapshot` records the period basis of the run.
The diff compares like for like, and where the basis moved it reports "period
changed" instead of a delta. This is a correctness requirement for the outcome
scoreboard, not a nicety.

### 3.5 Opportunity generation — CONFLICT (behavioural)

`organic_push` and `capture_share` mix ads and BA. Under §2.2 they are withheld
when unaligned. **On today's local data that removes the entire USA
`organic_push` set — the current top-ranked items.**

That is the correct outcome: those recommendations compare a May market to an
August account. In production with regular SQP sync the weeks will overlap and
they return. But it is a visible change and it is the user's call — see §5.

### 3.6 Opportunity scoring — RESOLVED, no conflict

The stable key is `(type, group, marketplace, subject)` and carries no date, so
identity survives any window change. `momentum_factor` already requires three
weeks; it gains the additional requirement that the weeks be recent.

### 3.7 Future AI insights — no conflict, a constraint

The narrator receives the payload only. It must also receive `windows`, and the
schema check must reject any narrative asserting causality between two metrics
whose periods do not overlap. Cheap to add now, impossible to retrofit once
prose is being generated.

### 3.8 Financials and Reporting — no conflict

The timeline layer is new, read-only, and lives inside `sti/`. It reads
`max(date)` per source and writes nothing. Consistent with the standing
constraint.

### 3.9 `config.py` date constants — tidy-up, not conflict

`ANCHOR_OFFSET_DAYS`, `DATE_PRESETS`, `MAX_RANGE_DAYS`, `BA_WEEKS_*`,
`BA_MAX_STALENESS_DAYS`, `DAYS_PER_MONTH`, `WEEKS_PER_MONTH` move behind the
timeline layer as source specs. `runner.resolve_dates()` is deleted; callers
ask the timeline layer.

---

## 4. What gets built, in order

1. `timeline.py` — cadences, source specs, measured availability, resolution.
   Pure functions over dates plus one `max(date)` query per source.
2. Fix `BASearchQueryWeekly.week_start` help_text (Sunday, not Monday).
3. `StiReportRun.windows` JSON + migration; snapshot period basis.
4. Spine aggregation by week bucket; roll-up helper.
5. Metric source declarations; withhold-with-reason path.
6. Generators take their period from the metric contract.
7. UI: per-source "as of" stamps, and an explicit withheld state distinct from
   empty.
8. Docs: new business rules, close/replace the staleness gap `MKT-STI-004`.

Re-verification afterwards: the 64-run regression; TACoS recomputed and
compared against the aligned figure by hand; a test asserting the two monthly
normalisations converge on a whole-week window; performance re-measured.

---

## 4a. Revenue source — a premise worth correcting

The instruction was to take revenue from *the Business Report by child ASIN,
which matches the exact time frame and stores per-SKU / product-group data*.
Two facts change what that means in practice:

**The per-ASIN Business Report is not in Pulse.** There is no Sales & Traffic
ingestion — the SP-API report types Pulse requests are the four Brand Analytics
reports, the date-range financial transactions report, and
`GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL`. `DailyMetric.sessions`
and `page_views` exist as columns with **nothing populating them** (both sum to
zero across the whole table). Adding Sales & Traffic would be a new Amazon sync,
which Phase 1 explicitly excluded.

**What Pulse has already meets the requirement.** `DailySkuSnapshot` is built
from the All-Orders report keyed on **order date**, at `(marketplace, date,
sku)` grain. That is per-SKU, rolls up to a product group through the catalog,
and is order-date aligned — so it matches a requested window *exactly*, by
construction. It is the same basis Daily P&L and SKU Profitability use.

So the misalignment in §1.2 is **not** a grain problem or a timeframe-basis
problem. It is purely a **freshness** problem: the ads sync had reached
2026-08-04 while the revenue sync had reached 2026-07-25. Switching source
would not fix that; only measuring availability does.

**Resolution:** keep `DailySkuSnapshot`, and let the timeline layer clamp
TACoS and paid share to the overlap, withholding them when the overlap is too
short to mean anything. When the revenue sync is current — the production
expectation, `sync_daily_metrics` runs daily at 10:00 — the overlap is the
whole window, no clamping is visible, and the numbers are exactly what the
instruction asked for. When it is not current, the clamp is what stops a
38.2% TACoS being printed in place of 21.2%.

Registering Sales & Traffic as a future source is worth doing on its own
merits — per-ASIN sessions and unit-session-percentage are the missing organic
funnel — and the timeline layer is designed to accept it as another
`SourceSpec` without redesign.

## 5. Decisions — resolved

| # | Decision | Chosen |
|---|---|---|
| 1 | Unaligned Brand Analytics | **Per-metric rule.** Withhold only metrics that mix sources across non-overlapping periods; BA-only metrics stay valid inside their own week. |
| 2 | Revenue / TACoS | **Keep `DailySkuSnapshot`** (already per-SKU and order-date aligned); clamp TACoS and paid share to the overlap, withhold below a short floor. See §4a. |
| 3 | Location | **`sti/timeline.py`** — no STI concepts in its signature, promotable later. |

### Still unanswerable from this machine

`INFRA-001` (P1) records that `deploy/crontab.txt` cannot be installed on EC2
and the server's crontab is hand-maintained and drifted. So two questions carry
to production verification and are added to the section's queue:

| id | question | consequence |
|---|---|---|
| V11 | What is the real `DailySkuSnapshot` lag behind the ads tables in production? | decides whether clamping is invisible or routine |
| V12 | Is `ingest_brand_analytics` scheduled on the EC2 crontab, and how often? | decides whether stale market data is the normal case, and therefore how much of the board runs BA-only |
