# Brand Analytics

What shoppers search for, what they buy alongside our products, and how often
they come back. Amazon's own view of demand around our listings — **earned**,
not paid.

## Purpose

Advertising tells us what our own campaigns did. Brand Analytics tells us what
the market did: which search terms lead to our category, how our share of those
terms moves, what customers put in the same basket, and whether they return.

It is the evidence behind product and keyword decisions that advertising data
alone cannot support.

**This section is complete and frozen** except for future feature changes. Two
features are documented; the process lessons are in
[RETROSPECTIVE.md](RETROSPECTIVE.md).

## The boundary with Marketing

[Marketing](../marketing/README.md) is **paid** — what we spent and what it
returned. This section is **earned** — what happened in Amazon's search results
regardless of spend. Both mention search terms and both are correct; they are
different populations measured for different reasons.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Search Query Performance | per week, per ASIN | search terms, impressions, clicks, purchases and our share of each |
| Market Basket | per week, per ASIN | what else customers bought in the same basket |
| Repeat Purchase | per week, per ASIN | how much of demand is returning customers |

All are **weekly** and **per ASIN** — Amazon retired the brand-aggregate variants,
so every report is submitted per ASIN per week. Reports are requested for ASINs
that actually sold in the last thirty days, because submitting for dormant SKUs
burns quota and returns nothing.

## Features

| Document | Covers | Open here when |
|---|---|---|
| [search-queries.md](search-queries.md) | search-term performance **and share** | a term's share or volume looks wrong |
| [baskets.md](baskets.md) | basket affinity and repeat purchase | a basket association looks wrong |

**Two documents, not three.** The map planned a separate market-share leaf;
market share is *derived from the search-query rows* rather than separately
reported, so it belongs with them. One table, one machine, one document.

## Ground truth

*Source: local development data; provisional. Nothing runs on a schedule here.*

| | Local |
|---|---|
| Search query rows | 940, one week (2026-05-31) |
| Market basket rows | 296, same week |
| Repeat purchase rows | 106, same week |
| Item comparison rows | 0 — **Amazon deprecated the report** |
| Brand share rows | 0 — **nothing writes this table**, see `BA-002` |
| Sync log | 12 collected, 32 still pending — see `BA-001` |
| `apps/sqp` | 0 rows in all three tables — superseded, `ARCH-003` |

## Architecture mismatches

`ARCH-003` — Search Query Performance is implemented twice. **The canonical
implementation is already identified in that entry**: the dashboard's BA models,
which hold the data and serve the pages. `apps/sqp` is 1,934 lines holding no
rows, and a Command Center widget reads it, so that widget renders empty and
reads as a data problem rather than a dead dependency.

## Navigation

| Working on… | Load |
|---|---|
| a search term's share | `CLAUDE.md` · this README · [search-queries.md](search-queries.md) · `gaps.md` |
| a basket association | `CLAUDE.md` · this README · [baskets.md](baskets.md) · `gaps.md` |

## Related sections

- [marketing](../marketing/README.md) — the paid view of search
- [reporting](../reporting/README.md) — what actually sold
