# Search Intelligence — reporting-window review under a marketing objective

Status: **REVIEW + REVISED PLAN. Supersedes `plans/sti-timeline-alignment.md`.**
Author: Claude · 2026-08-07

The question asked: *is any part of the Search Intelligence design trying to
align data for accounting purposes instead of supporting marketing decisions?*

**Answer: yes, three parts. The largest is the timeline-alignment plan itself,
which I now withdraw.** Evidence and replacements below.

The module's objective, restated so every decision below can be tested against
it: are we improving organically · where are we losing rank · which terms are
growing · where should PPC money go · what should become a negative · where are
competitors gaining · which products have untapped demand. Nothing on this page
reconciles revenue.

---

## 1. Accounting-shaped: the alignment engine I proposed

`plans/sti-timeline-alignment.md` §2.2 proposed that any metric combining two
sources must have an overlapping period or be **withheld**. Applied here, that
rule would have suppressed the entire USA `organic_push` set — because the
newest Brand Analytics week (May) does not overlap the ads window (Jul–Aug).

That is an accounting instinct: *two numbers may not be compared unless they
describe the same period.* It is right for a P&L. It is wrong here, because
market share is a **structural** property, not a period-sensitive level. Holding
0.4% of a demand pool does not become unknowable because the reading is eight
weeks old, and the decision it drives — *build rank on this query* — does not
change. Withholding it would have deleted a valid marketing signal to satisfy a
reconciliation rule nobody on this page needs.

**Withdrawn.** No `ReportingWindow`, no `aligned` period, no alignment mode, no
clamping engine, no week-bucketed dual-aggregate spine. PPC stays on T-2, Brand
Analytics stays on its latest completed week, and each is stamped.

**What survives from that plan, because it is marketing logic rather than
accounting logic:**

| Kept | Why it is not an accounting rule |
|---|---|
| `can_trend` gate (≥3 BA weeks, recent) | A *trend* claim genuinely requires several comparable periods. Refusing to say "share is falling" from one week is honesty about the signal, not reconciliation. |
| Staleness labelling | Transparency — the one universal requirement. |
| Sunday–Saturday week boundary | A data-correctness fix. `BASearchQueryWeekly.week_start` help_text says "Monday"; the data is Sunday-start. Anything reaching for `isocalendar()` would be off by one day. Still worth fixing. |
| Measured availability per source | Now used only to *stamp* a dataset's as-of date, never to clamp a window. |

---

## 2. Accounting-shaped: TACoS on the KPI strip

TACoS is ad spend ÷ total revenue — a financial-efficiency ratio. Test it
against the module's questions: it does not tell you which term to bid on,
which to negate, where rank is slipping, or which product has untapped demand.
It answers *"what share of revenue went to advertising"*, which is a P&L
question, and Pulse already answers it on Daily P&L and the Management P&L.

Its presence here is also the **direct cause** of the misalignment crisis: it is
the metric that produced 38.2% against an aligned 21.2%, and chasing that number
is what led me to propose the clamping engine.

**Recommendation: remove TACoS from the Center.** It has a home; duplicating it
here invites reconciliation questions this page should not be asked to answer.

**Paid share stays**, because *"are we improving organically?"* is one of the
module's actual questions and paid share is the closest available read of it.
One correctness rule applies, and it is arithmetic rather than accounting: a
ratio whose numerator and denominator cover different spans is simply wrong. So
paid share is computed over the days both datasets cover and stamped with that
span. That is not forcing alignment across the report; it is one metric
declining to divide by a different period.

---

## 3. Accounting-shaped: margin and ASP tied to the report window

This is the one that actually broke, and it was self-inflicted.

`margin_rate()` and `average_selling_price()` currently read `DailySkuSnapshot`
**over the report window**. Consequences observed:

- 7-day report → zero revenue rows → margin silently falls back to the
  pessimistic constant, and every opportunity value on the page is computed from
  it with no warning that the window is the reason.
- 30-day report → margin computed from 14 of 27 days.

But CM rate and ASP are **structural properties that barely move**:

| | May | Jun | Jul |
|---|---|---|---|
| USA CM rate | 30.3% | 30.1% | 28.7% |
| USA ASP | $35.89 | $36.22 | $35.92 |
| UK CM rate | 38.6% | 36.7% | 34.7% |

Pricing an opportunity does not need this month's margin; it needs *the group's
margin*. Tying a stable rate to a volatile window bought nothing and introduced
a silent failure.

**Recommendation:** compute CM rate and ASP from a **stable trailing window of
settled per-SKU data** (propose 90 days, ending at the latest available date),
independent of the report window, and stamp both with their own as-of date.

This removes the `DailySkuSnapshot` lag problem almost entirely — a 12-day lag
is irrelevant to a 90-day trailing rate — with no clamping machinery at all. It
is also the answer to the earlier instruction about matching timeframes: the
right fix was never a different source, it was recognising that a *rate* does
not belong on the report's clock.

---

## 4. Not accounting-shaped — no change needed

| Element | Verdict |
|---|---|
| PPC on T-2 | Correct. Operational speed is the point. |
| Brand Analytics on its latest completed week | Correct, and now explicitly endorsed. |
| Opportunity score in contribution margin per month | Marketing, not accounting. It exists so a negative keyword and a product launch can be ranked against each other; it reconciles nothing. |
| Scoping by SKU/ASIN from the catalog | Unrelated to timelines; a correctness fix already delivered. |
| `paid_share_exceeds` honesty flag | Keep. Explains a real attribution property rather than hiding it. |
| Inventory as a gate | Correct — a point-in-time snapshot, no period to align. Needs an as-of stamp. |

---

## 5. The real gap: transparency, which is currently partial

The stated universal requirement is that every module shows the reporting period
and "Data As Of" for each major dataset. Audit of the current payload:

| Dataset | Period / as-of shown today |
|---|---|
| Ads (spend, clicks, sales) | ✅ window in the header |
| Brand Analytics | ✅ week + staleness banner |
| Margin / CM rate | ❌ **none** |
| ASP | ❌ **none** |
| Group revenue | ❌ **none** |
| Inventory cover | ❌ **none** (the per-SKU snapshot date is fetched, then dropped) |

Four of six datasets carry no stamp. **This — not alignment — is the work.**

---

## 5a. The cadence contract — formalised

Governing decisions: `MKT-D-012` (each metric uses the cadence its question
needs) and `MKT-D-013` (decision quality outranks reporting accuracy), recorded
in [decisions.md](../docs/marketing/decisions.md).

Every metric on the page declares three things, and the UI shows all three.
Nothing is aligned to anything else.

| Metric | Question it answers | Source | Period | Data As Of |
|---|---|---|---|---|
| Spend · clicks · ad sales · ACOS · CTR · CVR · CPC | how is PPC performing now | search-term daily | the selected window, ending T-2 | latest ads date |
| Wasted spend · negative candidates | what should we stop paying for | search-term daily | selected window | latest ads date |
| Scale / bid-up candidates | where should the next PPC dollar go | search-term daily | selected window | latest ads date |
| Organic share · market size · rank | where are we growing or losing share | Brand Analytics weekly | latest completed week(s) | that week's end |
| Share trend · momentum | is share moving | Brand Analytics weekly | ≥3 comparable weeks, else **withheld** | latest week's end |
| Product gap | which products have untapped demand | Brand Analytics weekly + catalog | latest completed week | that week's end |
| CM rate · ASP (opportunity valuation) | what is an opportunity worth | settled per-SKU daily | **90-day trailing**, independent of the report | latest settled date |
| Paid share | are we improving organically | ads + settled revenue | days both cover | both dates shown |
| Inventory readiness | can we act on this | inventory snapshot | point-in-time | snapshot date |
| Listing coverage | which words are missing | catalog titles | timeless | catalog read time |

Two guards survive, and neither is an alignment rule:

- **A ratio never divides across different spans.** Arithmetic, not accounting —
  which is why paid share uses the overlapping days and why TACoS, whose whole
  purpose was that ratio, leaves the page entirely.
- **Trend and causal claims stay gated** on having enough comparable periods.

## 6. Revised plan

Much smaller than the withdrawn one. No new abstraction layer.

1. **Data As Of strip.** One row under the header, one entry per dataset:
   ads window · Brand Analytics week · margin & ASP basis · revenue basis ·
   inventory as-of. Each states its own period. This is the module's contract
   with the reader.
2. **Decouple margin and ASP** from the report window to a 90-day trailing
   settled window; stamp them; drop the silent fallback in favour of an explicit
   "no settled revenue in the last 90 days" state.
3. **Remove TACoS**; keep paid share computed over the overlapping days, stamped.
4. **Per-insight period labels.** Every opportunity card already cites evidence;
   each cited figure gains the period it came from, so a card mixing a BA week
   with an ads window says so on its face.
5. **Fix the week-boundary help_text** (Sunday, not Monday).
6. **`StiReportRun.data_as_of`** — a small JSON of per-source as-of dates, so a
   stored run can still explain itself months later. Not a `ReportingWindow`;
   just the stamps. Migration required.
7. Docs: replace the alignment language in `search-intelligence.md` §Business
   rules with the marketing-objective statement, and close `MKT-STI-004`
   (staleness) in favour of a transparency rule rather than a defect.

**Explicitly not doing:** aligned windows, clamping, withholding cross-source
opportunities, week-bucketed spine aggregation, a shared Pulse timeline layer.

---

## 7. One caveat worth stating plainly

Under this philosophy a marketing insight may legitimately combine a recent ads
reading with an older market reading. That is a deliberate, stated trade-off in
favour of decision usefulness, and the mitigation is the label, not the maths.

The one thing labels cannot rescue is a **trend or causal claim** across
mismatched periods — "share fell because a competitor rose" is unsupportable if
the two observations are eight weeks apart. The `can_trend` gate covers this
today for share trends, and it must also constrain the future AI narrative
layer, which is the component most likely to invent that sentence.
