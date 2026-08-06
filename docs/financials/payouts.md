# Amazon payouts

files: `apps/dashboard/views.py` — `cash_flow`
       `apps/dashboard/models.py` — `AmazonPayout`
verified against: `9227433` · 2026-08-06

Money that actually arrived in the bank from Amazon, per marketplace. Actuals,
not a forecast.

## Purpose

The P&L says what was recognised. This says what was **paid**, and when. The two
differ by Amazon's reserve, its settlement cycle and any deductions it applied
at disbursement — and the difference is a real question the business asks:
*Amazon recognised this in June; where is it?*

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Amazon disbursements | per payout event | money received, and its date |

One source, no precedence to resolve. A payout is a fact.

## Business rules

1. **A payout is money received, not revenue.** It is never a P&L line — the
   revenue it settles was recognised when Amazon posted it. See `FIN-D-006`.
2. **Payouts are per marketplace, in that marketplace's currency**, and are not
   summed across regions without conversion.
3. **A disbursement covers a settlement period, not a calendar month.** Payout
   totals and monthly P&L revenue will not agree, and are not meant to.
4. **Small same-cycle top-ups belong to their settlement.** Amazon commonly
   disburses a large amount and one or two small adjustments a day or two later;
   they are one event.

## Naming

**"Cash flow" names two different machines.**

| | Owns | Answers |
|---|---|---|
| **Amazon Payouts** — this document | money that arrived | *what has Amazon paid us?* |
| **Cash Flow Planner** — [inventory/cashflow.md](../inventory/cashflow.md) | a forward ledger | *when will we run short?* |

Both are wanted; only the shared name is wrong. `ARCH-005` records it. This is
also why the Inventory forecast projects inflows from *these* actuals rather
than from sales — see `INV-D-019`.

## Edge cases

- **A marketplace with no payout history.** Renders empty. It also means the
  Inventory cash-flow forecast can project no inflows for that region — the two
  facts share a cause.
- **A payout covering a period spanning two months.** Belongs to neither month
  cleanly, which is why payouts are not reconciled to monthly P&L revenue.

## Observations — not gaps

*Source: local development data; provisional.* 24 payouts, 2026-05-01 →
06-26. The sync that records them runs only in production.

## Architecture mismatches

`ARCH-005` — "Cash flow" names this actuals view and the Inventory forecast.
The recommendation is to rename the pages, not to merge the machines.

## Related decisions

`FIN-D-006` · and from Inventory: `INV-D-019`

## Related documents

- [pnl.md](pnl.md) — what was recognised, as against what was paid
- [inventory/cashflow.md](../inventory/cashflow.md) — the forecast, and `ARCH-005`
