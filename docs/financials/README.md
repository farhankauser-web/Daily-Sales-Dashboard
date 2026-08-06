# Financials

What Amazon financially recognised and settled. The management P&L, the cost
base beneath it, fee reconciliation, targets, and the money that actually
arrived.

## Purpose

This section answers **"what did Amazon financially recognise and settle?"** —
the numbers that tie to the books, reconcile to the bank, and survive an
accountant's questions.

Its source is Amazon's **Flat File V2 / transaction reporting via SP-API**:
settlement reports, the unified transaction report, and the Finances API. Those
are posted-date, settlement-grade records of money.

## The boundary with Reporting — intentional, not duplication

[Reporting](../reporting/README.md) and this section both expose **Revenue**,
**Profit** and **Margin**. They are **different measurements of the same trade**
and must not be merged.

| | Reporting | Financials |
|---|---|---|
| Source | daily order reports | Flat File V2 / transaction reporting |
| Grain | order date | posted / settled date |
| Question | *what is happening in the business?* | *what did Amazon recognise and settle?* |
| Optimised for | trends, KPIs, hourly shape, monitoring | reconciliation, accounting, profitability |
| Settles? | no — operational, immediate | yes — lags, then is final |

A figure will differ between them, and that difference is **information**: it is
the gap between what was sold and what Amazon has recognised. Anyone
reconciling the two should expect it rather than treat it as an error.

Where a term appears in both, each section documents it in its own context and
names its source. See the domain-boundary rule in
[methodology.md](../methodology.md).

## Features

| Document | Covers | Open here when |
|---|---|---|
| pnl.md *(pending)* | the management P&L statement | a month's profit looks wrong |
| cogs.md *(pending)* | the cost base — COGS upload, FBA rates, recalculation | a product's cost is wrong or missing |
| fee-drift.md *(pending)* | settlement actuals against uploaded fee assumptions | Amazon charged more than we modelled |
| payouts.md *(pending)* | money that actually arrived · `ARCH-005` | a disbursement is missing |
| targets.md *(pending)* | monthly revenue and margin targets | a target comparison looks wrong |

## Relationships

```
settlement reports · unified transaction report · Finances API
                    ↓
        settled line actuals (per month, per line)
                    ↓
   management P&L  ←  COGS  ←  fee rates
                    ↓
        consolidated across regions via FX
```

Two facts about this shape cause most confusion:

- **The P&L falls back to operational data during the settlement lag**, and says
  so per line. A recent month is not blank; it is provisional and labelled.
- **"Cash flow" names two machines.** This section's payouts view is money that
  *arrived*; Inventory's is a *forecast* of money leaving. `ARCH-005`.

## Ground truth

Established before writing. *Source: local development data; provisional.*

**The laptop runs no scheduled jobs — by design.** Staleness and empty tables
are expected here and are never on their own evidence of a defect.

| Table | Local state |
|---|---|
| Settled line actuals | 186 rows, 2026-04 → 2026-07, all four marketplaces |
| — by source | unified 136 · Finances API 39 · settlement report 11 |
| Settlement reports | 12 |
| Amazon payouts | 24, 2026-05-01 → 06-26 |
| COGS entries | 613 |
| Manual P&L entries · FX rates · FBA fee rates | 0 each |

The three-way source mix is the finding worth carrying into `pnl.md`: three
pipelines write the same table and something has to decide which wins.

## Navigation

| Working on… | Load |
|---|---|
| a month's profit | `CLAUDE.md` · this README · pnl.md *(pending)* · `gaps.md` |
| a product's cost | `CLAUDE.md` · this README · cogs.md *(pending)* · `gaps.md` |
| a fee that looks wrong | `CLAUDE.md` · this README · fee-drift.md *(pending)* · `gaps.md` |

## Method

Follows [methodology.md](../methodology.md). For this section especially:
**apparent overlap with Reporting is intentional** — establish that two machines
answer different questions from different sources before treating anything as
duplication.

## Related sections

- [reporting](../reporting/README.md) — the operational view of the same trade
- [inventory](../inventory/README.md) — the cash-flow *forecast*, and `ARCH-005`
- [marketing](../marketing/README.md) — the ad cost the P&L consumes
