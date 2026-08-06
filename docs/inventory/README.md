# Inventory

Everything between placing an order with a factory and the units being sellable
on Amazon: what we owe suppliers, what is being made, what is on the water, what
Amazon has counted in, and what we will run out of.

## Purpose

This section owns the **physical and financial supply chain for our own brand**
— suppliers, purchase orders, production, containers, receiving, warehouse stock
and the region cash-flow forecast.

It does not own the B2B business. Quotes, RFQs, customer orders and invoices
belong to the **[supply-chain](../supply-chain/README.md)** section, which is Atlas. The naming is
confusing and worth remembering: in this app, *Supply Chain* means B2B.

It does not own money that has already arrived. Amazon payouts and the
marketplace balance belong to **[financials](../financials/README.md)**; this section forecasts
what we will pay out. Both are called "cash flow" today — `ARCH-005`.

**This section is complete and frozen** except for future feature changes.
All ten features are documented; the registers below are the backlog, not
unwritten work. The process lessons are in
[RETROSPECTIVE.md](RETROSPECTIVE.md) — read that before starting a new
section.

## Features

| Document | Covers | Open here when |
|---|---|---|
| [containers.md](containers.md) | container lifecycle, statuses, in transit, history | a container is in the wrong place, or units are miscounted |
| [receiving.md](receiving.md) | Amazon's count vs our packing list, shortfall | Amazon received less than we shipped |
| [allocation-workbench.md](allocation-workbench.md) | packing list → container, PO drawdown | an upload is refused or allocates wrongly |
| [suppliers.md](suppliers.md) | suppliers, opening balance | supplier balances look wrong |
| [purchase-orders.md](purchase-orders.md) | PO workbook, production plans, balances | a PO import misbehaves |
| [planner.md](planner.md) | position, cover days, stockout and order-by | cover days or a stockout date look wrong |
| [loading-plan.md](loading-plan.md) | how much to ship on the next container | a container's mix looks wrong |
| [reorder.md](reorder.md) | suggestions → draft POs | what to buy, and from whom |
| [transfers.md](transfers.md) | warehouse → Amazon FC replenishment | stock moves between our own locations |
| [cashflow.md](cashflow.md) | the region ledger — forecast · `ARCH-005` | a container's payment is missing or wrong |

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
                                              FBA transfer ──→ Amazon FC
                                                    ↓
                                                 Planner
                                                    ↓
                                    Loading plan  ·  Reorder ──→ draft PO
```

Three relationships are not obvious from the chain and cause most confusion:

- **A container belongs to no supplier.** Ownership lives on each line, via the
  PO line the units were drawn from, so one container routinely carries goods
  from two factories.
- **Receiving is a stage, not a page.** A container moves into it the moment
  Amazon counts the first unit, whatever its status field says.
- **Planning is three machines, not one.** The planner says where we stand; the
  loading plan says what to ship to one region; reorder says what to buy across
  all of them. The last two legitimately disagree — see [reorder.md](reorder.md).

## Navigation

| Working on… | Load |
|---|---|
| a container in the wrong tab | `CLAUDE.md` · this README · [containers.md](containers.md) · `gaps.md` |
| a shortfall or Amazon's count | `CLAUDE.md` · this README · [receiving.md](receiving.md) · [containers.md](containers.md) · `gaps.md` |
| a packing-list upload | `CLAUDE.md` · this README · [allocation-workbench.md](allocation-workbench.md) · [purchase-orders.md](purchase-orders.md) · `gaps.md` |
| container payments | `CLAUDE.md` · this README · [cashflow.md](cashflow.md) · [containers.md](containers.md) · `gaps.md` |
| stock into an Amazon FC | `CLAUDE.md` · this README · [transfers.md](transfers.md) · [planner.md](planner.md) · `gaps.md` |
| a cover day or stockout date | `CLAUDE.md` · this README · [planner.md](planner.md) · `gaps.md` |
| what to put on the next container | `CLAUDE.md` · this README · [loading-plan.md](loading-plan.md) · [planner.md](planner.md) · `gaps.md` |
| what to buy | `CLAUDE.md` · this README · [reorder.md](reorder.md) · [purchase-orders.md](purchase-orders.md) · `gaps.md` |

## Current priorities

- `INV-CONT-001` — 188 in-transit lines carry no FOB, so cash flow prices them at zero · P1
- `INV-CONT-002` — opening balance is not consumable; packing lists never draw it down · P1
- `INV-RECV-001` — no active container carries an Amazon shipment ID, so nothing reaches Receiving · P1
- `INV-RECV-002` — 116 archived containers have no count; history reports 1,245,478 units lost · P1
- `INV-CONT-011` — the status-workbook import deletes every container in the region · P2
- `INV-ALLOC-003` — the container-manifest import strips FOB and PO attribution · P2
- `INV-PLAN-001` — lead times exist twice; editing a supplier's does not move the order-by date · P2
- `INV-SUP-001` — opening balance has no rate, so Outstanding FOB understates · P2

## Related sections

- [financials](../financials/README.md) — payouts received, P&L, COGS
- [reporting](../reporting/README.md) — sell-through that drives the planner
- [supply-chain](../supply-chain/README.md) — Atlas, the B2B arm. Not this.
