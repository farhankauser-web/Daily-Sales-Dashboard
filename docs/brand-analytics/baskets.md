# Baskets and repeat purchase

files: `apps/dashboard/views.py` — `ba_baskets`
       `apps/amazon_api/management/commands/ingest_brand_analytics.py`
verified against: `82744aa` · 2026-08-06

What customers bought alongside our products, and how much of our demand comes
from people buying again.

## Purpose

Two questions that neither sales nor advertising data can answer. *What else is
in the basket* points at bundles, cross-sells and competitors chosen alongside
us. *How much is repeat* says whether a product earns a customer or merely wins
a transaction — which changes what it is worth paying to acquire one.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Market Basket | week × ASIN | products purchased in the same basket, with their frequency |
| Repeat Purchase | week × ASIN | orders and sales from returning customers |

Both weekly and per ASIN, from the same ingestion as
[search-queries.md](search-queries.md). See `BA-D-001`.

## Business rules

1. **A basket association is a co-purchase, not a recommendation.** Amazon
   reports what was bought together; why is a commercial judgement.
2. **Associations include competitors.** A rival towel in the same basket is a
   finding about the customer's decision, not a data error.
3. **Repeat purchase is measured by Amazon**, which knows the customer identity
   we do not. It cannot be derived from our own order data.
4. **Both are per ASIN per week**, so a product with no sales in a week has no
   basket and no repeat data — absence is a consequence of not selling.

## Edge cases

- **A product with few sales.** Basket associations from a handful of orders are
  noise; the frequency column is what separates a pattern from a coincidence.
- **A week with no data for an ASIN.** It did not sell, or the report did not
  arrive. The collection state distinguishes them (`BA-001`).

## Observations — not gaps

*Source: local development data; provisional.* 296 basket rows and 106 repeat
rows for one week. Item Comparison shows zero rows, and that is correct —
**Amazon deprecated the report**, and it was removed from the ingestion
configuration while being retained in the completeness list so historical rows
still validate.

## Related decisions

`BA-D-001`

## Related documents

- [search-queries.md](search-queries.md) — the demand side of the same ingestion
- [reporting/product-performance.md](../reporting/product-performance.md) — what those products earn
