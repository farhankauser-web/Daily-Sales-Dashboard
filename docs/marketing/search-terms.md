# Search terms

files: `apps/dashboard/views.py` — `api_search_terms`, `api_search_term_detail`
       `apps/dashboard/management/commands/compute_campaign_profit.py` — the per-campaign summary
       `templates/dashboard/search_terms.html`
verified against: `2a7a3ab` · 2026-08-06

What shoppers actually typed, what it cost, and which terms are worth keeping.
The layer where advertising is tuned rather than measured.

## Purpose

A campaign is a budget; a search term is a decision. Every term is a candidate
to bid harder on, bid down, or exclude outright, and there are tens of thousands
of them. This page exists to make that set actionable — to surface the handful
worth touching this week out of a fact table that can exceed 100,000 rows a day.

That means the useful output is not the numbers but the **signals**: terms
spending with no sales, terms attracting clicks that do not convert, terms
losing money, terms worth scaling.

## Scope

Covers the search-term rollup, its signals, and the per-term detail.

**Not covered:**
- campaign-level profit — [campaigns.md](campaigns.md)
- targeting and keyword bids — not documented; Pulse reports, it does not bid
- where the rows come from — [ads-api.md](ads-api.md)

## Business rules

1. **A search term is what the shopper typed**, not what we bid on. The two
   differ constantly, and the gap between them is where wasted spend lives.
2. **Sponsored Display has no search-term data.** Amazon does not offer the
   report; only Sponsored Products and Brands appear.
3. **Terms are signalled, not just listed.** Five signals are computed per
   campaign per day — high spend with no sales, high click-through with low
   conversion, losing money, scaling opportunity, and high profit — so the table
   can be filtered to what needs a decision. See `MKT-D-010`.
4. **Profit per term is a proxy**, inheriting the campaign's margin rate — the
   rule and its reasoning live in [campaigns.md](campaigns.md); `MKT-D-011`.
5. **The rollup is sorted and limited on the server.** The underlying table is
   too large to ship whole, so a window returns the top rows by the chosen
   measure rather than everything.
6. **Signal thresholds are fixed and shared.** Every campaign is judged on the
   same lines, so the counts are comparable across campaigns.

## Signals

| Signal | Means | Decision it prompts |
|---|---|---|
| High spend, no sales | spend above the threshold with zero orders | exclude, or bid down |
| High CTR, low CVR | the ad is attractive, the listing is not | fix the listing, not the bid |
| Losing money | sales minus spend below the floor | bid down or exclude |
| Scaling opportunity | strong return at meaningful spend | bid up |
| High profit | the same, from the profit side | protect it |

The thresholds are deliberate business judgements rather than derived values,
and are stated in `MKT-D-010`.

## User actions

| Action | Who | Result |
|---|---|---|
| Choose a window | anyone | yesterday, 7 days, 30 days or month-to-date |
| Filter by signal | anyone | only terms carrying at least one selected signal |
| Scope to one campaign or ad product | anyone | narrows the rollup |
| Sort by spend, sales, profit, impressions, clicks or orders | anyone | server-side, so the limit is applied to the right rows |
| Open a term | anyone | its history and the campaigns it ran in |

## System behaviour

- Signal **counts** per campaign are computed nightly alongside campaign profit,
  so the campaign table can show how many terms need attention without scanning
  the fact table.
- The **rows** are aggregated per request over the chosen window, because the
  window is user-selected and cannot be pre-computed for every combination.
- Where campaign profit has not yet been computed for a day, the proxy margin is
  unavailable and profit for that day's terms reads as spend only — the same
  suppression rule as everywhere else (`MKT-D-007`).

## Data model

- **Search-term day** — one per marketplace, date, ad product, campaign, ad
  group, target and term: impressions, clicks, spend, orders, sales, units and
  the derived rates.
- **Search-term summary** — per campaign per day: how many distinct terms, and
  how many carry each signal, plus the wasted spend behind the first one.

## Edge cases

- **The same term in several campaigns.** Aggregated across them in the rollup
  and separable in the detail, because the decision differs — a term may be
  profitable in one campaign and not another.
- **A term with clicks and no impressions**, or other Amazon reporting
  artefacts. Stored as reported; the rates simply read as undefined.
- **A campaign with no profit row for the day.** Its terms show no profit rather
  than a zero, so a missing computation is not mistaken for a break-even term.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `MKT-TERM-001` | signal thresholds are fixed in code and identical across marketplaces and ad products | missing implementation |

## Related decisions

`MKT-D-007` `MKT-D-010`

## Related documents

- [campaigns.md](campaigns.md) — the margin rate the proxy profit inherits
- [ads-api.md](ads-api.md) — where the rows come from
- [placements.md](placements.md) — the other breakdown beneath a campaign
