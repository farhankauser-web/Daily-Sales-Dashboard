# Placements

files: `apps/dashboard/views.py` — `api_placements`
       `templates/dashboard/placements.html`
verified against: `2a7a3ab` · 2026-08-06

Where on Amazon the ad appeared — top of search, rest of search, or a product
page — and whether that position earned its premium.

## Purpose

Amazon charges very differently by position. Top of search costs a multiple of a
product-page placement and converts at a different rate, and the bid adjustment
that controls it is set per campaign. Without a placement view the adjustment is
set on instinct.

This page answers one question per placement: **is the extra cost of this
position returning more than it costs?**

## Scope

Covers the account-level placement rollup and the per-campaign placement mix.

**Not covered:**
- campaign-level profit — [campaigns.md](campaigns.md)
- search terms — [search-terms.md](search-terms.md)
- setting bid adjustments — Pulse reports; the change is made in Amazon

## Business rules

1. **Only Sponsored Products reports placement.** Amazon offers no placement
   report for Brands or Display, so the page is Sponsored Products throughout.
2. **Every placement is shown, including those with no rows.** A placement with
   zero impressions is a finding — it usually means the bid adjustment has
   pushed it out entirely — so it appears at zero rather than vanishing.
3. **Placements keep a fixed display order** rather than sorting by size, so the
   same position is in the same row every time and the mix is read at a glance.
4. **Profit per placement is a proxy**, inheriting the campaign's margin rate —
   the rule and its reasoning live in [campaigns.md](campaigns.md); `MKT-D-011`.
5. **Share of spend and share of sales are shown together.** A placement taking
   40% of spend for 20% of sales is the finding; either figure alone is not.

## User actions

| Action | Who | Result |
|---|---|---|
| Choose a window | anyone | yesterday, 7 days, 30 days or month-to-date |
| Scope to one campaign | anyone | that campaign's placement mix |
| Read the campaign mix table | anyone | the largest campaigns by spend, split by placement |

## System behaviour

- The rollup aggregates per request over the chosen window, joined to campaign
  profit for the margin rate.
- Where a campaign has no profit row for a day, its placements contribute spend
  and sales but no profit, rather than a zero (`MKT-D-007`).

## Data model

- **Placement day** — one per marketplace, date, ad product, campaign and
  placement: impressions, clicks, spend, orders, sales and the derived rates.

## Edge cases

- **A placement with spend and no sales.** Shown with negative proxy profit,
  which is correct — the position cost money and returned nothing.
- **A campaign whose bid adjustment excludes a placement.** That placement reads
  zero across the board, which is the intended signal rather than missing data.
- **Amazon reclassifying a placement.** Historical rows keep the classification
  they were reported under; the page does not restate history.

## Observations — not gaps

*Source: local development data; provisional.*

- **Placement rows exist only for Sponsored Products**, and the sync log shows
  sustained Sponsored Brands placement failures. Those failures are historical
  residue from a report kind Amazon does not offer, already removed — see the
  observations in [ads-api.md](ads-api.md). The absence of Brands placement data
  is a limitation of Amazon's reporting, not a gap in ours.

## Related decisions

`MKT-D-007`

## Related documents

- [campaigns.md](campaigns.md) — the margin rate the proxy profit inherits
- [search-terms.md](search-terms.md) — the other breakdown beneath a campaign
- [ads-api.md](ads-api.md) — where the rows come from, and why Brands has none
