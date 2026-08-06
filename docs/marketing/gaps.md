# Marketing — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**Where the evidence comes from.** Counts here are measured against the **local
SQLite database** unless stated otherwise. Findings drawn from *code* transfer to
production unchanged; findings drawn from *data* are provisional and must be
re-measured on production Postgres before anyone acts on them, and say so. See
[templates/README.md](../templates/README.md) for the precedence.

**No scheduler runs on the development machine — by design.** Production runs
all 33 jobs in `deploy/crontab.txt` continuously; the laptop runs none and is
not always on. Every freshness difference between Marketing tables reflects
which command someone last ran by hand. **Staleness is never evidence of a
defect here**, and neither is an empty or partly filled table.

**Absence of data is not a defect.** Every gap carries a **Classification** —
missing implementation, bug, configuration, missing operational process, or
legacy data — a root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|
| `MKT-ALLOC-002` | The allocator reads a superseded, campaign-blind copy of the advertised-product data | P2 | missing implementation | open |
| `MKT-ALLOC-001` | The campaign → product-group map is hardcoded in a view module | P2 | missing implementation | open |
| `MKT-ALLOC-003` | Amazon's own SKU attribution is discarded and re-derived | P3 | missing implementation | open |
| `MKT-ALLOC-004` | The smoothing docstring describes a blend the code does not perform | P3 | bug — stale docs | open |
| `MKT-AMS-001` | A dataset that stops delivering is silent | P2 | missing implementation | open |
| `MKT-ADS-001` | A report day that never resolves is invisible | P2 | missing implementation | open |
| `MKT-AMS-002` | The legacy dataset map covers SP only outside North America | P3 | missing implementation | open |
| `MKT-CAMP-001` | Nothing flags a campaign whose profit rests mostly on fallback margins | P2 | missing implementation | open |
| `MKT-UPL-001` | An unmatched campaign name on upload is logged, never reported | P3 | missing implementation | open |
| `MKT-TERM-001` | Signal thresholds are fixed in code and identical across marketplaces | P3 | missing implementation | open |

---

## `MKT-ALLOC-002` · The allocator reads a superseded, campaign-blind copy of the advertised-product data

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | missing implementation — a pipeline was replaced and one consumer was not repointed |
| **Code alone fixes it** | yes — the data is already ingested daily |
| **Dependencies** | none. Resolves `MKT-ALLOC-003` in the same change |

**Current behaviour** — Advertised-product spend is stored **twice**, by two
generations of pipeline:

| | `PPCProductSnapshot` (older) | `AdsAdvertisedProductDailySnapshot` (current) |
|---|---|---|
| campaign grain | **none** | `campaign_id` |
| SKU | stored, unread | stored |
| ad products | Sponsored Products only | SP, SB and SD |
| written by | `backfill_ppc` | `ingest_ads_detail_reports` |

The allocator reads the **older** one. Because it has no campaign column, Pass 1
computes ASIN weights from the day's spend summed across every Sponsored
Products campaign, then applies that same distribution to each campaign
individually — so a campaign advertising one ASIN has its spend spread across
every ASIN advertised that day.

**Expected behaviour** — A campaign's spend is distributed over the ASINs *that
campaign* advertised, in that campaign's own proportions, using the table that
already records exactly that.

**Root cause** — Not a missing capability. The Phase 1 detail-report pipeline
landed with a superset table — campaign grain, SKU, all three ad products — and
the allocator was never repointed from the Phase 0 table it was built against.
Both are still written, so nothing broke and nothing signalled the duplication.

**Evidence** — source: **code** for the behaviour, **local data** for the scale.
```python
# ppc_allocator.py, _pass1_sp — no campaign filter, because the table has no such column
qs = (PPCProductSnapshot.objects
      .filter(marketplace=mp, date=d, campaign_type='sp')
      .values('asin').annotate(spend=Sum('spend')))

# ads_detail_reports.py, sp_advertised_product — campaignId IS requested
columns=['campaignId', 'adGroupId', 'advertisedAsin', 'advertisedSku', ...]
```
On the development database, *provisional*: the current table holds 40,483 rows
across 443 campaigns and $468,914 of spend, current to 2026-08-04; the one the
allocator reads holds 4,624 rows, no campaigns, $179,442, stopping 2026-07-26.

**Business impact** — Per-SKU ad cost is misattributed between SKUs whenever
Sponsored Products campaigns target different ASINs, which is the normal case.
Campaign totals stay correct because reconciliation scales each campaign to its
true spend, so the error is invisible in every campaign-level view and distorts
only the per-SKU split — which is what TACoS and contribution margin are built
from. The engine also records `sp_advertised_product` at 1.00 confidence for
these rows, so the audit trail overstates their quality.

**Technical impact** — Two tables holding the same fact, one a strict superset
of the other, with the primary consumer on the weaker one. See `ARCH-009`.

**Recommendation** — Repoint Pass 1 at `AdsAdvertisedProductDailySnapshot`,
filtering by campaign. The data is already ingested daily and reaches back to
2026-05-13, so no re-request or backfill from Amazon is needed. Lower the
confidence for any day that still falls back to the older table. Then resolve
`ARCH-009` — one of the two tables should stop being written.

**Related documents** — [sku-allocation.md](sku-allocation.md), [ads-api.md](ads-api.md)

---

## `MKT-ALLOC-001` · The campaign → product-group map is hardcoded in a view module

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — Sponsored Brands and Display campaigns, and any SP
campaign without product data, are attributed by matching a **campaign-name
prefix** to a product group. That mapping is a Python dictionary in
`apps/amazon_api/views.py`. Adding a product group, or a new campaign naming
convention, requires editing source and deploying.

**Expected behaviour** — The mapping is business configuration, editable by the
people who name the campaigns.

**Root cause** — It began as a small lookup for a handful of prefixes and grew
into the routing table for every non-SP campaign. No configuration surface was
ever built, and none was needed while the list was short.

**Evidence** — source: **code**. `_CAMP_PREFIX_GROUP` in
`apps/amazon_api/views.py`. The `unmapped_ppc_campaigns` command exists solely
to report misses and prints ready-to-paste Python for the dictionary — a
diagnostic that assumes a code edit is the remedy.

Four campaigns are unmapped over the last 30 days, totalling $48.02 — *dev
snapshot; provisional.*

**Business impact** — An unmapped campaign's spend is unallocated until someone
edits Python and deploys. Small today, and it scales with every new product
line or naming change. A marketing team cannot fix its own campaign naming.

**Technical impact** — Business configuration inside a view module, which also
means the allocator imports from `apps.amazon_api.views` to reach it — a
dependency from the engine to a view layer.

**Recommendation** — Move the mapping to a table with an admin surface, seeded
from the current dictionary. Keep `unmapped_ppc_campaigns` as the report, but
have it point at the admin page rather than at a source file.

**Related documents** — [sku-allocation.md](sku-allocation.md)

---

## `MKT-ALLOC-003` · Amazon's own SKU attribution is discarded and re-derived

| | |
|---|---|
| **Priority** | P3 on local evidence — **P1 if production has any multi-SKU ASIN; unverified** |
| **Status** | open — resolved by the same change as `MKT-ALLOC-002` |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | `MKT-ALLOC-002` — one edit fixes both |

**Current behaviour** — Amazon states the SKU it charged for. Both the older and
the current advertised-product tables store it. The allocator reads only the
ASIN, then Pass 2 re-derives the SKU split statistically from revenue and price.

**Expected behaviour** — Where Amazon states the SKU, that is the answer. Pass 2
is for the cases where it does not.

**Root cause** — Shared with `MKT-ALLOC-002`: the engine was specified as a
two-pass ASIN→SKU model against a table that, at the time, was the only source.
The SKU column was stored and never wired in, and the newer table that also
carries it was never adopted.

**Evidence** — source: **code** for the behaviour, **local data** for the scale.
`_load_sp_asin_spend` and `_pass1_sp` both aggregate `.values('asin')`, dropping
the SKU. On the development database the current table carries a SKU on 38,824
of 40,483 rows.

**Business impact** — **Unknown, and it hinges on one production query.**

*Local observation:* all 114 active USA ASINs map to exactly one non-excluded
SKU, so Pass 2's estimate always equals Amazon's answer and the impact is nil.
**This is the development catalogue.** If production carries any ASIN with two
active SKUs — a variation family, a repackaged size — this gap is **live now**,
splitting spend by estimate while the exact figure sits unread.

**Run that query on production before deprioritising this.** Count active ASINs
with more than one non-excluded SKU. Non-zero makes this P1 today.

**Technical impact** — A correctness landmine that arms on a *catalogue* change
rather than a code change, so nothing in the deploy pipeline would catch it.

**Recommendation** — Fold into the `MKT-ALLOC-002` change: when reading the
campaign-grain table, take its SKU where present and fall back to the
revenue-and-price estimate only where it is absent. Add a check reporting any
ASIN with more than one active SKU, so the day this starts to matter is visible.

**Related documents** — [sku-allocation.md](sku-allocation.md)

---

## `MKT-ALLOC-004` · The smoothing docstring describes a blend the code does not perform

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | bug — stale documentation in code |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — `_ema_smooth` is documented as blending "today's value
with yesterday's published value". It reads the previously persisted rows for
**the same date** and blends against those — damping movement between successive
runs of one day, not between consecutive days. An inline comment states this
correctly, one line below the docstring that does not.

**Expected behaviour** — The docstring states what the function does.

**Root cause** — The smoothing was reasoned about as a day-over-day EMA and
implemented as a within-day one, which is the behaviour the T+3 settlement model
actually needs. The docstring was never updated to match.

**Evidence** — source: **code**.
```python
prev = SkuPpcAllocation.objects.filter(marketplace=mp, date=d)   # same date, not d-1
```

**Business impact** — None. The implemented behaviour is the correct one for a
day that is recomputed hourly.

**Technical impact** — A reader reconciling the engine against the spec will
find a discrepancy that does not exist, or "fix" the code to match the
docstring and introduce day-over-day smearing across settlement states. This is
the third instance of the same pattern in this codebase; see the
[Inventory retrospective](../inventory/RETROSPECTIVE.md).

**Recommendation** — Rewrite the docstring: blends against the previous run of
the same day, so successive intra-day recomputes do not swing published figures.

**Related documents** — [sku-allocation.md](sku-allocation.md)

---

---

## `MKT-AMS-001` · A dataset that stops delivering is silent

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — Six datasets feed the hourly figures: traffic and
conversion for each of Sponsored Products, Brands and Display. If one stops
arriving — a subscription lapses, a provisioning failure is never retried,
Amazon changes an endpoint — its events simply stop. The hourly table keeps
filling from the other five, and nothing reports the silence.

**Expected behaviour** — A subscription that has delivered nothing for longer
than its normal cadence is reported.

**Root cause** — The pipeline is built for exactly-once consumption of what
arrives, and it does that well. Nothing was built to notice what does *not*
arrive, because absence has no event to hang a check on.

**Evidence** — source: **code**. `ingest_ams_s3` touches `last_ingest_at` on
active subscriptions when a run produces buckets, so the signal to alert on
already exists and is written — but no command, view or scheduled check reads
it. `seed_ams_subscriptions` creates and repairs subscriptions; it does not
report stale ones.

**Business impact** — Sponsored Brands or Display spend would quietly vanish
from the intra-day picture while Sponsored Products kept flowing, so the day
would look cheaper than it is. The settled daily figures would still be right,
so the discrepancy surfaces a day later as an unexplained jump — if anyone
notices.

**Technical impact** — The one field that would answer "is this dataset alive"
is maintained and unread.

**Recommendation** — A scheduled check comparing `last_ingest_at` per active
subscription against a threshold. The threshold is a judgement about how quiet a
dataset can legitimately be overnight; start at 6 hours and tune.

**Same remedy as `MKT-ADS-001`** — both signals are written and unread. Build
one pipeline-health check covering the stream and the report days, not two.

**Related documents** — [ams-stream.md](ams-stream.md)

---

## `MKT-AMS-002` · The legacy dataset map covers SP only outside North America

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — When an event carries no `dataset_id`, the dataset is
inferred from the publishing account in the topic ARN. That lookup is complete
for North America and, for Europe and the Far East, lists Sponsored Products and
budget-usage only. A European Sponsored Brands or Display event on that path
cannot be identified and is skipped.

**Expected behaviour** — Every dataset we subscribe to is identifiable in every
region we advertise in.

**Root cause** — Deliberate and documented: the map was filled in for the
regions and products in use at the time, with a comment saying the rest would be
added as needed. UK advertising is the case that makes it needed.

**Evidence** — source: **code**. `_SNS_ACCOUNT_TO_DATASET` in `ams_consumer.py`
carries three EU and three FE entries against seven NA ones, with the comment
"SB/SD will be added as needed".

**Business impact** — None on the current Firehose path, which carries
`dataset_id` in the payload and never needs the fallback. It bites only if a
marketplace is wired through the legacy SNS envelope path, at which point UK
Brands and Display spend would be silently dropped rather than reported.

**Technical impact** — A fallback that is complete for one region and partial
for others, where the partiality is invisible until it matters.

**Recommendation** — Fill in the EU and FE accounts for the four missing
datasets from Amazon's dataset reference, or remove the fallback entirely if the
legacy path is confirmed dead — an incomplete safety net is worse than a
documented absence of one.

**Related documents** — [ams-stream.md](ams-stream.md)

---

## `MKT-ADS-001` · A report day that never resolves is invisible

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none — **same remedy as `MKT-AMS-001`; build one health view, not two** |

**Current behaviour** — Every report day and kind records its outcome, and that
record is used to **suppress** a day whose data is incomplete. Suppression is the
right behaviour and it is silent: a day that never resolves simply never appears,
and nothing reports that it is missing.

**Expected behaviour** — A day that has not reached `ok` or `empty_from_amazon`
after a reasonable window is reported, so somebody can decide whether to chase
it.

**Root cause** — The completeness layer was built to answer "may this day be
shown", which is a question with a yes/no answer and no need for an alert. The
inverse question — "which days did we never manage to fill" — was never asked of
the same data, and the data supports it.

**Evidence** — source: **code**. `completeness.py` states its contract as gating
whether a day appears; every consumer listed uses it that way. `log_sync` writes
an outcome per marketplace, date and source — the exact record an alert would
read — and no command, view or scheduled check reports on it.

*Local corroboration, provisional:* across three months and nine report kinds,
two days never resolved — 2026-06-30 and 2026-07-02, both Sponsored Brands.
Neither is surfaced anywhere. The re-pull design recovered everything else, so
the volume is small and the silence is the problem rather than the rate.

**Business impact** — Small and open-ended. A permanently missing day removes
its campaign, search-term and placement rows from every settled figure without
saying so, and the figures around it look normal. Two days in three months is
tolerable; the same failure sustained for a week would not be, and would look
identical.

**Technical impact** — A completeness record that answers one of the two
questions it can answer.

**Recommendation** — One check across both pipelines: report days where a source
is still unresolved beyond its window, and active stream subscriptions whose
last ingest is older than theirs (`MKT-AMS-001`). Both signals are already
written; neither is read. Build it once.

**Related documents** — [ads-api.md](ads-api.md), [ams-stream.md](ams-stream.md)
**Related decisions** — `MKT-D-007`

---

## `MKT-UPL-001` · An unmatched campaign name on upload is logged, never reported

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — Seller Central's hourly export has no campaign id, so
campaigns are matched by name. A name that resolves to nothing keeps a
placeholder id — the right call, since dropping the row would lose real spend —
and the miss is written to the application log. The person who uploaded the file
is told the upload succeeded, with no mention that some campaigns were not
matched.

**Expected behaviour** — The upload result names the unmatched campaigns, the
way the parser already names a missing column.

**Root cause** — The placeholder fallback and the audit record were built at
different times. The audit captures rows imported and the date range; the
name-matching outcome was treated as a diagnostic and sent to the log, where the
uploader will never see it.

**Evidence** — source: **code**. `import_hourly_csv_bytes` collects
`unmatched_names`, emits a `logger.warning`, and neither stores them on the
audit record nor returns them in its result dict. The result dict is what the
upload view renders.

**Business impact** — Spend under a placeholder id does not join to that
campaign's detail view, so a campaign silently under-reports its hourly figures
while its daily figures look normal. Nobody is prompted to investigate, because
the upload reported success.

None on the current data — all 202 campaign ids on uploaded rows are real, so
nothing is currently mismatched. *Local; provisional.*

**Technical impact** — A diagnostic that exists, is computed, and is discarded
at the boundary where it would be useful.

**Recommendation** — Return the unmatched names in the result and store them on
the upload audit, so the uploader sees them and the history is queryable. The
re-key command already exists to fix them once the names resolve; this is only
about knowing.

**Related documents** — [hourly-upload.md](hourly-upload.md)

---

## `MKT-CAMP-001` · Nothing flags a campaign whose profit rests mostly on fallback margins

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — Every campaign profit row carries an attribution
coverage percentage: the share of the campaign's sales that matched to a SKU we
hold real costs for. Where coverage is low, the margin is computed from a
fallback — referral percentage only, with no SKU-specific cost of goods or
fulfilment fee. The figure is stored and displayed as a column. Nothing sorts,
filters, warns or thresholds on it.

**Expected behaviour** — A campaign whose profit is mostly estimated is visibly
distinguished from one whose profit is measured, without the reader having to
compare a column they may not know the meaning of.

**Root cause** — Coverage was added as an honesty measure and it does its job at
the row level. The step from "the number is present" to "the number changes how
the row reads" was never taken, because at the time every campaign had good
coverage and the distinction was theoretical.

**Evidence** — source: **code**. `compute_campaign_profit` computes
`attribution_coverage_pct` and documents it as flagging campaigns whose profit
relies on fallback estimates; `api_campaigns_list` returns it as one column
among twenty. No threshold, ordering or styling references it.

**Business impact** — A campaign's profit can be largely estimated and read
exactly like one that is measured. Decisions to scale or cut are taken on the
profit figure, so an estimate presented with the same authority as a measurement
is the kind of error that compounds.

**Technical impact** — The value that qualifies every other value on the row is
inert.

**Recommendation** — Threshold it: below a coverage level, mark the row and
qualify the profit figure in the UI the way "not started" qualifies a container
variance in Inventory. The threshold is a business judgement — 80% is a
reasonable start — and should be confirmed rather than assumed.

**Related documents** — [campaigns.md](campaigns.md)
**Related decisions** — `MKT-D-009`

---

## `MKT-TERM-001` · Signal thresholds are fixed in code and identical across marketplaces

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — The five search-term signals fire on fixed constants:
spend above $5 with no orders, click-through above 0.5% with conversion below
2%, return on ad spend above 5, and a losing-money floor of −$20. The same
numbers apply to every marketplace, ad product and product category.

**Expected behaviour** — Thresholds are business settings, adjustable without a
deploy, and at minimum expressed per marketplace since they are money amounts in
different currencies.

**Root cause** — They were chosen to make the signals useful on the USA account
and hard-coded, which was the right first move. Nothing has yet forced the
question, because Sponsored Products USA is the only account the page is used
against in anger.

**Evidence** — source: **code**. Five module-level constants in
`compute_campaign_profit`, referenced directly in the tagging loop with no
per-marketplace lookup.

**Business impact** — A $5 spend threshold is a different judgement in GBP, and
a very different one for a £40 product than a £6 one. On a single-marketplace,
single-currency account it is invisible; it misleads quietly the moment UK
advertising is managed from this page.

**Technical impact** — Business configuration in module constants — the same
shape as `MKT-ALLOC-001` and the Inventory supplier-mapping gaps.

**Recommendation** — Move to per-marketplace settings with the current values as
defaults. Do it when UK advertising starts being managed here, not before.

**Related documents** — [search-terms.md](search-terms.md)
**Related decisions** — `MKT-D-010`

---

## Production verification queue

Findings below rest on local development data and **cannot be settled here**.
Each is one query on production. Until then their priority is provisional.

**This queue is worked at implementation time, not documentation time.** When a
feature is built or one of these gaps is picked up, run its query first, update
the classification if the evidence moves, and close or re-prioritise the row on
what it shows. Nothing here blocks a document.

| Gap | The one question production answers |
|---|---|
| `MKT-ALLOC-003` | does any active ASIN carry more than one non-excluded SKU? Non-zero makes this P1 today |
| `MKT-ALLOC-001` | how much spend sits in "Unallocated PPC" on production? Sets the real priority |

## Closed

| ID | Title | Closed by |
|---|---|---|
