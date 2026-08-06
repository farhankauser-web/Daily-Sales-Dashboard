# Ads API reports

files: `apps/amazon_api/{ads_detail_reports,services}.py`
       `apps/amazon_api/management/commands/ingest_ads_detail_reports.py`
       `apps/dashboard/management/commands/backfill_ads_detail_reports.py`
       `apps/dashboard/completeness.py`
verified against: `aab72a3` · 2026-08-06

The settled daily figures. Amazon builds a report on request; we submit it, wait,
download it, and store the rows. Nine report kinds across Sponsored Products,
Brands and Display.

## Purpose

The stream says what today is costing. This says what yesterday **actually**
cost, per campaign, per keyword, per search term, per placement and per
advertised product — the numbers Amazon will stand behind and the ones every
settled figure in Marketing is built from.

It is asynchronous by nature: a report is requested, Amazon assembles it, and it
becomes available minutes later. Everything about the design follows from that.

## Scope

Covers report configuration, submission, polling, download and storage, and how
completeness is recorded.

**Not covered:**
- the hourly, intra-day figures — [ams-stream.md](ams-stream.md)
- what the stored rows are shown as — campaigns.md *(pending)*,
  search-terms.md *(pending)*, placements.md *(pending)*
- which source wins when they disagree — `MKT-D-002`

## Business workflow

```
submit a report kind for a date  →  Amazon assembles it  →  poll  →  download
                                                                        ↓
                                              normalise columns → upsert rows
                                                                        ↓
                                                    record the day as ok / empty / failed
```

## Business rules

1. **A report is requested per kind and per day.** Nine kinds are in use;
   Sponsored Display has no search-term or placement report, and Sponsored
   Brands has no placement report — those are Amazon limitations, not omissions.
2. **A report already in flight is resumed, never re-submitted.** Amazon returns
   *Too Early* when the same window and configuration were requested recently;
   that response carries the existing report id and we poll it.
3. **Amazon returning no rows is an answer, not a failure.** *Empty* is recorded
   distinctly from *failed*, because "we know there was nothing" and "we do not
   know" lead to different decisions.
4. **Recent days are re-pulled.** Attribution keeps arriving after a day closes,
   so a window of past days is requested again and the rows updated in place.
5. **Column names differ by ad product and are normalised on the way in.**
   Sponsored Products calls a field `targeting` where Brands calls it
   `targetingExpression`; purchases arrive as `purchases7d`, `purchases14d` or
   `purchases` depending on the report. One normaliser reconciles them.
6. **Rows are upserted on a natural key**, so a re-pull updates rather than
   duplicates. The key always includes the marketplace, the date, the ad product
   and the campaign.
7. **Every day and kind records its outcome.** That record is what decides
   whether a day's figures are shown at all. See `MKT-D-007`.
8. **Incomplete days are suppressed, not estimated.** A day missing a core
   source does not appear; a day missing only Brands or Display data appears
   without those columns.

## States

Per day and report kind:

| State | Meaning | Consequence |
|---|---|---|
| `ok` | rows received | the day's figures are shown |
| `empty_from_amazon` | Amazon returned nothing | treated as a known zero |
| `pending` | submitted, not yet downloaded | treated as missing until it lands |
| `failed` | the request or download errored | treated as missing; retried by later runs |

## Actors

| Actor | Does |
|---|---|
| Amazon | assembles a report on request, minutes to an hour later |
| The system | submits, resumes, polls, downloads, normalises, upserts, records the outcome |
| Ops | runs a one-off backfill when a longer history is needed |

## System behaviour

- **In production the ingest runs on a schedule** — a nightly pass for the
  previous day, a frequent daytime pass to pick up reports that were still
  building, and a daily pass that rewinds several days for late attribution.
- **Submissions are spaced and throttling is retried.** Amazon's burst limit on
  the report endpoint trips well below its documented rate under sustained load.
- **A failed day is not abandoned.** Later scheduled runs request it again, which
  is why transient failures self-heal without intervention.
- **The one-off backfill** submits every kind across a window, then polls until
  each day is resolved, capped so a stuck report cannot block the run.

## Data model

- **Report configuration** — per kind: the ad product, Amazon's report type, how
  rows are grouped, and the columns requested. This is where a report's grain is
  decided, and therefore what questions its rows can answer later.
- **Snapshot rows** — one table per grain: targeting, search term, advertised
  product, placement. Each keyed by marketplace, date, ad product and campaign,
  plus whatever identifies the row within a campaign.
- **Sync log** — one row per marketplace, date and source, carrying the outcome
  and the report id. It is the completeness record, not a debug log.

## Integrations

| System | Direction | What moves |
|---|---|---|
| Amazon Ads API v3 reporting | out | a report request per kind per day |
| Amazon Ads API v3 reporting | in | gzipped JSON rows, minutes later |

## Edge cases

- **The same report requested twice.** Amazon deduplicates and returns the
  in-flight id; we resume it rather than failing or double-counting.
- **A report kind Amazon does not offer** for an ad product. Requested once,
  rejected, and removed from the configuration — see the observations below.
- **Throttling under a backfill.** Expected; the run retries and later scheduled
  runs recover whatever is still missing.
- **A day that never recovers.** Its rows are simply absent and the figures for
  that day are suppressed rather than shown short. Nothing reports the hole
  (`MKT-ADS-001`).

## Observations — not gaps

*Source: local development data; provisional. Nothing runs on a schedule here,
so these describe structure and history rather than production health.*

- **The sync log shows 60 failures for Sponsored Brands placement and one for
  Sponsored Products ad-group.** Both are historical residue from report kinds
  Amazon does not offer: Brands rejected placement grouping outright, and there
  is no standalone ad-group report type in v3. Both were removed from the
  configuration — the last failure is 2026-06-11 — and the rollups they would
  have provided are computed from other reports instead.
- **The re-pull design demonstrably works.** Across three months and nine report
  kinds, exactly two days never resolved (2026-06-30 and 2026-07-02, both
  Sponsored Brands). Every other apparently-unrecovered date is simply the most
  recent day, still in flight.
- **Throttling failures are common and self-healing.** They appear against most
  sources and are followed by successes for the same day.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `MKT-ADS-001` | a day that never resolves is invisible — the completeness record is written and nothing watches it | missing implementation |

## Related decisions

`MKT-D-002` `MKT-D-007`

## Related documents

- [ams-stream.md](ams-stream.md) — the intra-day figures this is measured against
- [sku-allocation.md](sku-allocation.md) — the largest consumer, and `ARCH-009`
- campaigns.md *(pending)* — where these rows are shown
