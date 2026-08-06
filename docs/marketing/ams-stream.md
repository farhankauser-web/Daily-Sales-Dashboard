# AMS stream

files: `apps/dashboard/ams_consumer.py`
       `apps/dashboard/management/commands/{ingest_ams_s3,seed_ams_subscriptions}.py`
verified against: `6d18acc` · 2026-08-06

Amazon Marketing Stream delivers advertising events **hourly**, continuously, as
they happen. This is the pipeline that turns them into the hourly campaign
figures the business sees during a trading day.

## Purpose

The Ads API answers "what did yesterday cost" a day or two after the fact. That
is the settled number, and it is too late to act on. The stream answers "what is
today costing, right now" — hour by hour, while the day is still running.

It exists so a campaign that is burning budget at 11am is visible at noon rather
than the following afternoon.

## Scope

Covers subscribing to the stream, consuming its events, and folding them into
hourly campaign figures.

**Not covered:**
- the settled daily figures — [ads-api.md](ads-api.md)
- Seller Central hourly files uploaded by hand into the same table —
  hourly-upload.md *(pending)*
- which source wins when they disagree — [sku-allocation.md](sku-allocation.md), `MKT-D-002`

## Business workflow

```
Amazon Marketing Stream ──→ Firehose ──→ S3 (one object per batch)
                                            ↓
                            list new objects, skip anything already consumed
                                            ↓
                        parse events → identify dataset → fold to (campaign, hour)
                                            ↓
                              add to the stored hourly figure
```

## Business rules

1. **Each S3 object is consumed exactly once.** A ledger records every object
   processed, and consumption is keyed off it — not off timestamps or filenames.
2. **Hourly figures accumulate; they are never replaced.** Amazon keeps sending
   late revisions for hours already stored, and a run only sees the events in
   its own batch. Replacing would overwrite a complete total with a partial one.
   Accumulating is only safe *because* of rule 1. See `MKT-D-005`.
3. **Only one ingest runs at a time.** Two overlapping runs would list the same
   objects before either recorded them and both would add their totals.
4. **Traffic and conversion are written separately.** An hour that has seen only
   impressions and cost is not blanked by a later conversion-only event, and
   vice versa.
5. **Events are bucketed in the marketplace's local time**, because a trading
   day is local. Amazon timestamps them in UTC.
6. **Attribution windows differ by ad product and are not reconciled.**
   Sponsored Products reports one-day attribution and settles within a day;
   Brands and Display report fourteen-day attribution and revise upward for
   weeks. Both are stored as they arrive. See `MKT-D-006`.
7. **Budget-usage events are routed separately** and never folded into the
   hourly spend figure — they describe budget consumption, not cost incurred.
8. **A manually uploaded hour is never overwritten by the stream.** See
   hourly-upload.md *(pending)*.
9. **An event whose dataset cannot be identified is skipped and counted**, never
   guessed into a bucket.

## Actors

| Actor | Does |
|---|---|
| Amazon | publishes events to the stream and writes them to our S3 bucket |
| The system | polls, parses, deduplicates, folds into hourly figures |
| Ops | seeds and repairs subscriptions when a dataset stops flowing |

## System behaviour

- The ingest runs **every minute in production** and is capped per run, so a
  backlog is worked off steadily rather than in one large batch.
- Objects are deduplicated **incrementally while listing**, not by collecting
  every key first — the earlier approach deadlocked once the listing window held
  more already-processed objects than the per-run cap, and the ingest reported
  "no new objects" indefinitely.
- Each ingest records how many records it parsed and how many it used, per
  object, so silent drops are measurable rather than invisible.
- Days that received events are marked in the completeness log, which is what
  gates whether a metric column is shown as trustworthy.
- Active subscriptions have their last-ingest time touched on every run, so a
  subscription that has stopped delivering is visible.

## Data model

- **Subscription** — one per marketplace and dataset, with a provisioning
  status. Six datasets are used: traffic and conversion for each of Sponsored
  Products, Brands and Display.
- **Processed object** — the exactly-once ledger: which S3 object was consumed,
  how large it was, how many records it carried and how many were used.
- **Hourly campaign figure** — one per marketplace, date, hour, campaign and ad
  product, carrying spend, impressions and clicks from the traffic side and
  orders, sales and units from the conversion side, plus which source wrote it.

## Integrations

| System | Direction | What moves |
|---|---|---|
| Amazon Marketing Stream → Firehose → S3 | in | one event per campaign per hour per dataset |

## Edge cases

- **The same hour arriving many times.** Normal. Each delivery adds only what
  that batch carried, and the ledger stops any object being counted twice.
- **An event for a campaign we have never seen.** Stored against its ID; the
  name is filled in later from the daily snapshots, because the stream carries
  IDs and not names.
- **Sponsored Brands and Display revisions weeks later.** Accepted, and the
  reason the daily snapshot rather than the stream is authoritative for settled
  days (`MKT-D-002`).
- **A dataset with no active subscription.** Its events simply never arrive, and
  the gap is invisible in the hourly figures — nothing reports an ad product
  that is silent.

## Observations — not gaps

*Source: local development data; provisional. This machine runs no scheduled
jobs, so these describe structure, not production behaviour.*

- **Subscriptions show `FAILED_PROVISIONING` rows.** Every one of the six
  datasets also has an `ACTIVE` row — the failures are historical retry
  attempts, not a dataset that is down. Nothing to fix.
- **Roughly 3.8% of parsed records are not used** (1,621,517 of 1,684,947).
  Budget-usage events are parsed and deliberately not folded into hourly spend,
  which accounts for the bulk of it. Not evidence of dropped spend.
- **The stream and manual uploads both write this table** — about 139,000 rows
  from the stream and 112,000 from uploads on this machine.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `MKT-AMS-001` | a dataset that stops delivering is silent — nothing checks that every subscription is still arriving | missing implementation |
| `MKT-AMS-002` | the legacy topic-ARN dataset map covers Sponsored Products only outside North America | missing implementation |

## Related decisions

`MKT-D-002` `MKT-D-005` `MKT-D-006`

## Related documents

- [ads-api.md](ads-api.md) — the settled daily figures this is measured against
- hourly-upload.md *(pending)* — the other writer of the hourly table
- [sku-allocation.md](sku-allocation.md) — what consumes these figures
