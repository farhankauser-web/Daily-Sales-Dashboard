# Campaigns

files: `apps/dashboard/views.py` — `api_campaigns_list`, `api_campaign_detail` and the campaign APIs
       `apps/dashboard/management/commands/compute_campaign_profit.py`
       `templates/dashboard/campaigns*.html`
verified against: `2a7a3ab` · 2026-08-06

Every campaign, with what it cost, what it earned, and **what it made** — the
only place in the section where advertising meets margin.

## Purpose

ACoS and ROAS say whether a campaign returned more revenue than it spent. Neither
says whether it made money: a campaign at 20% ACoS on a product with a 15% margin
is losing on every sale. Answering that needs the cost of goods, the referral fee
and the fulfilment fee for the SKUs the campaign actually sold.

This is where that is computed, once, nightly, so the page can be read rather
than derived.

## Scope

Covers the campaign list and its profit measures, the per-campaign detail views,
and the nightly profit computation behind them.

**Not covered:**
- how spend reaches a SKU — [sku-allocation.md](sku-allocation.md)
- search-term and placement breakdowns — [search-terms.md](search-terms.md), [placements.md](placements.md)
- where the underlying figures come from — [ads-api.md](ads-api.md), [ams-stream.md](ams-stream.md)

## Business workflow

```
advertised-product rows (campaign × ASIN × SKU)
        + per-SKU revenue, COGS, referral fee, fulfilment fee
        + campaign totals, marketplace totals
                    ↓  nightly
        campaign profit per day  →  the Campaign Performance Center
```

## Business rules

1. **Profit is measured, not inferred from ACoS.** Contribution margin is
   attributed revenue less cost of goods, referral fee, fulfilment fee and ad
   spend. A campaign can have a strong ACoS and negative profit.
2. **Attribution comes from Amazon's advertised-product report** — the campaign,
   ASIN and SKU Amazon says it charged for — not from this section's own
   allocation. The two answer different questions and this one has Amazon's
   answer available. See `ARCH-009`.
3. **TACoS is spend over *total* marketplace revenue**, not over attributed
   revenue. It answers "how much of the business is advertising paying for",
   which is a different question from ACoS.
4. **Every profit figure declares its coverage.** Attribution coverage is the
   share of a campaign's sales that matched to a SKU we hold costs for. A low
   figure means the margin is resting on fallbacks. See `MKT-D-009`.
5. **Where a SKU's own costs are unknown, a fallback margin is used** — referral
   percentage only, without SKU-specific cost of goods or fulfilment fee — and
   the coverage figure falls accordingly.
6. **Today is shown without profit.** Detailed advertising data is available
   through yesterday; today's view carries live spend and clicks, states plainly
   that profit and margin will appear once yesterday lands, and shows no
   estimate. See `MKT-D-007`.
7. **Margins are measured on revenue ex-VAT**, as everywhere in Pulse.
8. **Breakdowns beneath a campaign inherit its margin rate.** Amazon attributes
   cost of goods to a campaign, never to a search term or a placement, so profit
   at those levels is that row's sales at the campaign's contribution-margin
   rate, less its spend. It is a proxy, and every view that shows it says so.
   See `MKT-D-011`.

## States

A campaign's figures come from one of two sources depending on the window:

| Window | Source | Carries |
|---|---|---|
| Today | live campaign totals | spend, impressions, clicks — no profit |
| Yesterday and earlier | the nightly profit rows | the full picture |

## User actions

| Action | Who | Result |
|---|---|---|
| Filter by period | anyone | today, yesterday, 7 days, 30 days or month-to-date |
| Filter by ad product, status or brand | anyone | narrows the table |
| Open a campaign | anyone | daily trend, top SKUs, targeting, search terms and hourly detail |

## System behaviour

- **Profit is computed nightly, after the detail reports have landed**, and
  re-computed for a window of recent days as late attribution arrives.
- The campaign list reads the pre-computed rows rather than deriving profit per
  request — the arithmetic spans four sources and is not a page-load operation.
- **Contribution to profit** is each campaign's share of the window's total, so
  the table sorts by what actually moves the business rather than by spend.
- A campaign present in the reports but not in the campaign dimension still
  appears, keyed by its id.

## Data model

- **Campaign** — the dimension: id, name, ad product, portfolio. Names change;
  the id does not.
- **Campaign profit day** — one per marketplace, date and campaign: spend,
  attributed revenue, units and orders, the three attributed cost components,
  contribution margin, gross profit, margin percentage, TACoS, and the coverage
  that qualifies them.
- **Search-term summary** — per campaign per day, the counts behind the
  search-term signals. See [search-terms.md](search-terms.md).

## Edge cases

- **A campaign advertising a SKU we hold no costs for.** Profit is computed on
  the fallback margin and coverage drops — the number is still shown, with the
  caveat attached to it rather than in a footnote.
- **A campaign with spend and no attributed sales.** Profit is negative by the
  full spend, which is correct: the money was spent and nothing came back.
- **A renamed campaign.** Its history follows the id, so the trend does not break.
- **Sponsored Brands and Display attribution arriving weeks late.** Profit for a
  recent day rises as it lands. See `MKT-D-006`.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `MKT-CAMP-001` | attribution coverage is computed and shown, but nothing flags a campaign whose profit rests mostly on fallbacks | missing implementation |

## Architecture mismatches

`ARCH-009` — this engine reads the canonical advertised-product table while
[sku-allocation.md](sku-allocation.md) reads the superseded copy. Two consumers,
two generations of the same data; one has already migrated.

## Related decisions

`MKT-D-006` `MKT-D-007` `MKT-D-009`

## Related documents

- [sku-allocation.md](sku-allocation.md) — the other attribution path, and why it differs
- [search-terms.md](search-terms.md) · [placements.md](placements.md) — the breakdowns beneath a campaign
- [ads-api.md](ads-api.md) — where the attributed rows come from
