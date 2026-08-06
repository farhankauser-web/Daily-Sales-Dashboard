# Inventory

Everything between placing an order with a factory and the units being sellable
on Amazon: what we owe suppliers, what is being made, what is on the water, what
Amazon has counted in, and what we will run out of.

## Purpose

This section owns the **physical and financial supply chain for our own brand**
— suppliers, purchase orders, production, containers, receiving, warehouse stock
and the region cash-flow forecast.

It does not own the B2B business. Quotes, RFQs, customer orders and invoices
belong to the **supply-chain** section *(pending)*, which is Atlas. The naming is
confusing and worth remembering: in this app, *Supply Chain* means B2B.

It does not own money that has already arrived. Amazon payouts and the
marketplace balance belong to **financials** *(pending)*; this section forecasts
what we will pay out (`ARCH-005`).

## Features

| Document | Covers | Open here when |
|---|---|---|
| [containers.md](containers.md) | container lifecycle, statuses, in transit, history | a container is in the wrong place, or units are miscounted |
| receiving.md *(pending)* | Amazon's count vs our packing list, shortfall | Amazon received less than we shipped |
| allocation-workbench.md *(pending)* | packing list → container, PO drawdown | an upload is refused or allocates wrongly |
| suppliers.md *(pending)* | suppliers, opening balance | supplier balances look wrong |
| purchase-orders.md *(pending)* | PO workbook, production plans, balances | a PO import misbehaves |
| planner.md *(pending)* | projection, runway, reorder, loading plan | cover days or reorder dates look wrong |
| transfers.md *(pending)* | FBA transfers, goods-receipt variance | stock moves between our own locations |
| cashflow.md *(pending)* | the region ledger — forecast | a container's payment is missing or wrong |

## Relationships

```
Supplier → Purchase order → Production plan
                                  ↓
                          Allocation workbench
                                  ↓
                    Container ── in transit ──→ Receiving ──→ History
                                  ↓                 ↓
                            Cash flow          Warehouse stock
                                                    ↓
                                                 Planner
```

Two relationships are not obvious from the chain and cause most confusion:

- **A container belongs to no supplier.** Ownership lives on each line, via the
  PO line the units were drawn from, so one container routinely carries goods
  from two factories.
- **Receiving is a stage, not a page.** A container moves into it the moment
  Amazon counts the first unit, whatever its status field says.

## Navigation

| Working on… | Load |
|---|---|
| a container in the wrong tab | `CLAUDE.md` · this README · [containers.md](containers.md) · `gaps.md` |
| a shortfall or Amazon's count | `CLAUDE.md` · this README · receiving.md *(pending)* · [containers.md](containers.md) · `gaps.md` |
| a packing-list upload | `CLAUDE.md` · this README · allocation-workbench.md *(pending)* · purchase-orders.md *(pending)* · `gaps.md` |
| container payments | `CLAUDE.md` · this README · cashflow.md *(pending)* · [containers.md](containers.md) · `gaps.md` |

## Current priorities

- `INV-CONT-001` — 188 in-transit lines carry no FOB, so cash flow prices them at zero · P1
- `INV-CONT-002` — opening balance is not consumable; packing lists never draw it down · P1
- `INV-SUP-001` — opening balance has no rate, so Outstanding FOB understates · P2

## Related sections

- `docs/financials/` *(pending)* — payouts received, P&L, COGS
- `docs/reporting/` *(pending)* — sell-through that drives the planner
- `docs/supply-chain/` *(pending)* — Atlas, the B2B arm. Not this.
