# Walmart orders

files: `apps/walmart_mcf/{models,state,views}.py`
       `apps/walmart_mcf/pipeline.py` — `import_orders`
       `templates/walmart_mcf/`
verified against: `c525636` · 2026-08-06

An order placed on Walmart, and its life from arrival to archive. The state
machine everything else in this section moves orders through.

## Purpose

A Walmart order is a promise to a customer that we will ship something we hold
in Amazon's warehouse. Between those two facts sit a dozen ways to fail: the SKU
may not map, the stock may be short, either API may be down, a customer may
cancel mid-flight.

The order record and its state machine exist so that at any moment there is one
answer to *where is this order, and why*, and an audit trail saying how it got
there.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Walmart released-orders feed | per purchase order | the order, its customer, address and lines |
| SKU mapping | per Walmart SKU | which Amazon SKU fulfils it |
| Amazon inventory | per SKU | whether it can be fulfilled now |

The Walmart feed is authoritative for everything about the order itself. Nothing
in this section edits an order's contents — only its state.

## Business rules

1. **An order is acknowledged to Walmart on import.** Walmart requires it, and
   it is what stops the order being re-released.
2. **An order is identified by its purchase order id**, and importing the same
   order twice updates rather than duplicates. Re-running an import is always
   safe.
3. **Every Walmart SKU must map to an Amazon SKU before fulfilment.** An
   unmapped SKU is a hold, not a guess. See `WM-D-001`.
4. **Insufficient stock holds the order rather than failing it**, with the SKU
   and the shortfall recorded. A held order is retried; a failed one is not.
5. **State transitions are atomic and exclusive.** Exactly one worker can move
   an order out of a state; every other caller is refused and skips it. See
   `WM-D-002`.
6. **Only listed transitions are legal.** Anything else raises rather than
   silently moving the order.
7. **Every transition writes an audit event** — from, to, who and why — in the
   same transaction as the move, so the trail cannot disagree with the state.
8. **An error state is recoverable by a human**, never automatically. An order
   in ERROR moves back to NEW or to CANCELLED only by an operator's decision.

## States

| State | Meaning | Leaves to |
|---|---|---|
| NEW | imported and acknowledged | VALIDATED · HOLD · ERROR · CANCELLED · COMPLETED |
| VALIDATED | mapped and in stock | PROCESSING · HOLD · ERROR · CANCELLED |
| PROCESSING | being submitted to Amazon | MCF_CREATED · ERROR · **NEW** · CANCELLED |
| MCF_CREATED | Amazon accepted the fulfilment order | SHIPPED · ERROR · CANCELLED |
| SHIPPED | Amazon has despatched packages | TRACKING_UPLOADED · ERROR · CANCELLED |
| TRACKING_UPLOADED | Walmart has the tracking number | COMPLETED · **SHIPPED** · CANCELLED |
| HOLD | short of stock or unmapped | HOLD · NEW · VALIDATED · ERROR · CANCELLED |
| ERROR | failed; needs a person | NEW · CANCELLED |
| CANCELLED | abandoned | NEW |
| COMPLETED | archived | — terminal |

Three edges are deliberate and easy to mistake for bugs:

- **PROCESSING → NEW** is a clean rollback. If submission fails without Amazon
  accepting anything, the order returns to the queue rather than to ERROR.
- **TRACKING_UPLOADED → SHIPPED** happens when Amazon despatches *more*
  packages after the first tracking upload. A split shipment is normal.
- **HOLD → HOLD** is a re-hold: still short, checked again, still short.
- **NEW → COMPLETED** covers an order fulfilled outside this system entirely.

## User actions

| Action | Who | Result |
|---|---|---|
| Run a stage now | ops | one pipeline stage runs immediately rather than waiting |
| Reprocess an order | ops | a stuck or errored order is pushed back through |
| Export orders | ops | the current view as a spreadsheet |
| Edit a SKU mapping | ops | future validations use it; held orders can then proceed |
| Configure credentials | ops | Walmart API access, stored encrypted |

## System behaviour

- Import looks back over a window rather than only at what is new, so an order
  missed during an outage is picked up on the next run.
- Every outbound and inbound API call is logged with its endpoint, status and
  duration, which is what makes an incident reconstructable.
- Failures are recorded against the order where one is known — though the log
  itself has no lifecycle (`WM-ERR-001`).

## Data model

- **Order** — the Walmart purchase order: customer, address, shipping method,
  state, and the raw payload as received.
- **Order item** — one line: Walmart SKU, quantity, unit price.
- **SKU mapping** — Walmart SKU → Amazon SKU, with an enabled flag so a mapping
  can be suspended without deleting it.
- **Audit event** — one per transition: from, to, actor, detail.
- **API log** and **error log** — the call record and the failure record.

## Edge cases

- **An order whose SKU is unmapped.** Held with the SKU named, so the remedy is
  obvious. It proceeds once the mapping exists.
- **An order short by one unit.** Held whole; the pipeline does not part-ship,
  because a partial fulfilment against a Walmart order creates a customer
  promise we cannot complete.
- **A customer cancelling after Amazon accepted.** Detected by the cancellation
  sync and moved to CANCELLED. See [mcf-pipeline.md](mcf-pipeline.md).
- **An order fulfilled manually outside the system.** Moves NEW → COMPLETED once
  its tracking has been uploaded, rather than being forced through the pipeline.

## Observations — not gaps

*Source: local development data; provisional.*

- **198 orders sit in NEW.** The stage that would move them runs only in
  production. Not a backlog; an absent scheduler.
- **Three orders are held on the same SKU**, each naming the shortfall — the
  hold mechanism working exactly as designed.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `WM-ERR-001` | the error log has no lifecycle, so a repeating failure buries every real one | missing implementation |
| `WM-001` | every non-JSON response reported as "session expired" | to be established |

## Related decisions

`WM-D-001` `WM-D-002`

## Related documents

- [mcf-pipeline.md](mcf-pipeline.md) — the stages that move orders through these states
- [reporting/mcf-orders.md](../reporting/mcf-orders.md) — the other "MCF", `ARCH-006`
