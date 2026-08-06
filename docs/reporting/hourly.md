# Hourly patterns

files: `apps/dashboard/hourly_aggregator.py` · `apps/dashboard/completeness.py`
       `apps/dashboard/management/commands/snapshot_hourly_metrics.py`
       `templates/dashboard/hourly_patterns.html`
verified against: `b70e2c2` · 2026-08-06

When during the day sales actually happen, and what they cost per hour. The one
view built on an explicit contract about what it is allowed to show.

## Purpose

A daily total hides the shape of a trading day. Knowing that half of revenue
arrives between 6pm and 11pm changes when budgets are set, when prices move and
when stock-outs matter. This view exposes that shape.

It is also the strictest view in the application about **not showing a number it
cannot stand behind**, because an hourly heatmap invites pattern-reading and a
single fabricated cell would be read as a real pattern.

## Scope

Covers the per-hour cells, the completeness contract that gates them, and how
advertising is distributed across hours.

**Not covered:**
- daily totals — [daily.md](daily.md)
- where hourly advertising figures come from — [ams-stream.md](../marketing/ams-stream.md)
- the manual hourly upload — [hourly-upload.md](../marketing/hourly-upload.md)

## Business rules

1. **A day is shown only if its core sources are complete** — hourly orders and
   hourly advertising both. A day failing either is **excluded entirely**, not
   shown partially. See `REP-D-003`.
2. **Missing means missing, never zero.** An unknown figure is absent; nothing
   is defaulted to zero, because zero is a claim.
3. **An aggregate is shown only when every component is known.** Total ad spend
   for an hour appears only if Sponsored Products, Brands and Display are all
   known for that day — otherwise the total is absent rather than understated.
4. **Sponsored Products hourly spend is real. Brands and Display are
   distributed uniformly across the day**, and only when that day's source is
   confirmed complete. This is the only estimation permitted anywhere in the
   view, and it is labelled. See `REP-D-004`.
5. **Layers never contaminate each other.** A daily figure spread across hours is
   never written into the hourly table; the distribution happens at read time so
   the stored data stays honest about its own grain.
6. **Completeness is a recorded fact, not an inference.** Every ingestion writes
   an outcome per day and source, and this view reads those records rather than
   guessing from whether rows exist.

## The completeness contract

| Layer | Sources | Effect if incomplete |
|---|---|---|
| Core | hourly orders, hourly advertising | the whole day is excluded |
| Ads | Brands daily, Display daily | those columns read as unknown for that day |

"Amazon returned nothing" counts as complete — it is a known zero. "Failed" and
"still running" both count as incomplete, because neither is an answer.

## System behaviour

- Hourly snapshots are written **every hour in production**, which is what makes
  the view current within the hour rather than the day.
- Cells are assembled at read time from three tables plus the completeness log;
  nothing pre-computes a heatmap, so a late-arriving day appears as soon as its
  sources are marked complete.
- Where Brands or Display are unknown, the per-hour figure is absent and the
  reason is available rather than silently blank.

## Data model

- **Hourly metric snapshot** — one per marketplace, date and hour: revenue,
  units, orders and margins.
- **Hourly SKU snapshot** — the same grain per SKU, which is what lets the daily
  page show last-hour units per SKU.
- **Sync log** — the completeness record this view gates on. See
  [ads-api.md](../marketing/ads-api.md).

## Edge cases

- **A day where advertising is complete and orders are not.** Excluded, by rule
  1 — an hourly cost pattern with no revenue pattern invites exactly the wrong
  conclusion.
- **A day covered by a manual hourly upload.** Marked complete by the upload, so
  it renders. See [hourly-upload.md](../marketing/hourly-upload.md).
- **An hour with genuinely no sales.** Shows zero, correctly — that is a known
  zero, distinct from an unknown.

## Observations — not gaps

*Source: local development data; provisional.*

- **The completeness source list still names report kinds Amazon does not
  offer** — Brands placement and the ad-group reports. They are retained
  deliberately so historical `failed` rows still validate; the kinds were
  removed from ingestion. See the observations in
  [ads-api.md](../marketing/ads-api.md).

## Related decisions

`REP-D-003` `REP-D-004`

## Related documents

- [daily.md](daily.md) — the totals these hours sum to
- [ams-stream.md](../marketing/ams-stream.md) — where hourly advertising comes from
- [ads-api.md](../marketing/ads-api.md) — the completeness record
