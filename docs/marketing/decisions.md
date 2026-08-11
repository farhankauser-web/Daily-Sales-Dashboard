# Marketing — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

Never edit a decision to change its meaning. Add a new one, mark the old
`superseded by`, and say what changed.

| ID | Decision | Date | Status |
|---|---|---|---|
| `MKT-D-001` | Unattributable spend stays unallocated, never spread | 2026-06-08 | accepted |
| `MKT-D-002` | The daily snapshot is authoritative for settled days | 2026-06-08 | accepted |
| `MKT-D-003` | Allocations lock at T+3; unlocked days are smoothed | 2026-06-08 | accepted |
| `MKT-D-004` | `AMZN.*` SKUs are excluded from allocation | 2026-06-08 | accepted |
| `MKT-D-005` | Hourly figures accumulate, never replace | 2026-07-28 | accepted |
| `MKT-D-006` | Attribution windows differ by ad product and are not reconciled | 2026-06-08 | accepted |
| `MKT-D-007` | An incomplete day is suppressed, never estimated | 2026-05-13 | accepted |
| `MKT-D-008` | An uploaded hour replaces the stream; the stream accumulates | 2026-06-14 | accepted |
| `MKT-D-009` | Profit is always shown, always qualified by coverage | 2026-05-13 | accepted |
| `MKT-D-010` | Search terms are signalled on fixed shared thresholds | 2026-05-13 | accepted |
| `MKT-D-011` | Breakdowns inherit the campaign's margin rate | 2026-05-13 | accepted |
| `MKT-D-012` | Each metric uses the cadence its business question needs | 2026-08-07 | accepted |
| `MKT-D-013` | Decision quality outranks reporting accuracy | 2026-08-07 | accepted |
| `MKT-D-014` | The opportunity is the product; information must earn its place | 2026-08-07 | accepted |
| `MKT-D-015` | The report runs on Amazon reporting periods, not rolling windows | 2026-08-07 | accepted |

---

## `MKT-D-001` · Unattributable spend stays unallocated, never spread

| | |
|---|---|
| **Date** | 2026-06-08 · **Status** accepted |

**Context** — Some campaigns cannot be attributed: no Sponsored Products data,
no prior weights, and a name that matches no product group. Their spend is real
and has to go somewhere, or be seen to go nowhere.

**Decision** — It goes **nowhere**. The spend is reported as its own
"Unallocated PPC" figure alongside the allocated groups, and is never
distributed across SKUs.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Spread it across all SKUs by revenue | Puts cost on SKUs the campaign demonstrably did not advertise, and the error is invisible — every SKU is slightly wrong rather than one figure being obviously missing |
| Drop it silently | Per-SKU ad cost would sum to less than real spend with nothing saying why, and TACoS would read better than reality |

**Reason** — A visible gap is worth more than an invisible error. An unallocated
total is a prompt to fix the campaign naming; a smeared cost is a wrong number
nobody can find.

**Consequences** — Σ per-SKU ad cost is **less than** total ad spend by the
unallocated amount, deliberately. Anything reconciling the two must read the
unallocated figure as well. `unmapped_ppc_campaigns` exists to drive it toward
zero.

**Affected documents** — [sku-allocation.md](sku-allocation.md)

---

## `MKT-D-002` · The daily snapshot is authoritative for settled days

| | |
|---|---|
| **Date** | 2026-06-08 · **Status** accepted |

**Context** — Campaign spend for a day arrives from up to three sources: the Ads
API daily report, the AMS hourly stream, and occasionally a Seller Central
hourly file uploaded by hand. They overlap and disagree.

**Decision** — A precedence, not a sum:

1. A **manual upload** for a settled day is authoritative alone; everything else
   for that date is ignored.
2. Otherwise, for a **settled day**, the Ads API daily snapshot wins wherever it
   has the campaign; the stream fills only campaigns the snapshot missed.
3. For **today**, whichever of stream and snapshot is larger wins, per campaign.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Sum the sources | Double-counts. Manual rows are keyed by slugified campaign name and daily rows by Amazon's numeric id, so nothing dedupes them and every row survives both filters |
| Always prefer the stream | Late-arriving revisions and new attributions push the total above what Amazon's own UI reports for that day, so our figure could never be reconciled against Amazon's |
| Always prefer the daily snapshot | It does not exist yet for today, which is exactly when the stream is valuable |

**Reason** — The daily report is the number Amazon will stand behind for a
settled day. The stream is the early signal for a day that has not settled.

**Consequences** — Today's figure can move down as well as up when the daily
snapshot arrives and replaces a larger streamed value. A manual upload is a
blunt override: it silences both other sources for that date, so a partial file
under-reports.

**Affected documents** — [sku-allocation.md](sku-allocation.md), [ams-stream.md](ams-stream.md), [ads-api.md](ads-api.md)

---

## `MKT-D-003` · Allocations lock at T+3; unlocked days are smoothed

| | |
|---|---|
| **Date** | 2026-06-08 · **Status** accepted |

**Context** — Attribution keeps arriving for days after a sale, and the
allocation is recomputed hourly. Without a cut-off, historical per-SKU ad cost
would never stop moving, and reports run a week apart would disagree.

**Decision** — A day is **provisional** while it is today, **settling** for two
days after, and **locked** at T+3 — after which it is not recomputed. Unlocked
days blend 70% of the newly computed figure with 30% of the previous run's.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Never lock | No historical figure is ever final; a P&L restates itself silently |
| Lock at T+1 | Amazon's attribution is still materially incomplete a day out, so we would freeze a wrong number |
| Lock without smoothing | Each hourly recompute swings published per-SKU cost, and the last run before the lock wins arbitrarily |

**Reason** — Three days is where late attribution stops being material. The
smoothing exists so the value at lock is not whichever recompute happened to run
last.

**Consequences** — A locked day carries a figure that is close to, but not
exactly, what a fresh computation would produce — the accepted cost of
stability. Re-opening a locked period is deliberate and explicit.

**Affected documents** — [sku-allocation.md](sku-allocation.md)

---

## `MKT-D-004` · `AMZN.*` SKUs are excluded from allocation

| | |
|---|---|
| **Date** | 2026-06-08 · **Status** accepted |

**Context** — Amazon Renewed and Warehouse listings appear in the catalogue as
SKUs prefixed `AMZN.`, sharing an ASIN with the SKU we actually sell. We run no
advertising against them.

**Decision** — They are **excluded from the allocation entirely**. Their share
of an ASIN's spend redistributes to the normal sibling SKUs. An ASIN whose only
SKUs are excluded drops out of the weight basis altogether.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Allocate to them like any SKU | Charges advertising cost to a listing we do not advertise, understating the real SKU's cost and inventing a loss on the Renewed one |
| Allocate, then subtract later | Every downstream consumer would need to know to exclude them; one rule at the source is cheaper than a rule in five reports |

**Reason** — Business call: we do not pay to advertise those listings, so no ad
cost belongs to them.

**Consequences** — The exclusion is a prefix rule, so a differently-named Amazon
listing would not be caught. The redistribution is automatic — nothing needs to
know it happened — which also means a mistaken exclusion would be invisible.

**Affected documents** — [sku-allocation.md](sku-allocation.md)

---

## `MKT-D-005` · Hourly figures accumulate, never replace

| | |
|---|---|
| **Date** | 2026-07-28 · **Status** accepted |

**Context** — Amazon delivers an hour's metrics as a stream of events and keeps
sending late revisions for hours already stored. A single ingest run sees only
the events in its own batch.

**Decision** — The hourly figure is **added to**, never overwritten. Safety comes
from consuming each S3 object exactly once, recorded in a ledger, plus a
single-instance lock so two runs cannot process the same batch concurrently.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Replace the stored value with the run's total | A run carrying three late events overwrites a complete day. This is not hypothetical: 2026-07-28 collapsed from $7,618 to $289 exactly this way |
| Re-aggregate the whole day from raw events each run | Requires keeping every raw event indefinitely, and re-reading them all on a job that runs every minute |

**Reason** — The ledger already guarantees exactly-once consumption, which is
precisely the condition that makes accumulation correct. Given that, adding is
both cheaper and safer than replacing.

**Consequences** — Correctness now depends on two things holding together: the
ledger and the lock. Losing either double-counts silently, with no error — which
is why the lock is taken before any listing and the ledger write shares the
upsert's transaction. Re-ingesting an object deliberately requires deleting its
ledger row first.

**Affected documents** — [ams-stream.md](ams-stream.md)

---

## `MKT-D-006` · Attribution windows differ by ad product and are not reconciled

| | |
|---|---|
| **Date** | 2026-06-08 · **Status** accepted |

**Context** — Sponsored Products reports conversions on a one-day attribution
window; Brands and Display report only fourteen-day. The same stored column
therefore holds figures measured over different periods.

**Decision** — Store each as Amazon reports it, in the same columns, and **do
not attempt to normalise them**. Sponsored Products settles within a day; Brands
and Display keep revising upward for weeks, and that drift is accepted.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Normalise everything to a common window | Amazon does not provide the data to do it; any conversion factor would be invented and would then be compounded by every downstream calculation |
| Hold SB/SD figures back until they stabilise | Weeks of blank intra-day reporting for two ad products, to avoid a number that is directionally right immediately |
| Separate columns per window | Triples the schema and pushes the same reconciliation problem onto every reader |

**Reason** — A figure measured over a stated window is honest; a figure
converted between windows is a guess wearing a precise number.

**Consequences** — Brands and Display sales for a recent period are understated
and rise for weeks afterwards. Anyone comparing ad products on conversion needs
to know the windows differ — the column name does not say so, which is a trap
worth remembering.

**Affected documents** — [ams-stream.md](ams-stream.md), [ads-api.md](ads-api.md)

---

## `MKT-D-007` · An incomplete day is suppressed, never estimated

| | |
|---|---|
| **Date** | 2026-05-13 · **Status** accepted |

**Context** — Advertising data for a day arrives from several reports, each of
which can be late, empty or failed. A day is often partly filled. Something has
to decide what the dashboard does with it.

**Decision** — A day whose **core** sources are not resolved is **not shown at
all**. A day missing only Sponsored Brands or Display data is shown **without
those columns**. Nothing is interpolated, carried forward or estimated. "Amazon
returned zero rows" is recorded distinctly from "we did not get an answer",
because only the first is a known zero.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Show what arrived | A day showing Sponsored Products only looks like a cheap day, not an incomplete one, and nothing on the page says which |
| Carry the previous day forward | Invents trading history, and the invention persists in every average computed over it |
| Estimate from the surrounding days | Same objection, with more arithmetic to make it look deliberate |

**Reason** — A missing day is recoverable — the re-pull will fill it. A wrong
day is not, because nobody knows to go back for it.

**Consequences** — The dashboard has holes rather than errors, which is the
right trade and an unfamiliar one: a gap in a chart reads as a bug to anyone who
does not know this rule. It also means a permanently failed day is invisible
rather than obviously wrong (`MKT-ADS-001`).

**Affected documents** — [ads-api.md](ads-api.md), [ams-stream.md](ams-stream.md)

---

## `MKT-D-008` · An uploaded hour replaces the stream; the stream accumulates

| | |
|---|---|
| **Date** | 2026-06-14 · **Status** accepted |

**Context** — Two writers fill the hourly table. The stream delivers an hour as
a series of events plus late revisions, so it **adds** (`MKT-D-005`). A manual
upload delivers a whole hour, complete, from Amazon's own console.

**Decision** — The upload **replaces** the hours it covers and marks them as
manually sourced; the stream continues to accumulate. Where an hour has been
uploaded, the upload wins.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Make the upload accumulate too | It carries a complete hour, so adding it to whatever the stream already had doubles that hour |
| Make the stream replace too | Its events are partial by nature; this is exactly the failure `MKT-D-005` records |
| Refuse to upload an hour the stream already has | Removes the escape hatch precisely when it is needed — a stream hour known to be wrong |

**Reason** — The two sources have genuinely different shapes: one is a stream of
increments, the other a snapshot of a finished hour. One write strategy cannot
be correct for both.

**Consequences** — Two write paths into one table with opposite semantics, which
is a trap for anyone adding a third. The `source` column records which wrote each
row, and any future writer must declare its shape before it is added. An upload
covering an hour the stream is still receiving events for will be topped up
again by those events — so uploads are for settled periods.

**Affected documents** — [hourly-upload.md](hourly-upload.md), [ams-stream.md](ams-stream.md)

---

## `MKT-D-009` · Profit is always shown, always qualified by coverage

| | |
|---|---|
| **Date** | 2026-05-13 · **Status** accepted |

**Context** — Campaign profit needs each sold SKU's cost of goods and fees. Some
sales attribute to SKUs we hold no costs for — a new product, a SKU absent from
the cost upload — so the margin for part of a campaign has to be estimated from
the referral percentage alone.

**Decision** — Show the profit **always**, and publish an **attribution
coverage** figure beside it: the share of the campaign's sales backed by real
per-SKU costs. Never suppress the profit for low coverage, and never silently
substitute an estimate.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Suppress profit below a coverage threshold | The most interesting campaigns are often the newest, which have the worst coverage — the rule would blank exactly the rows someone came to read |
| Show the estimate without saying so | Presents a guess with the authority of a measurement, which is the failure the coverage figure exists to prevent |
| Show only the covered portion | A partial profit figure with no denominator is harder to reason about than a full one with a caveat |

**Reason** — An estimate that says it is an estimate is useful. The alternative
to showing it is not showing something better; it is showing nothing.

**Consequences** — Coverage has to be read to interpret profit, and nothing yet
forces that — `MKT-CAMP-001`. It also means improving cost coverage improves the
reported numbers without any campaign changing, which must be remembered when
comparing periods.

**Affected documents** — [campaigns.md](campaigns.md)

---

## `MKT-D-010` · Search terms are signalled on fixed shared thresholds

| | |
|---|---|
| **Date** | 2026-05-13 · **Status** accepted |

**Context** — A day's search-term table can exceed 100,000 rows. Sorting by
spend surfaces the biggest terms, which are usually the ones already being
managed. The terms worth acting on are defined by a *pattern*, not a size.

**Decision** — Five signals, computed on **fixed thresholds applied identically
to every campaign**: high spend with no sales, high click-through with low
conversion, losing money, scaling opportunity, high profit.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Thresholds relative to each campaign's own distribution | Every campaign then has outliers by construction, including healthy ones, and the counts stop being comparable between campaigns |
| Rank terms instead of signalling them | A ranking always has a top ten, whether or not anything is wrong |
| Let the user set thresholds per view | Nobody tunes five numbers before reading a page; the defaults would be the answer anyway |

**Reason** — A shared line makes the counts comparable — "this campaign has
fourteen wasteful terms and that one has two" is a sentence worth saying, and it
is only true if the line is the same.

**Consequences** — The thresholds are money amounts applied across marketplaces
and currencies, which is wrong the moment a second marketplace is managed here
(`MKT-TERM-001`). They also encode a view about what "high spend" means that
will drift as the account grows, so they are worth revisiting annually rather
than never.

**Affected documents** — [search-terms.md](search-terms.md), [campaigns.md](campaigns.md)

---

## `MKT-D-011` · Breakdowns inherit the campaign's margin rate

| | |
|---|---|
| **Date** | 2026-05-13 · **Status** accepted |

**Context** — Campaign profit is real: it is built from the cost of goods and
fees of the SKUs Amazon says the campaign sold. Beneath a campaign sit search
terms and placements, and Amazon attributes **no** cost of goods at those
levels — only spend, orders and sales.

**Decision** — A search term's or placement's profit is **its sales at the
campaign's contribution-margin rate, less its spend**. It is a proxy, labelled
as one wherever it appears, and the rule lives in
[campaigns.md](campaigns.md) rather than being restated in each breakdown.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Show no profit below campaign level | Spend and sales alone cannot separate a term that is profitable from one that is not, which is the entire purpose of the page |
| Re-derive margin per term from the SKUs it sold | Amazon does not report which SKU a search term sold, so there is nothing to derive it from |
| Use a single account-wide margin rate | Cheaper and worse: a term in a high-margin campaign and one in a low-margin campaign would read identically |

**Reason** — The campaign is the finest level at which a real margin exists, so
it is the finest level whose rate can honestly be inherited. Inheriting it is an
approximation; inventing one would not be.

**Consequences** — A term selling an unusually high- or low-margin SKU within a
mixed campaign is mis-stated in proportion to how mixed the campaign is. The
proxy is only as good as campaign coverage (`MKT-D-009`), so a campaign resting
on fallback margins passes that weakness down to every term and placement
beneath it.

**Affected documents** — [campaigns.md](campaigns.md), [search-terms.md](search-terms.md), [placements.md](placements.md)

---

## `MKT-D-012` · Each metric uses the cadence its business question needs

| | |
|---|---|
| **Date** | 2026-08-07 · **Status** accepted |

**Context** — The Search Intelligence Center reads sources that refresh on
different clocks: advertising daily at T-2, Brand Analytics weekly, settled
per-SKU revenue daily but behind, inventory as a point-in-time snapshot. An
early design treated this as a defect and proposed forcing every metric onto a
common period, withholding any figure whose sources did not overlap. Applied to
real data that rule suppressed the entire organic-opportunity set, because the
newest Brand Analytics week did not overlap the advertising window.

**Decision** — **Every metric uses the reporting period that best represents the
question it answers, not the period other metrics happen to use.** Organic share
is strategic and uses the latest completed Brand Analytics period. PPC
performance is operational and uses T-2. Opportunity valuation uses a long-run
settled contribution margin and average selling price rather than the report
window. Inventory readiness uses the latest snapshot. Product-gap analysis uses
the latest demand data. The single obligation is **transparency**: every
significant metric states its data source, its reporting period, and its
"Data As Of" date.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Force every metric onto one aligned window | Suppresses valid strategic signal to satisfy a reconciliation requirement this module does not have; measured to remove 23% of opportunities on real data |
| Clamp cross-source metrics to the overlapping days | Correct for a ratio, wrong as a general rule — it made a stable margin rate depend on a volatile window and produced a silent fallback on short reports |
| Show everything on one window and say nothing | The failure this replaced: a 38.2% TACoS printed where the comparable figure was 21.2% |

**Reason** — This is a decision-support module, not a reporting one. It is not
reconciling revenue; it is choosing where to bid, what to negate, where to build
rank and what to launch. Market share is a structural property that does not
become unknowable because the reading is some weeks old, and the decision it
drives does not change. Accounting alignment belongs where accounting questions
are asked — Financials and Reporting each set their own cadence for their own
objective, and no shared timeline layer spans them.

**Consequences** — An insight may legitimately combine a recent advertising
reading with an older market reading, and the label carries the caveat rather
than the arithmetic. Two guards remain, and they are not alignment rules: a
ratio never divides across different spans, and trend or causal claims stay
gated on having enough comparable periods (`MKT-D-013`). The cost is that a
reader who ignores the period labels can misread a card; the mitigation is that
every card carries them.

**Affected documents** — [search-intelligence.md](search-intelligence.md)

---

## `MKT-D-013` · Decision quality outranks reporting accuracy

| | |
|---|---|
| **Date** | 2026-08-07 · **Status** accepted |

**Context** — Where a dataset is older than its neighbours, a module can either
suppress what it supports or show it with its age stated. The first is safer for
a report; the second is more useful for a decision. This module needed the
question settled once rather than re-argued per feature.

**Decision** — **Never optimise for reporting accuracy when it reduces decision
quality.** If older Brand Analytics data supports a better strategic
recommendation, show the recommendation with its period stated rather than
suppress it because another dataset is newer. Every recommendation must still
carry its evidence and the reporting period behind it.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Suppress any recommendation whose data is not current | Silence is not neutrality — it hides a real opportunity and teaches nobody why |
| Show stale-backed recommendations unlabelled | Removes the reader's ability to weigh them, which is the only thing that makes showing them defensible |

**Reason** — A recommendation is a prompt to think, not a published figure. The
cost of a slightly dated market read is a decision made on eight-week-old share
data; the cost of suppression is the decision never being considered. For this
module the first is clearly cheaper.

**Consequences** — The bar moves from *is this current* to *is this evidenced
and labelled*. One class of statement is still forbidden regardless of labelling:
a **trend or causal claim** across periods that cannot support it — "share fell
because a competitor rose" is unsupportable when the two observations are weeks
apart. That gate binds the future AI narrative layer, which is the component
most likely to produce such a sentence unprompted.

**Affected documents** — [search-intelligence.md](search-intelligence.md)

---

## `MKT-D-014` · The opportunity is the product; information must earn its place

| | |
|---|---|
| **Date** | 2026-08-07 · **Status** accepted |

**Context** — A dashboard accumulates panels. Each one is individually
defensible and the sum is a data-exploration tool that leaves the reader to work
out what to do — which is the job this module exists to have already done.

**Decision** — Four rules govern what may appear.

1. **Actionability over information.** Every screen, widget, chart, KPI and
   recommendation answers *"what decision should the user make after seeing
   this?"* A section that reports without influencing a decision must become
   supporting evidence for an opportunity, move to a drill-down, or be removed.
2. **Progressive disclosure.** The first screen is executive: highest-value
   insights only. Detail lives inside the opportunity that needs it. No table or
   chart on the first screen unless it directly supports a recommendation.
3. **Evidence before AI.** Every recommendation traces to deterministic data.
   AI explains, prioritises and summarises; it never invents an opportunity and
   is never the source of truth. Business rules and data stay authoritative.
4. **New data strengthens opportunities; it does not add sections.** Business
   Reports, Voice of Customer, pricing, reviews and Marketing Stream must enrich
   existing evidence, raise confidence, or create new opportunity *types* — not
   fragment the page into isolated dashboards.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Keep informational panels because they are cheap and someone may want them | This is how a decision-support tool decays into a reporting tool — the failure this module already corrected once |
| Give each new dataset its own section | Fragments the experience and makes the opportunity one tab among many, rather than the product |
| Let AI generate opportunities directly | An opportunity that cannot be traced to a query is not auditable, and the outcome loop could never measure whether it was right |

**Reason** — The unit of this product is the opportunity, not the metric. Every
element either sharpens one, prices one, or gets out of the way. That constraint
is what keeps the first screen readable in under a minute as the number of
sources grows.

**Consequences** — Genuinely interesting analysis will be cut from the first
screen, and some of it will be missed. The mitigation is that it stays reachable
as evidence rather than being deleted. Applied to the current build: the
spend-by-intent breakdown becomes evidence behind waste opportunities rather
than a standing table, and the KPI strip narrows to the figures that frame a
decision.

**Affected documents** — [search-intelligence.md](search-intelligence.md)

---

## `MKT-D-015` · The report runs on Amazon reporting periods, not rolling windows

| | |
|---|---|
| **Date** | 2026-08-07 · **Status** accepted |

**Context** — The Center originally offered rolling windows: last 7, 14 or 30
days ending at T-2. Two problems followed. A rolling window resolves differently
every day, so two runs never cover the same days and the run-diff had to
withhold every comparison — the learning loop could never close. And an
arbitrary advertising range has no matching Brand Analytics data, because Brand
Analytics publishes only on Amazon's week, so the market half of any report was
paired with whatever week happened to be newest.

**Decision** — The report runs on **Amazon reporting periods**: Sunday-start
weeks under Amazon's own numbering, or calendar months. The selector offers
periods that have advertising data, each labelled with its day coverage and
whether Brand Analytics exists for it. Brand Analytics is then read **for that
period only** — never "the latest available".

Week numbering is Amazon's, not ISO: week 1 is the Sunday-start week containing
1 January, so Week 31 of 2026 is 2026-07-26 → 2026-08-01. `isocalendar()` calls
the same week 30 and is wrong by one everywhere.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Keep rolling windows | The diff can never compare two runs, so outcome measurement is impossible — the point of storing runs at all |
| Offer only weeks Brand Analytics has published | Tidier, and it would leave UK, UAE and KSA with no periods at all — Brand Analytics covers USA alone today — while freezing USA to a single stale week |
| Let the user pick any date range and fetch whatever market data is nearest | The behaviour being removed: it paired a May market read with an August ad window and left a banner to apologise |

**Reason** — A named period is fixed forever, which is what makes *"we acted in
Week 30 — did Week 31 improve?"* answerable. It also matches Amazon's own grid,
so advertising and market data describe the same days by construction rather
than by alignment logic.

**Consequences** — The Center becomes a **review** surface rather than a live
monitor; the operational "how are we doing right now" view belongs to Reporting.
A period with no Brand Analytics still reports its advertising, with the market
sections marked unavailable — decision quality over reporting purity
(`MKT-D-013`). Partial periods are offered but labelled, because a fixed period
exposes data gaps that a rolling window would have averaged away, and an
unlabelled gap reads as a business collapse. The report's market usefulness now
rests entirely on Brand Analytics ingestion, which raises `MKT-STI-004` from a
reporting nuisance to the thing that decides whether this is a market tool or a
PPC tool.

**Affected documents** — [search-intelligence.md](search-intelligence.md)
