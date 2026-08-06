# SKU allocation

files: `apps/dashboard/ppc_allocator.py`
       `apps/dashboard/management/commands/{compute_sku_ppc,unmapped_ppc_campaigns}.py`
       `apps/amazon_api/views.py` — campaign-name → product-group matching
verified against: `4eb69fe` · 2026-08-06

Amazon reports advertising spend per **campaign**. Profit is measured per
**SKU**. This engine bridges the two: it decides how much of each campaign's
spend belongs to each SKU, every day.

## Purpose

Every per-SKU money figure in Pulse that involves advertising — ad cost, TACoS,
contribution margin, campaign profit — rests on this allocation. Amazon does not
provide it. A campaign advertises ASINs, an ASIN carries one or more SKUs, and
the split between them has to be inferred from what those SKUs actually sell.

Because it is inferred, every row records **how** it was derived and **how much
to trust it**. That is the difference between an allocation engine and a guess:
the guess is still there, but it is labelled.

## Scope

Covers the two-pass allocation, its fallbacks, reconciliation, smoothing and
settlement.

**Not covered:**
- where campaign spend comes from — [ads-api.md](ads-api.md), [ams-stream.md](ams-stream.md)
- what the allocated cost is used for — [campaigns.md](campaigns.md), and
  [financials](../financials/README.md)

## Business workflow

```
Campaign spend for a day
        ↓  Pass 1 — campaign → ASIN weights
   ASIN share
        ↓  Pass 2 — ASIN → SKU weights
   SKU share  →  spend × asin_weight × sku_weight
        ↓
   reconcile to campaign spend  →  smooth  →  persist with source + confidence
        ↓
   provisional → settling → locked at T+3
```

## Business rules

1. **Spend is attributed, never assumed.** Each campaign's spend is split by
   evidence, in a fixed order of preference, and the source is recorded on every
   row.
2. **Pass 1 prefers Amazon's own product report.** Where Sponsored Products
   advertised-product data exists for the day, ASIN weights come from it. Where
   it does not, yesterday's weights for that campaign carry forward. Where
   neither exists, the campaign's **name** is matched to a product group and
   weights come from that group's recent revenue mix.
3. **Pass 2 splits an ASIN across its SKUs by recent revenue and price** —
   65% seven-day revenue, 25% thirty-day revenue, 10% catalogue price. The price
   term is what lets a SKU with no sales history still receive a share.
4. **A SKU with no history at all is priced-in, not zeroed.** Where no sibling
   has revenue, the split falls back to price, and where there are no prices, to
   an equal share.
5. **Σ SKU spend = campaign spend, exactly.** After both passes the rows are
   scaled to the campaign's actual spend; any material adjustment marks the rows
   `reconciled` and lowers their confidence.
6. **A campaign that cannot be attributed keeps its spend unattributed.** It is
   reported as its own "Unallocated PPC" figure and never spread across SKUs.
   See `MKT-D-001`.
7. **Every row carries a confidence score**, from the strength of its source and
   of the SKU's own history against its siblings. An Amazon-sourced row scores
   1.00 at the source term; an equal split scores 0.30.
8. **Days settle, then lock.** Today is *provisional*, a past day is *settling*,
   and at **T+3** the day *locks* and is not recomputed. See `MKT-D-003`.
9. **Unlocked days are smoothed**, blending 70% of the new figure with 30% of
   the previous run's, so a partially-reported day does not swing the numbers.
10. **`AMZN.*` SKUs are excluded entirely** — we do not pay advertising against
    Amazon Renewed or Warehouse listings, and their share redistributes to the
    normal sibling SKUs. See `MKT-D-004`.

## States

| State | Meaning | When |
|---|---|---|
| Provisional | the day is still running; figures will move | date is today |
| Settling | the day is complete but late attributions still arrive | T-1 to T-2 |
| Locked | final; not recomputed unless forced | T+3 and older |

## User actions

The allocation has no UI of its own — it is a scheduled computation whose output
appears throughout Marketing and Reporting.

| Action | Who | Result |
|---|---|---|
| Recompute a day or window | ops | allocations rebuilt; locked days skipped |
| List unmapped campaigns | ops | campaigns whose name matches no product group, with their spend |

## System behaviour

- The computation is **idempotent**: a day's rows are replaced wholesale, so
  re-running cannot double-count.
- **Spend source per day follows a fixed precedence.** A manually uploaded
  Seller Central hourly file, where present for a settled day, is authoritative
  alone. Otherwise the Ads API daily snapshot wins for settled days and the
  hourly stream fills only campaigns it missed; for today, whichever is larger
  wins. See `MKT-D-002`.
- Campaign names missing from the stream are enriched from historical daily
  snapshots, because the name is what the product-group matcher needs.
- A day with no allocatable rows **clears** that day's rows rather than leaving
  stale ones behind.

## Data model

- **Allocation row** — one (day, campaign, ASIN, SKU): the campaign's spend, the
  two weights, the resulting SKU spend, the revenue signals it was derived from,
  the attribution source, the confidence, and the settlement state.
- **Attribution source** — which of the derivation paths produced the row. It is
  the audit trail, and it is what makes a low-quality allocation visible rather
  than merely wrong.

## Edge cases

- **An ASIN with no mapped SKUs.** The ASIN itself stands in as the SKU, so the
  spend is not lost.
- **A campaign advertising a product group that has no revenue yet.** Equal
  split across the group's ASINs, recorded as a cold start at 0.30 confidence.
- **A campaign whose name matches no product group.** Its spend stays
  unallocated and is reported (`MKT-D-001`). Four such campaigns totalling
  $48.02 over 30 days on the development snapshot — *provisional; re-measure on
  production.*
- **Re-running a day that is already locked.** Skipped, so a backfill cannot
  silently restate a closed period.

## Observations — not gaps

Recorded because each looks like a defect and is not. *Source: dev snapshot.*

- **47% of all allocation rows are equal splits.** They are confined to
  2026-04-10 → 2026-05-22. Revenue history begins 2026-05-01, so every row
  before that had no signal to use and the cold-start path was correct. It stops
  entirely once the windows fill — zero on 2026-06-10.
- **Allocation output stops at 2026-06-16** while its inputs are current. **No
  crontab is installed on the development machine** — `deploy/crontab.txt`
  specifies 33 jobs and `crontab -l` reports none. Every freshness difference
  between Marketing tables reflects which command someone last ran by hand. The
  engine itself runs correctly on demand: 2026-08-04 allocates $8,655.30 against
  $8,708.91 of campaign spend.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `MKT-ALLOC-001` | the campaign-name → product-group map is a hardcoded dict in a view module | missing implementation |
| `MKT-ALLOC-002` | the allocator reads a superseded, campaign-blind copy of the advertised-product data | missing implementation |
| `MKT-ALLOC-003` | Amazon's own SKU attribution is discarded and re-derived | missing implementation |
| `MKT-ALLOC-004` | the smoothing docstring describes a blend the code does not perform | bug — stale docs |

## Architecture mismatches

`ARCH-009` — advertised-product data is stored twice, and this engine reads the
weaker copy. `MKT-ALLOC-002` and `MKT-ALLOC-003` are both symptoms of it.

## Related decisions

`MKT-D-001` `MKT-D-002` `MKT-D-003` `MKT-D-004`

## Related documents

- [ads-api.md](ads-api.md) — where the daily reports come from
- [ams-stream.md](ams-stream.md) — where the hourly figures come from
- [campaigns.md](campaigns.md) — what the allocated cost is shown against
