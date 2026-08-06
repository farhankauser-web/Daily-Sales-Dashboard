# Reporting

What sold, when, and at what margin. The daily dashboard, the SKU table, hourly
patterns, historical trends, the widget dashboard, and the Amazon MCF order
mirror.

## Purpose

This section owns **sales reporting for our own listings** — the figures the
business looks at first each morning and watches through the day. It answers
*what happened*: revenue, units, margin, by day, by hour and by SKU.

It does not own advertising. Campaign spend, search terms and the spend-to-SKU
attribution belong to [marketing](../marketing/README.md); this section
*consumes* the per-SKU ad cost that produces.

It does not own money. P&L, COGS, settlements and payouts belong to
**financials** *(pending)*. This section reports revenue and contribution
margin; that one reconciles them to the bank.

**This section is complete and frozen** except for future feature changes. Six
features are documented; the registers are the backlog, not unwritten work. The
process lessons are in [RETROSPECTIVE.md](RETROSPECTIVE.md).

## Features

| Document | Covers | Open here when |
|---|---|---|
| [daily.md](daily.md) | the daily dashboard and its KPI tiles | today's headline figures look wrong |
| [product-performance.md](product-performance.md) | the SKU table · `ARCH-007` | a SKU's revenue, margin or ad cost looks wrong |
| [hourly.md](hourly.md) | hourly patterns and the completeness gates | an hour is missing or the heatmap has holes |
| [historical.md](historical.md) | multi-day and multi-month trends | a period comparison looks wrong |
| [command-center.md](command-center.md) | the widget dashboard · `ARCH-003` | a widget is empty or stale |
| [mcf-orders.md](mcf-orders.md) | the Amazon MCF order mirror · `ARCH-006` | an MCF order is missing |

## Relationships

```
Amazon SP-API ──orders/finances──→ DailyMetric · DailySkuSnapshot
                                          ↓
        hourly snapshots ──────→  daily dashboard · SKU table · historical
                                          ↓
              per-SKU ad cost (marketing) → margin, TACoS
```

Two facts about this shape cause most confusion:

- **The same table is served by more than one code path**, chosen by what data
  is available for the window. They are not alternatives a reader picks between
  — see `ARCH-007`.
- **"MCF" means two different things.** This section mirrors Amazon's own MCF
  order list, read-only. The Walmart fulfilment pipeline that *creates* MCF
  orders belongs to `docs/walmart/` *(pending)* — `ARCH-006`.

## Ground truth

Established before writing. *Source: local development data; provisional.*

**The laptop runs no scheduled jobs — by design.** Production runs them
continuously. Staleness, empty tables and never-run jobs are expected here and
are never on their own evidence of a defect.

| Table | Local state |
|---|---|
| Daily metrics | 256 rows, 2026-04-11 → 07-25, four marketplaces |
| Daily SKU snapshots | 11,892 rows, 2026-05-01 → 07-25 |
| MCF order mirror | 1,783 rows |

Marketplaces carrying data: `usa` 84 days · `uk` 78 · `ae` 47 · `sa` 47.

## Navigation

| Working on… | Load |
|---|---|
| a headline figure | `CLAUDE.md` · this README · [daily.md](daily.md) · `gaps.md` |
| a SKU's margin or ad cost | `CLAUDE.md` · this README · [product-performance.md](product-performance.md) · `gaps.md` |
| a missing hour | `CLAUDE.md` · this README · [hourly.md](hourly.md) · `gaps.md` |
| a trend or target comparison | `CLAUDE.md` · this README · [historical.md](historical.md) · `gaps.md` |

## Current priorities

- `REP-PROD-001` — the live fallback builds its own SKU table without the allocator · P2
- `ARCH-007` — one canonical builder, one straggler · P1 mismatch
- `ARCH-003` — a Command Center widget reads a superseded app · P1 mismatch

## Method

This section follows [methodology.md](../methodology.md). Note especially, for
this section: **name the canonical implementation before recommending any
refactor** — `ARCH-007` and `ARCH-001` both live here, and the canonical SKU
builder has already been identified.

## Related sections

- [marketing](../marketing/README.md) — the ad cost this section shows
- `docs/financials/` *(pending)* — where revenue is reconciled to money
- `docs/walmart/` *(pending)* — the other "MCF"
