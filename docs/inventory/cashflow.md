# Cash flow planner

files: `apps/inventory_planning/{cashflow,views,models}.py`
       `templates/inventory_planning/cashflow.html`
verified against: `ad78c4b` · 2026-08-06

A forward-looking ledger per region: opening bank position, expected Amazon
inflows, container payments and running costs, on a dated running balance. It
answers **when the money runs short, and by how much.**

This is a **forecast**. Money that has already arrived belongs to Amazon Payouts
under Financials. The two are different machines that unfortunately share a name
— see `ARCH-005`.

## Purpose

The business pays factories months before Amazon pays us. A container ordered in
March is paid for around the port date in June and starts earning in July. That
gap is the single largest financial risk in the supply chain, and it is
invisible without a dated projection.

The planner exists to answer one question early enough to act on: *on the
current plan, what is the lowest the bank balance goes, and on what date.* A
negative low point is the signal to inject funds, delay a container, or slow
purchasing.

## Scope

Covers the per-region ledger: opening position, generated and manual entries,
and the running balance.

**Not covered:**
- payouts that have actually landed — [payouts.md](../financials/payouts.md), and `ARCH-005`
- where a container's FOB rate comes from — [allocation-workbench.md](allocation-workbench.md)
- what we owe suppliers in total — [suppliers.md](suppliers.md)

## Business workflow

```
Opening balance (per region, as at a date)
        + Amazon inflows      ← projected from real settlement history
        + funds injections    ← manual
        − container payments  ← generated: FOB + freight, dated on the port date
        − running costs       ← manual: HR, storage, duty, other
        = dated running balance → lowest point and its date
```

## Actors

| Actor | Does |
|---|---|
| Finance | sets the opening position, adds costs and injections, reads the low point |
| The system | generates container payments and inflow estimates, and refreshes them |

## Business rules

1. **One ledger per region, in that region's currency.** Container FOB is
   entered in the region's currency and nothing is ever converted, so figures
   are never summed across regions. See `INV-D-004`.
2. **A container's payment is its snapshotted FOB plus freight**, dated on the
   port ETA less the region's payment lead. See `INV-D-005`.
3. **Only active containers are planned for.** A received or cancelled container
   has been paid or abandoned, and never appears as a future outflow.
4. **Amazon inflows are estimated from real settlements, not from sales.** The
   projection uses the average of recent actual disbursements at their observed
   cadence — the money that hit the bank, not a run rate. See `INV-D-019`.
5. **The horizon is four payment cycles**, measured from the region's own
   observed cadence rather than a fixed number of days. A biweekly region plans
   about eight weeks ahead; a monthly one plans about four months.
6. **A human edit wins and is preserved.** Generated rows refresh freely; a row
   someone has edited is locked and never overwritten. See `INV-D-020`.
7. **Unpriced units are declared, not absorbed.** Where a container carries
   units with no rate anywhere, the row reports how many, because the payment is
   understated by exactly that much (`INV-CONT-001`).
8. **The lowest balance and its date are the output.** Not the closing balance —
   a ledger that ends positive can still go deeply negative in week three, and
   that is the week that matters.

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Set the opening position | finance | a region | balance and as-at date; the ledger starts here |
| Set the payment lead | finance | a region | container payments shift that many days before the port date |
| Refresh | finance | — | container payments and inflow estimates regenerated; locked rows untouched |
| Add an entry | finance | date, direction, amount | manual inflow or cost in the ledger |
| Edit a generated row | finance | — | the row locks, and refresh will not touch it again |

## System behaviour

- **Refresh regenerates, it does not accumulate.** Container rows are matched to
  their container and updated in place; rows whose container is no longer active
  are removed. Unlocked inflow estimates are discarded and rebuilt, because they
  are forward-looking guesses rather than records.
- **Settlement events are collapsed before they are averaged.** Amazon pays a
  large settlement and small top-ups a day or two later; those are one
  disbursement, and treating them as several would halve the apparent cadence.
- **Tiny off-cycle disbursements are excluded** from the average — reserve
  releases and partials would drag the typical settlement down and make the
  forecast pessimistic.
- **A received container is skipped at read time as well as at refresh**, so a
  ledger read between a receipt and the next refresh still cannot plan for a
  payment already made.

## Data model

- **Cash-flow plan** — one per region: the opening balance, its as-at date, and
  the payment lead in days.
- **Cash-flow entry** — one dated movement: direction, category, amount, an
  optional link to the container that generated it, whether it was generated or
  entered, and whether it is locked.
- The running balance is not stored; it is accumulated on every read.

## Edge cases

- **A container with no dates at all.** It cannot be placed on a timeline and is
  omitted from the forecast, so the projection is optimistic by its value. The
  container is still visible in [containers.md](containers.md).
- **A region with no payout history.** No inflows can be projected, so the
  ledger shows outflows only and its low point is worst-case.
- **Editing a generated container payment.** The row locks. Later changes to the
  container's FOB or freight no longer reach it — which is the point, but means
  a locked row can quietly go stale.
- **Every container priced at zero.** The ledger looks healthy while the real
  position is not. This is the state the development snapshot is in, and why
  rule 7 exists (`INV-CONT-001`).

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INV-CONT-001` | in-transit lines carry no FOB, so containers price at zero | legacy data |
| `INV-CASH-001` | opening-balance backlog never reaches the forecast | blocked on a business decision |

## Architecture mismatches

`ARCH-005` — "Cash flow" names this forecast **and** the Financials payouts
page. Both are wanted; only the shared name is wrong. This document is the
**Cash Flow Planner**; the other is **Amazon Payouts**.

## Related decisions

`INV-D-004` `INV-D-005` `INV-D-019` `INV-D-020`

## Related documents

- [containers.md](containers.md) — what generates each outflow
- [allocation-workbench.md](allocation-workbench.md) — where the FOB rate is captured
- [suppliers.md](suppliers.md) — total owed, as distinct from dated payments
