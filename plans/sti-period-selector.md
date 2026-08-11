# Search Intelligence — Amazon periods and the action→outcome loop

Status: **PLAN — for review before coding.**
Author: Claude · 2026-08-07

Replace relative windows (last 7 / 14 / 30 days) with **named Amazon reporting
periods** — Weekly (Sun–Sat) and Monthly — matching the Brand Analytics
selector, and applied to PPC as well.

The reason is not tidier date handling. It is that a named period is the unit
in which *"did what we did last week improve anything?"* can be answered at all.

All figures below: local dev snapshot, provisional until re-run on production.

---

## 1. Why this unlocks outcome measurement

A relative window resolves differently every day, so two runs a week apart never
cover the same days. The run-diff already refuses to compare them — the period
guard fires and withholds, by design (`MKT-D-012`). That is correct and it is
also a dead end: **the learning loop can never close while the window moves.**

A named period is fixed forever. Week 31 is Week 31 in October as it was in
August. That makes three things possible that are impossible today:

1. **Week-over-week comparison** as a first-class feature, not a withheld one.
2. **Action → outcome**: an opportunity marked done during Week 30 can be
   measured against Week 31 on its own subject.
3. **A stable series** per opportunity, because snapshots become period-anchored
   rather than run-anchored.

## 2. What "did our ranking improve?" can actually be measured with

Amazon does not report our organic *rank*. What Brand Analytics gives per query
per week is our slice of the funnel, which is the better measure anyway:

| Field | Meaning |
|---|---|
| `asin_purchase_count / purchases_total` | our share of purchases on that query |
| `brand_click_share`, `brand_purchase_share` | Amazon's own share figures |
| `search_query_score` | the query's popularity rank — the market's, not ours |

So the measurable outcome is **share movement on a query, week over week** —
*"we captured more of that demand"* rather than *"we moved from position 9 to
6"*. That is the honest form of the question, and it is the form that matters.

**Two speeds, and they must not be promised as one:**

| Side | Data depth today | Outcome measurement |
|---|---|---|
| PPC (spend, orders, ACOS, CVR per term) | daily, ~11 weeks | **works now** — verified a term across two weeks: $2,834/233 orders → $4,463/318 orders |
| Organic share / "ranking" | weekly, **1 week** | mechanism buildable, **cannot be demonstrated** until Brand Analytics has ≥2 weeks (`MKT-STI-004`) |

The organic half is the half you care most about, and it is gated on a sync that
may not be scheduled in production. The plan builds it and labels it honestly
rather than showing an empty trend as if it were a flat one.

## 3. Period model

**Weekly — Amazon's numbering, not ISO.** Verified against your screenshot:
Week 31 = 2026-07-26 → 2026-08-01. The rule that reproduces it is *Sunday-start,
week 1 contains Jan 1* (week 1 of 2026 begins 2025-12-28). `isocalendar()` calls
that same week 30 and would be wrong by one everywhere. This is the same trap as
the `week_start` help_text that claimed Monday.

**Monthly.** Calendar month for PPC. Brand Analytics has no monthly report in
Pulse — all five BA models are weekly — so a month's market data is the weeks
belonging to it, and the rule must be stated on the page. Proposed: **a week
belongs to the month its `week_end` falls in**, so every week counts once and
none is split.

**Quarterly: deferred.** It compounds the week-to-month rule and needs 13 weeks
of BA depth that does not exist. Not worth building against one week.

**Completeness is part of the option, not a footnote.** Local ads data:

```
2026-07-19 → 2026-07-25   6/7 days   $38,720   partial
2026-07-26 → 2026-08-01   7/7 days   $52,351   complete
2026-08-02 → 2026-08-08   3/7 days   $26,038   partial
```

Five of eleven weeks are partial and two are missing outright. A rolling window
averages over gaps; a fixed period exposes them — which is better only if it is
labelled. Every option in the selector carries its day coverage, incomplete
periods are marked, and the current in-progress period is not offered as though
it were finished.

## 4. Changes required

| Area | Change |
|---|---|
| `sti/periods.py` *(new)* | Amazon week numbering, month periods, availability + completeness per period. All date logic, one module. |
| `config.py` | `DATE_PRESETS` retired; period types and the week-1 rule move here |
| `runner.resolve_dates` | replaced by a period resolver |
| `StiReportRun` | add `period_type` + `period_key` (`2026-W31`, `2026-07`) + migration — so runs of the same period can be found and compared |
| `StiOpportunity` | add `acted_period_key` — which period an action was taken in, set when status → done |
| `StiOpportunitySnapshot` | becomes period-anchored; add `period_key` |
| `_diff` | compares **the same named period across runs**, and gains a second mode: **this period vs the previous period** |
| Outcome view *(new)* | per acted-on opportunity: its subject's metrics in the action period vs the following period, PPC-side now, organic-side when BA depth allows |
| UI | Reporting Range + period selector with coverage; "vs previous period" on the diff |

## 5. Conflicts checked

| Area | Verdict |
|---|---|
| Valuation basis (CM%, ASP) | **No conflict** — deliberately a 90-day trailing window, off the report period (`MKT-D-012`) |
| Paid share | No conflict; it will resolve on older periods and withhold on the newest, which is self-explaining |
| Monthly normalisation of scores | Cleaner — a week is exactly 7 days, a month exactly one; no more `30.44 / window` drift |
| Opportunity stable key | **No conflict** — carries no date, so identity survives |
| Run diff period guard | **Strengthened** — comparisons become the normal case instead of the withheld one |
| `MKT-D-012` (cadence per question) | **Consistent** — this does not force alignment; it lets the user *choose* a period both sources publish on |
| Financials / Reporting | Untouched; `sti/` remains read-only outside its own tables |

One real trade-off, already named and accepted: this makes the Center a
**review** surface rather than a live monitor. Reporting owns the operational
"how are we doing right now" view.

## 6. Milestones

1. `periods.py` + selector, weekly and monthly, with completeness. Verify the
   week numbering reproduces Amazon's labels exactly.
2. Model fields + migration; runs and snapshots become period-anchored.
3. Diff gains "this period vs previous period".
4. Outcome view: PPC-side measurement, with the organic half built and labelled
   as awaiting Brand Analytics depth.
5. Docs + a decision record for the period model.

## 7. Open questions

1. **Default period** — latest *complete* week (recommended), or latest month?
2. **Keep a custom date range** as an escape hatch, or periods only?
3. **Outcome measurement window** — measure the period immediately after the
   action (fast, noisy) or the two following periods (slower, steadier)?
   Recommended: the next period for PPC, two for organic, since rank moves slower.
