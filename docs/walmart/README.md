# Walmart

Orders placed on Walmart, fulfilled from our Amazon stock. The only section that
**writes to an external system on the business's behalf**.

## Purpose

We sell on Walmart and hold no Walmart-side inventory. Every Walmart order is
fulfilled by Amazon's Multi-Channel Fulfilment from the same stock that serves
Amazon customers.

This section is the machinery between the two: it takes an order from Walmart,
asks Amazon to ship it, follows the shipment, and tells Walmart the tracking
number. Nobody touches it in the normal case, which is precisely why its state
machine and audit trail matter — when it does go wrong, the record is the only
account of what happened.

**This section is complete and frozen** except for future feature changes. Two
features are documented; the process lessons are in
[RETROSPECTIVE.md](RETROSPECTIVE.md).

## Data source

| Source | Direction | Carries |
|---|---|---|
| Walmart Marketplace API | in | released orders, cancellations, order status |
| Walmart Marketplace API | **out** | acknowledgements, shipping updates with tracking |
| Amazon MCF (SP-API) | out | fulfilment order creation |
| Amazon MCF (SP-API) | in | fulfilment status, shipments, tracking numbers |

Both directions matter. This is the only section where a defect can produce a
**wrong outward action** — a duplicate fulfilment, a wrong tracking number sent
to a customer — rather than merely a wrong number on a screen.

## Features

| Document | Covers | Open here when |
|---|---|---|
| [orders.md](orders.md) | the order, its states, validation and holds | an order is stuck or held |
| [mcf-pipeline.md](mcf-pipeline.md) | the five scheduled stages and the Amazon side | fulfilment, tracking or reconciliation misbehaves |

## Relationships

```
Walmart released orders ──→ NEW ──→ VALIDATED ──→ PROCESSING ──→ MCF_CREATED
                             │ (acknowledge)                          │
                             └──→ HOLD (short) / ERROR                ↓
                                                              SHIPPED ──→ TRACKING_UPLOADED
                                                                              ↓
                                                                         COMPLETED
```

Two facts about this shape cause most confusion:

- **"MCF" means two different things.** This section *creates* MCF orders. The
  read-only mirror of Amazon's own MCF order list belongs to
  [reporting/mcf-orders.md](../reporting/mcf-orders.md) — `ARCH-006`.
- **An order archives only once Walmart has the tracking number**, never on
  Amazon's shipped status alone. See `WM-D-003`.

## Ground truth

Established before writing. *Source: local development data; provisional.*

**The laptop runs no scheduled jobs — by design.** Production runs the five
stages continuously. Orders piling up in an early state here is the scheduler
being absent, not the pipeline failing.

| | Local state |
|---|---|
| Orders | 616 · COMPLETED 348 · NEW 198 · MCF_CREATED 59 · TRACKING_UPLOADED 7 · HOLD 3 · SHIPPED 1 |
| SKU mappings | 119, all enabled |
| MCF orders created | 415 |
| Packages | 358, all uploaded to Walmart |
| Audit events | 1,388 |
| Error log | 9,651 unresolved — see `WM-ERR-001` |

**The pipeline demonstrably works end to end**: 348 orders reached COMPLETED,
and every one of the 358 packages was uploaded to Walmart.

## Navigation

| Working on… | Load |
|---|---|
| a stuck or held order | `CLAUDE.md` · this README · [orders.md](orders.md) · `gaps.md` |
| tracking or fulfilment | `CLAUDE.md` · this README · [mcf-pipeline.md](mcf-pipeline.md) · `gaps.md` |

## Method

Follows [methodology.md](../methodology.md).

## Related sections

- [reporting/mcf-orders.md](../reporting/mcf-orders.md) — the other "MCF", and `ARCH-006`
- [inventory](../inventory/README.md) — the stock these orders consume
