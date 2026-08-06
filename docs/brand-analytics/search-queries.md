# Search queries and share

files: `apps/dashboard/views.py` — `ba_queries`, `ba_market_share`, `ba_share_trend`
       `apps/amazon_api/management/commands/ingest_brand_analytics.py`
verified against: `82744aa` · 2026-08-06

What shoppers typed in Amazon's search box, how our listings performed against
those terms, and what share of them we took.

## Purpose

A search term with high volume where we take 2% of clicks is an opportunity. The
same term where we take 40% is a position to defend. Neither is visible from
advertising data, because most of that traffic is not paid.

This is the demand picture around our products, weekly, from Amazon's own
measurement.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Search Query Performance | week × ASIN × query | query volume, and our impressions, clicks and purchases within it |

**Market share is derived from these rows, not separately reported.** Our share
of a query is our clicks or purchases against the query's total — one table
answers both questions, which is why they are one document.

## Business rules

1. **The week is Amazon's week**, Sunday to Saturday. A report requested for any
   other window is rejected by Amazon rather than silently reshaped.
2. **Everything is per ASIN.** Amazon retired the brand-aggregate reports, so a
   product-level view is an aggregation we perform, never one Amazon supplies.
   See `BA-D-001`.
3. **Share is a ratio within a query**, not across queries. Our share of "bath
   towels" says nothing about our share of the category.
4. **A query we do not appear in does not appear in our data at all.** Absence is
   invisible here, which is the most important limitation of the dataset: it
   measures how we do where we show up, not where we fail to.
5. **Impressions, clicks and purchases are Amazon's counts**, not ours, and will
   not tie to advertising figures — that data covers paid placements only.

## Edge cases

- **A newly launched ASIN.** No history, so no trend; the first week is a
  baseline rather than a movement.
- **A query where we rank but do not sell.** High impressions, low purchases —
  the signal that the listing rather than the ranking is the problem.
- **A week where the report never arrived.** That week is simply absent from the
  trend, which is why the collection state matters (`BA-001`).

## Observations — not gaps

*Source: local development data; provisional.* 940 rows for a single week,
2026-05-31. One week is enough to confirm ingestion works and not enough for a
trend — the scheduled collection runs in production.

## Related decisions

`BA-D-001`

## Related documents

- [baskets.md](baskets.md) — what else those customers bought
- [marketing/search-terms.md](../marketing/search-terms.md) — the **paid** view, and why it differs
