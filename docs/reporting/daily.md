# Daily dashboard

files: `apps/amazon_api/views.py` — `fetch_dashboard_data`
       `apps/dashboard/sync.py` · `apps/dashboard/models.py`
       `templates/dashboard/index.html`
verified against: `edcec31` · 2026-08-06

The first page anyone opens. Revenue, units, margin and ad spend for a chosen
window, per marketplace, refreshed through the trading day.

## Purpose

One page that answers "how are we doing" without anyone having to open Seller
Central. It has to be **fast**, **current** and **honest about which of those it
is trading away** — a figure for today is necessarily less settled than one for
last week, and the page says so rather than pretending otherwise.

## Scope

Covers the window selection, the headline figures, and how the page decides
where its numbers come from.

**Not covered:**
- the per-SKU table beneath the headlines — [product-performance.md](product-performance.md)
- hour-by-hour detail — hourly.md *(pending)*
- multi-month comparison — historical.md *(pending)*
- where ad spend per SKU comes from — [sku-allocation.md](../marketing/sku-allocation.md)

## Business workflow

```
choose marketplace + window
        ↓
serve from the freshest sufficient source:
   daily snapshots  →  hourly snapshots  →  a live call to Amazon
        ↓
headline figures · targets · per-SKU table
```

## Business rules

1. **A window is resolved in the marketplace's local time.** "Today" in the UK
   is not today in the USA, and a trading day belongs to the marketplace.
2. **Figures are served from the freshest sufficient source, in a fixed order** —
   stored daily figures first, hourly snapshots second, a live call to Amazon
   last. Each tier is tried and its failure falls through to the next. See
   `REP-D-001`.
3. **A live call is the last resort, never the default.** It is slow and rate
   limited, and the cached tiers are written by jobs that already ran.
4. **Margins are measured on revenue ex-VAT** — the rule and the error it has
   caused live in [product-performance.md](product-performance.md).
5. **Targets are pro-rated to the window.** A monthly revenue target shown
   against a 7-day window is that target divided by the days in the month and
   multiplied by the days in the window.
6. **Target matching ignores pack-size formatting.** "2", "2-Pack" and "Pack
   of 2" are the same target, because they are the same product.
7. **Ad spend is included at the level it is known.** Where the allocator has
   attributed spend to SKUs, per-SKU figures are real; where it has not, spend
   appears at group level and the difference is visible rather than smoothed.

## States

The window determines how settled the numbers are:

| Window | Served from | Settled? |
|---|---|---|
| Today | hourly snapshots, or a live call | no — moves all day |
| Yesterday | stored daily figures | mostly; late attribution still arrives |
| 7d · 30d · MTD · custom | stored daily figures summed | yes, except any part covering today |

## User actions

| Action | Who | Result |
|---|---|---|
| Choose a marketplace | anyone with access | the whole page re-resolves |
| Choose a window | anyone | today, yesterday, MTD, 7 days, 30 days, or a custom range |
| Force a live refresh | anyone | bypasses the cached tiers and calls Amazon |

Marketplace access is checked per request, so a user without access to a
marketplace cannot read its figures by changing the parameter.

## System behaviour

- **Each tier falls through on failure rather than erroring.** A failure in the
  cached path is logged and the next tier is tried, so the page degrades to
  slower-but-correct rather than to an error.
- **A range that includes today picks today up from the same place the "today"
  window does**, because that path writes what it computes — so the sum does not
  double-count or omit it.
- The per-SKU table is built by **one canonical builder** shared across the
  cached paths. See [product-performance.md](product-performance.md) and
  `ARCH-007`.

## Data model

- **Daily metric** — one per marketplace and date: revenue, units, orders, cost
  of goods, Amazon fees, fulfilment fees, margins and ad spend. The settled
  record of a day.
- **Hourly metric snapshot** — the same shape per hour, written through the day.
- **Monthly target** — per marketplace, month, product type and pack size.

## Edge cases

- **A marketplace with no data for the window.** Renders empty rather than
  failing; an empty marketplace and a broken one look different.
- **A custom range with the dates reversed or unparseable.** Falls back to a
  single day rather than erroring.
- **Today, before the first hourly snapshot.** Falls through to a live call,
  which is the slow path and the correct one.

## Observations — not gaps

*Source: local development data; provisional. Nothing runs on a schedule here.*

- **Daily metrics cover 2026-04-11 → 07-25 and stop.** Expected: the syncs that
  write them run only in production. The four marketplaces carry different day
  counts (`usa` 84, `uk` 78, `ae` and `sa` 47 each), which reflects when each was
  connected, not missing data.

## Architecture mismatches

`ARCH-001` — this endpoint lives in `apps/amazon_api`, an app named for an
integration, while it is the Reporting engine. `ARCH-004` covers moving it.

## Related decisions

`REP-D-001`

## Related documents

- [product-performance.md](product-performance.md) — the SKU table this page carries
- hourly.md *(pending)* — where today's figures come from
- [sku-allocation.md](../marketing/sku-allocation.md) — the ad cost shown here
