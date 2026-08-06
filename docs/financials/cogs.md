# Cost of goods

files: `apps/dashboard/cogs_recalc.py` · `apps/dashboard/views.py` — `cogs`
       `templates/dashboard/cogs.html`
verified against: `9227433` · 2026-08-06

What each unit cost us to buy and land. The single number every margin in the
application is measured against.

## Purpose

Revenue is reported by Amazon; cost is not. Every margin, every profitable-SKU
judgement, every campaign that looks worth scaling rests on a figure the
business supplies itself.

That makes correcting it consequential. A COGS upload does not change one
number — it changes every margin already computed from the old one, across
several views and time ranges.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| COGS upload | per SKU, effective from a month | unit cost |
| FBA fee rate upload | per SKU, effective from a date | the modelled fulfilment fee |

Both are **uploaded by the business**, not fetched. Amazon supplies neither, and
neither is derivable from anything Amazon sends.

A cost is **effective from a month**, so a mid-year price change applies forward
without restating history.

## Business rules

1. **Cost of goods is a business input, never inferred.** A SKU with no cost
   record has no margin, and is shown as such rather than assumed to cost zero.
2. **A cost is effective from a month and applies forward.** Correcting a past
   month's cost restates that month deliberately; it does not silently restate
   every month.
3. **Correcting COGS restates every figure derived from it**, in every storage
   layer, for the affected month. See `FIN-D-004`.
4. **The recalculation formulas mirror the original write path exactly.** A
   restated margin and a freshly computed one must be identical, or the
   correction introduces a discrepancy of its own.
5. **Cost of goods for the P&L is (order units − refund units) × unit cost**,
   which is how the business calculates it — refunded units cost nothing because
   the goods came back.
6. **Missing costs are reported, not hidden.** The set of SKUs with no cost
   record is exportable, because filling it is an operational task.

## What a correction touches

A COGS upload for one month restates, in order: the P&L's cost line; per-SKU
daily margins; daily totals and the margins derived from them; hourly figures
within their retained window; and campaign profit for the month.

Every one of those is a stored figure rather than a live derivation, which is
what makes the restatement necessary — and what makes rule 4 load-bearing.

## Edge cases

- **A SKU sold before its cost was uploaded.** Margin is absent until the cost
  arrives, then restated by the recalculation.
- **A cost corrected for a month already settled in the P&L.** The cost line is
  restated; the settled revenue and fee lines are not, because those are
  Amazon's figures and not ours to change.
- **A refund in a later month than its sale.** Units net off in the month the
  refund posted, matching the P&L's posted-date basis.

## Observations — not gaps

*Source: local development data; provisional.*

- **613 cost entries exist and no FBA fee rates do.** The two uploads are
  independent; an empty fee-rate table means that upload has not been run here,
  and it is what fee-drift.md compares against.

## Known gaps

*None filed here.* `FIN-001` — referral fee computed on gross revenue, never
checked against a settlement — is carried in the root index and belongs to
[fee-drift.md](fee-drift.md), which is the document that would prove it.

## Related decisions

`FIN-D-004`

## Related documents

- [pnl.md](pnl.md) — where the cost line lands
- [fee-drift.md](fee-drift.md) — whether the modelled fees match reality
- [product-performance.md](../reporting/product-performance.md) — the margins restated
