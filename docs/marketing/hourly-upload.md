# Hourly upload

files: `apps/dashboard/{manual_hourly_parser,manual_hourly_importer}.py`
       `apps/dashboard/management/commands/{import_hourly_csv,relink_manual_hourly_campaign_ids}.py`
verified against: `cd5d9c0` · 2026-08-06

Amazon's advertising console lets you download hourly reports by hand. This is
how those files get in — and it is the only way to have hourly history for days
before the stream was subscribed.

## Purpose

The stream only knows what it was subscribed for. Anything earlier is
unrecoverable from it, and Amazon offers no API for hourly history — only a
console download, fourteen days at a time, reaching back about a month.

The upload exists to fill that. It is also the escape hatch when the stream has
missed a period: a person can always go and fetch the day themselves.

## Scope

Covers the file, its parsing, how campaigns are matched, and what an upload
supersedes.

**Not covered:**
- the automated hourly pipeline — [ams-stream.md](ams-stream.md)
- the settled daily figures — [ads-api.md](ads-api.md)
- how an uploaded day is then used — [sku-allocation.md](sku-allocation.md), `MKT-D-002`

## Business workflow

```
download an hourly report from Amazon's console (≤14 days, one ad product)
        ↓  upload
parse: detect encoding, map columns, coerce date + hour
        ↓
match campaign names → real campaign IDs
        ↓
write the hours, marked as manual  →  record the day as complete  →  audit the upload
```

## Business rules

1. **An uploaded hour supersedes the stream for that hour.** A person fetching a
   file from Amazon's own console has the authoritative figure, and the stream's
   accumulated total for that hour is replaced rather than added to. See
   `MKT-D-008`.
2. **A settled day with an upload is authoritative alone.** Neither the stream
   nor the daily report is mixed into it — see `MKT-D-002` for why summing them
   double-counts.
3. **One ad product per file.** Amazon exports Sponsored Products, Brands and
   Display separately, and the file does not say which it is; the uploader does.
4. **Fourteen days per file, matching Amazon's own export limit.** A wider file
   is refused with the reason, rather than partly imported.
5. **Campaigns are matched by name, because the export has no ID.** The name is
   resolved against the campaign dimension, then against historical daily
   figures. An unmatched name keeps a placeholder rather than being dropped —
   losing the spend would be worse than misfiling it.
6. **Every upload is audited**, successful or not: who, when, which file, how
   many rows, which dates, and why it failed.
7. **An uploaded day is marked complete**, which is what allows it to appear at
   all. See `MKT-D-007`.

## Actors

| Actor | Does |
|---|---|
| Ops | downloads the report from Amazon, chooses the ad product, uploads |
| The system | parses, matches campaigns, writes hours, records completeness and the audit |

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Upload a file | ops | a ≤14-day export, one ad product | hours written, day marked complete, upload audited |
| Import from the command line | ops | same file | identical result — one code path, two entry points |
| Re-key legacy placeholder rows | ops | campaign names now resolvable | placeholder ids replaced with real ones |

## System behaviour

- The parser **detects the encoding** — Amazon's console emits UTF-8, UTF-16 and
  CP1252 depending on browser and locale — and maps column names
  case-insensitively against known aliases, because the headers differ between
  ad products and change over time.
- It reports **what it found and what it could not**, so a rejected file says
  which column was missing rather than failing opaquely.
- Rows are aggregated by date, hour and campaign before writing, so a file with
  finer granularity than hourly still lands correctly.
- Writing is a **replace** for the hours in the file, unlike the stream's
  accumulate — the two writers are deliberately different, and the source column
  records which wrote each row.

## Data model

- **Upload audit** — one per attempt: marketplace, ad product, who uploaded it,
  the filename, rows in the file, rows imported, the dates covered, and the
  outcome.
- **Hourly campaign figure** — the same rows the stream writes, marked with
  `manual` as their source. See [ams-stream.md](ams-stream.md).

## Validation rules

| Input | Rule | On failure |
|---|---|---|
| Ad product | Sponsored Products, Brands or Display | refused |
| File | parseable, with date, hour and a spend column | refused, naming what was missing |
| Date span | at most 14 days | refused, stating the span and asking for a split |
| Rows | at least one parseable row | refused, and the attempt is still audited |

## Edge cases

- **A campaign name that matches nothing.** Keeps a placeholder id and is logged.
  The spend is preserved, but the row will not join to that campaign's detail
  view until the name resolves — which the re-key command then fixes in bulk.
- **A campaign renamed since the export.** Matches nothing, for the same reason.
  Matching is on the normalised name, which tolerates spacing and case but not a
  genuine rename.
- **The same file uploaded twice.** Harmless: the hours are replaced with
  identical values, and both attempts are audited.
- **A file covering hours the stream has already recorded.** The upload wins,
  by rule 1.

## Observations — not gaps

*Source: local development data; provisional.*

- **Uploads cover 2026-05-13 to 2026-06-14 only, and Sponsored Products only.**
  That is exactly the window before the stream was delivering — the feature's
  purpose, not a coverage gap.
- **No placeholder campaign ids remain.** All 202 distinct campaign ids on
  manual rows are real numeric ids, so the re-key command has already been run
  and the name-matching is resolving.
- **Two of eleven uploads failed.** Both were rejected at parse or validation and
  audited as such, which is the designed behaviour.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `MKT-UPL-001` | an unmatched campaign name is logged but never reported to the person who uploaded the file | missing implementation |

## Related decisions

`MKT-D-002` `MKT-D-007` `MKT-D-008`

## Related documents

- [ams-stream.md](ams-stream.md) — the other writer of the same table
- [ads-api.md](ads-api.md) — the settled daily figures
- [sku-allocation.md](sku-allocation.md) — what an uploaded day feeds
