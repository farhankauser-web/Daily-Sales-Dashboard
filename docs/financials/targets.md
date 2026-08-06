# Targets

files: `apps/dashboard/models.py` — `MonthlyTarget`, `ProductTypePackMonthlyTarget`
       `apps/amazon_api/views.py` — target payload for the dashboard
verified against: `9227433` · 2026-08-06

Monthly revenue targets per product group, and the comparison of actual
performance against them.

## Purpose

A month's revenue is only meaningful against what it was supposed to be. Targets
are how the business states that intent in advance, per product group, so that
mid-month performance can be judged rather than merely observed.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Target upload | per marketplace, month, product type and pack size | the revenue target |

Set by the business. Nothing derives a target from history — that would make the
target follow performance rather than lead it.

## Business rules

1. **A target is monthly and is pro-rated to whatever window is being viewed.**
   A monthly target shown against seven days is that target ÷ days in month ×
   7. See `FIN-D-007`.
2. **Targets are set per product group**, at the same product-type and pack-size
   grain the SKU table groups by, because that is the level buying decisions are
   made at.
3. **Matching ignores pack-size formatting.** "2", "2-Pack" and "Pack of 2" are
   the same group; a target must not be missed because of how it was typed.
4. **A group with no target shows none**, rather than a zero — an absent target
   and a target of zero are different statements.

## Edge cases

- **A part-month view.** Pro-rated, by rule 1, so a month-to-date comparison is
  fair rather than flattering.
- **A product group created mid-month with no target.** Shows actuals without a
  comparison.

## Observations — not gaps

*Source: local development data; provisional.* 156 product-group targets exist;
the marketplace-level target table is empty. The two are independent, and the
group-level one is what the dashboard reads.

## Related decisions

`FIN-D-007`

## Related documents

- [reporting/daily.md](../reporting/daily.md) — where targets are shown against actuals
- [reporting/historical.md](../reporting/historical.md) — target comparison over a range
