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

**Affected documents** — [sku-allocation.md](sku-allocation.md), [ams-stream.md](ams-stream.md), ads-api.md *(pending)*

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

**Affected documents** — [ams-stream.md](ams-stream.md), ads-api.md *(pending)*
