# Marketing — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**Where the evidence comes from.** Counts here are measured against the **local
SQLite database** unless stated otherwise. Findings drawn from *code* transfer to
production unchanged; findings drawn from *data* are provisional and must be
re-measured on production Postgres before anyone acts on them, and say so. See
[templates/README.md](../templates/README.md) for the precedence.

**No scheduler runs on the development machine.** `deploy/crontab.txt` specifies
33 jobs; `crontab -l` reports none. Every freshness difference between Marketing
tables reflects which command someone last ran by hand, **not** pipeline health.
Staleness alone is never evidence of a defect here.

**Absence of data is not a defect.** Every gap carries a **Classification** —
missing implementation, bug, configuration, missing operational process, or
legacy data — a root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|
| `MKT-ALLOC-002` | Pass 1 spreads each SP campaign's spend over every ASIN advertised that day | P2 | missing implementation | open |
| `MKT-ALLOC-001` | The campaign → product-group map is hardcoded in a view module | P2 | missing implementation | open |
| `MKT-ALLOC-003` | Amazon's own SKU attribution is discarded and re-derived | P3 | missing implementation | open |
| `MKT-ALLOC-004` | The smoothing docstring describes a blend the code does not perform | P3 | bug — stale docs | open |

---

## `MKT-ALLOC-002` · Pass 1 spreads each SP campaign's spend over every ASIN advertised that day

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes — but the report must be re-requested and history re-fetched |
| **Dependencies** | none |

**Current behaviour** — For a Sponsored Products campaign, Pass 1 computes ASIN
weights from the day's advertised-product spend **summed across every SP
campaign**, then applies that same distribution to each campaign individually. A
campaign advertising one ASIN therefore has its spend spread across every ASIN
advertised that day.

**Expected behaviour** — A campaign's spend is distributed over the ASINs *that
campaign* advertised, in that campaign's own proportions.

**Root cause** — The data is not stored at that grain, and is not requested at
that grain. `PPCProductSnapshot` is keyed by marketplace, date, ASIN and campaign
type, with no campaign field; the report that fills it is requested with
`groupBy: ['advertiser']` and columns that do not include `campaignId`. The
allocator's own comment states the limitation and calls the day-level
proportions "the closest defensible weighting" — accurate, and a workaround
rather than the intent.

**Evidence** — source: **code**, so it holds in production.
```python
# ppc_allocator.py, _pass1_sp — no campaign filter on the queryset
qs = (PPCProductSnapshot.objects
      .filter(marketplace=mp, date=d, campaign_type='sp')
      .values('asin').annotate(spend=Sum('spend')))

# services.py, get_advertised_product_summary — campaignId is never requested
'groupBy':  ['advertiser'],
'columns':  ['advertisedAsin', 'advertisedSku', 'impressions', 'clicks',
             'cost', 'purchases7d', 'sales7d', 'unitsSoldClicks7d'],
```

**Business impact** — Per-SKU ad cost is misattributed between SKUs whenever SP
campaigns target different ASINs, which is the normal case. Campaign totals stay
correct because reconciliation scales each campaign to its true spend — so the
error is invisible in any campaign-level view and only distorts the per-SKU
split. TACoS and contribution margin for individual SKUs inherit it.

**Technical impact** — The most authoritative path in the engine is degraded to
an approximation, while still recording `sp_advertised_product` as its source
and scoring 1.00 confidence for it. The audit trail overstates the quality.

**Recommendation** — Request `campaignId` in the advertised-product report, add
it to `PPCProductSnapshot`, and filter Pass 1 by campaign. Re-fetch the history
that matters; older locked days can keep their existing figures. Until then, the
confidence score for this source should not be 1.00.

**Related documents** — [sku-allocation.md](sku-allocation.md), ads-api.md *(pending)*

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
| **Priority** | P3 — raise to P1 the moment one ASIN carries two active SKUs |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — The Sponsored Products advertised-product report carries
`advertisedSku`, and it is stored: 4,624 of 4,624 rows have a SKU, covering
$179,441 of spend. The allocator reads only the ASIN from those rows, then Pass 2
re-derives the SKU split statistically from revenue and price.

**Expected behaviour** — Where Amazon states the SKU, that is the answer. Pass 2
is for the cases where it does not.

**Root cause** — The engine was specified as a two-pass ASIN→SKU model, and the
report's SKU column was stored but never wired into it. The field exists and is
fully populated; nothing reads it.

**Evidence** — source: **code** for the behaviour, **dev snapshot** for the
scale. `_load_sp_asin_spend` and `_pass1_sp` both aggregate `.values('asin')`,
dropping `sku`. `PPCProductSnapshot.sku` is populated on 100% of rows.

**Business impact** — **None measurable today.** All 114 active USA ASINs map to
exactly one non-excluded SKU, and the SP report's 81 ASINs likewise resolve 1:1,
so Pass 2's estimate always equals Amazon's answer. *Provisional; re-measure on
production.*

It becomes real the first time a variation family or a second SKU shares an
ASIN — at which point spend is split by estimate while the exact figure sits
unread in the same table, and nothing signals the change.

**Technical impact** — A correctness landmine that activates silently on a
catalogue change rather than a code change.

**Recommendation** — In Pass 2, use the report's SKU where the row has one, and
fall back to the revenue-and-price estimate otherwise. Add a check that reports
any ASIN carrying more than one active SKU, so the day this starts to matter is
visible.

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

## Closed

| ID | Title | Closed by |
|---|---|---|
