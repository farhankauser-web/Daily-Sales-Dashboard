# MCF pipeline

files: `apps/walmart_mcf/pipeline.py` · `apps/walmart_mcf/core.py`
       `apps/walmart_mcf/walmart_client.py`
verified against: `c525636` · 2026-08-06

The five scheduled stages that move an order from arrival to archive, and the
translation between two systems that describe shipping differently.

## Purpose

Amazon and Walmart both know how to fulfil an order and they agree on almost
nothing: shipping speeds have different names, carriers have different codes,
addresses validate differently, and each has its own idea of when an order is
finished.

This pipeline is the translation, run on a schedule, in five stages that can
each be re-run at any time without harm.

## Data source

| Stage | Reads | Writes |
|---|---|---|
| Import | Walmart released orders | new orders, acknowledged back to Walmart |
| Submit | orders ready to fulfil, Amazon inventory | **an Amazon fulfilment order** |
| Check status | Amazon fulfilment status and shipments | packages and tracking numbers |
| Upload tracking | new packages | **a Walmart shipping update** |
| Reconcile | everything unfinished | completions and a stuck-order report |

Two of those five write to an external system on the business's behalf. That is
what makes idempotence a correctness requirement here rather than a convenience.

## Business rules

1. **Every stage is idempotent.** Re-running one must not duplicate a
   fulfilment or re-send a tracking update. Uniqueness is enforced on the
   records, not assumed from the schedule.
2. **Each stage runs under a lock.** Two concurrent runs of the same stage would
   race for the same orders; the state machine would refuse the loser, but the
   external calls would already have been made.
3. **A fatal API response is never retried.** A 4xx means the request was wrong;
   retrying it produces the same answer and burns rate limit. Retryable statuses
   back off exponentially. See `WM-D-004`.
4. **Shipping speed and carrier are translated through explicit maps**, with a
   pass-through fallback rather than a failure — an unknown carrier code is more
   likely to be a new carrier than an error.
5. **An address Amazon rejects is retried in variant forms** before the order is
   held. Amazon and Walmart disagree about address formatting more often than
   customers type them wrongly.
6. **A package is uploaded to Walmart once**, tracked by a content hash, so a
   re-run cannot re-send a tracking number a customer has already been given.
7. **An order archives only after Walmart has confirmed tracking**, never on
   Amazon's shipped status alone. See `WM-D-003`.
8. **Cancellations flow inward.** A customer cancelling on Walmart is detected
   and the order is cancelled here, rather than shipping something nobody wants.
9. **Anything unfinished beyond a threshold is reported**, not auto-resolved —
   a stuck order needs a person, and the report is how they learn.

## The five stages

| Stage | Cadence in production | Moves |
|---|---|---|
| Import orders | every 5 minutes | Walmart → NEW, acknowledged |
| Submit orders | every 10 minutes | NEW or HOLD → VALIDATED → PROCESSING → MCF_CREATED |
| Check status | every 15 minutes | MCF_CREATED → SHIPPED, harvesting packages |
| Upload tracking | every 15 minutes | SHIPPED → TRACKING_UPLOADED |
| Reconcile | nightly | TRACKING_UPLOADED → COMPLETED, and the stuck report |

## System behaviour

- **Submission looks for an existing Amazon fulfilment order before creating
  one**, including ones created manually, so a human intervention mid-flight
  does not produce a duplicate shipment.
- **Packages are harvested from Amazon's shipment detail**, one record per
  tracking number, because a single order can ship in several parcels on
  different days.
- **A split shipment reopens the order.** New packages after a tracking upload
  move it back to SHIPPED so the second parcel's tracking is also sent.
- **Manually fulfilled orders are backfilled** — their tracking is uploaded to
  Walmart first, and only then do they archive.
- Every call in both directions is logged with status and duration.

## Edge cases

- **Amazon already has a fulfilment order for this purchase order.** Adopted
  rather than duplicated — the check is by identifier and by customer order id.
- **Walmart already has tracking for the order.** The upload reports it and the
  package is marked uploaded; it is not an error, and one order in the local data
  did exactly this after four days of retries and completed normally.
- **An order cancelled on Walmart after Amazon accepted it.** Cancelled here;
  whether Amazon can still stop the shipment is Amazon's to answer.
- **A carrier code Amazon returns that Walmart does not know.** Passed through
  rather than dropped, on the reasoning in rule 4.

## Observations — not gaps

*Source: local development data; provisional.*

- **All 358 packages were uploaded to Walmart successfully**, and 348 orders
  reached COMPLETED. The pipeline works end to end.
- **One order accumulated 84 failures over four days and then succeeded**,
  reaching COMPLETED. The retry design recovered it; the failure rows remain
  unresolved, which is `WM-ERR-001` rather than a pipeline fault.
- **8,581 of the local error rows are one Amazon credential rejection.** That is
  a local configuration matter — the credentials on this machine are not the
  production ones — and says nothing about production health.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `WM-ERR-001` | the error log has no lifecycle, so a repeating failure buries every real one | missing implementation |

## Related decisions

`WM-D-003` `WM-D-004`

## Related documents

- [orders.md](orders.md) — the states these stages move orders through
- [reporting/mcf-orders.md](../reporting/mcf-orders.md) — the other "MCF", `ARCH-006`
