# Receiving

files: `apps/inventory_planning/{views,models,planning}.py`
       `apps/inventory_planning/management/commands/{sync_awd_receipts,sync_fba_receipts,reconcile_transit_receipts,backfill_container_shipment_ids}.py`
       `templates/inventory_planning/{receiving,containers,container_history}.html`
verified against: `86d5f35` · 2026-08-06

Receiving is the stage between a container being on the water and the container
being closed: Amazon is counting our cartons in, and we are comparing what it
counts against what we packed.

## Purpose

Receiving answers one question — *did all of it arrive* — and stops a second
one being answered wrongly. Until Amazon's count is known, units that have
already landed are still counted as inbound, so the planner sees them twice:
once in warehouse stock and once on the container. That double-count previously
left 45,088 phantom units on the books while five landed containers sat "at
port".

The comparison is also the basis of a claim. A shortfall argued with Amazon is
argued from our packing list, so the number has to be the packing list's.

## Scope

Covers Amazon's count of a container, the shortfall it implies, and the stage
a container occupies while that is happening.

**Not covered:**
- the container's own lifecycle and statuses — [containers.md](containers.md)
- how a container comes to exist — allocation-workbench.md *(pending)*
- PO-line goods-receipt variance, ordered vs received against a purchase order
  — transfers.md *(pending)*
- what a lost unit costs. The app values nothing; see `INV-D-008`.

## Business workflow

```
Container in transit → Amazon counts first unit → Receiving → Amazon CLOSES → History
                                    ↓                              ↓
                        planner counts only the remainder    shortfall frozen
```

A container enters Receiving the moment Amazon counts a single unit against its
shipment, and leaves only when Amazon closes the shipment. Nothing in between is
a human decision — ops sets no status, and the page carries no buttons.

The whole chain depends on one field. A container with no Amazon shipment ID is
invisible to Amazon's side of the comparison: it never enters Receiving, its
units stay fully inbound, and it sits past its ETA with no signal. That is the
commonest cause of a "stuck" container, and today it is the normal case
(`INV-RECV-001`).

## Actors

| Actor | Does |
|---|---|
| Ops | records the Amazon shipment ID on the container; nothing else here |
| Amazon | counts units in, reports a shipment status, closes the shipment |
| The system | polls twice daily, records the counts, moves the container between stages |
| Warehouse | counts a container by hand instead, where there is no Amazon shipment to count against |

## Business rules

1. **Receiving starts at the first counted unit**, not at arrival and not at a
   status change. Membership is derived from the receipts themselves, so a
   container whose status was never advanced is still in the right place. See
   `INV-D-009`.
2. **Three quantities per SKU, and they are not interchangeable** —
   *declared (A)* what Amazon was told when labels were generated,
   *packed (B)* what the packing list says left the factory,
   *received (C)* what Amazon has counted in.
3. **Variance is B − C.** Never A − C. See `INV-D-001`.
4. **A ≥ B always.** We declare at least what we pack, so an A-based figure
   invents a shortage out of our own over-declaration. Over-declaration is
   reported separately and is not a loss.
5. **AWD counts in CASES; FBA Inbound counts in EACHES.** Both are stored in
   eaches. Applying the AWD case conversion to an FBA payload multiplies every
   figure by the pack size — a 1,440-unit line reads as 34,560.
6. **Amazon's case pack wins** where it disagrees with ours. The difference
   lands inside the variance rather than being argued about. See `INV-D-012`.
7. **Amazon's count never overwrites a human count.** The two are held apart;
   where both exist the human figure is used, where only one exists that one is.
   See `INV-D-006`.
8. **A container Amazon has not started counting has no variance.** Reporting
   one would read the entire container as missing.
9. **Only Amazon CLOSING the shipment ends receiving.** A cancelled, deleted or
   errored shipment advances nothing. See `INV-D-008`.
10. **Counted units stop being inbound.** The planner counts the un-received
    remainder of a container, never the whole line.
11. **The app values no loss.** Units short remain on the line as packed minus
    counted, for the COGS system to value.
12. Each container is reconciled through **exactly one API**, chosen by the
    shape of its shipment ID. See `INV-D-013`.

## States

Receiving is a stage of the container, not an entity of its own — see
[containers.md](containers.md) for the container's own statuses. What is
specific to this stage is Amazon's view of the shipment:

| Amazon status | Means | Effect here |
|---|---|---|
| `CREATED` · `WORKING` | shipment exists, nothing counted | stays In Transit |
| `SHIPPED` | in Amazon's hands, not counted | stays In Transit |
| `RECEIVING` | intake started | moves to Receiving |
| `CLOSED` | intake finished | archives to Container History |
| `CANCELLED` · `DELETED` · `ERROR` | dead end | left where it is, never archived |

A container also enters Receiving on the first counted unit even when Amazon's
status has not yet moved to `RECEIVING`, because the receipts are the more
reliable signal.

## User actions

The Receiving page is **read-only**. It has a region selector, a refresh, and a
filter between *being counted in*, *all linked* and *including closed*. Every
action that affects receiving happens elsewhere:

| Action | Who | Where | Precondition | Result |
|---|---|---|---|---|
| Record the Amazon shipment ID | ops | Containers → edit | container exists | the receipt syncs can find it |
| Link IDs in bulk from the ops workbook | ops | `backfill_container_shipment_ids` | a Containers Summary file | existing IDs are never silently replaced |
| Set a variance reason | ops | Goods Receipt | a line with a variance | damage · short-ship · lost · miscount · other |
| Receive by hand | ops | Containers → Receive | active container | container archived, stock added, receipt attributed to the user |

Receiving by hand pre-fills every line with the packed quantity, so accepting
the form as presented records a perfect receipt. All 4 of the hand-counted
containers on record show counted exactly equal to packed.

## System behaviour

- **Twice daily**, every open container carrying an Amazon shipment ID is polled
  and its per-SKU declared and received figures recorded — AWD at 07:20 and
  19:20, fulfilment centres five minutes later.
- **Ten minutes before that, at 07:10 and 19:10**, warehouse stock is refreshed
  from Amazon. The order is the point: a receipt sync takes counted units out of
  inbound, and they only reappear once stock has refreshed. Run far apart, the
  planner spends the gap with those units in neither column and under-states
  cover.
- Those schedules are what `deploy/crontab.txt` specifies. It is a macOS
  template and the EC2 crontab has drifted from it — `INFRA-001`.
- **Routing is by the shape of the shipment ID.** `STAR-…` is an AWD shipment;
  anything else is an FBA inbound shipment. The two APIs do not resolve each
  other's ids, and the FC path skips `STAR-` so the two jobs never contend for
  the same container.
- **Case packs are learned from the AWD payload only.** The FBA path stores no
  pack size, because its figures are already eaches and a stored pack would
  corrupt anything reading it.
- **Stage advancement is enabled on the scheduled runs**: first receipt moves a
  container to Receiving, `CLOSED` moves it to History. Both syncs report
  without writing unless told otherwise, and advance no status unless told
  separately again — a run by hand changes nothing by default.
- **The planner counts the remainder.** A line 900 of 1,000 counted contributes
  100 inbound units, and a fully counted line contributes none.
- **Containers bound for a fulfilment centre are netted out** of Amazon's own
  inbound figure, because Amazon reports the same cartons and the container
  carries more detail.
- **A separate reconciliation** looks for containers Amazon already shows as
  on-hand with nothing inbound — the fingerprint of a landed container still
  open in Pulse — and reports them rather than closing them, because closing a
  container is an accounting act.

## Data model

- **Container line** — one SKU on one container. Holds four counts side by side:
  packed, Amazon's declared, Amazon's received, and the human count. They are
  separate on purpose; a disagreement between them is information.
- **Counted units** — the derived figure everything reporting a shortfall must
  read: the human count where there is one, otherwise Amazon's.
- **Shortfall** — packed minus counted, floored at zero.
- **Amazon shipment link** — the container's shipment ID, plus Amazon's last
  reported status and when it was last polled. Without the ID none of the above
  can be populated.

## Integrations

| System | Direction | What moves |
|---|---|---|
| Amazon AWD inbound | in | shipment status, per-SKU expected and received, in **cases**, plus the case pack |
| Amazon FBA inbound | in | shipment status, per-SKU shipped and received, in **eaches** |
| Amazon inventory | in | on-hand and inbound per SKU, used to spot containers that have quietly landed |

## Dependencies

Depends on [containers.md](containers.md) for the container and its shipment ID,
and on allocation-workbench.md *(pending)* for the packed quantity that every
comparison is made against. Feeds planner.md *(pending)* — the un-received
remainder is what stays inbound — and container history.

## Edge cases

- **No Amazon shipment ID.** Nothing can be counted against. The container never
  reaches Receiving and its units stay fully inbound until somebody receives it
  by hand. Currently true of every active container (`INV-RECV-001`).
- **Amazon declares more than we packed.** Normal and not a loss. Amazon
  reconciles against its own declared figure, so its discrepancy report will
  look worse than ours, and that difference is explained rather than reconciled
  away.
- **Amazon counts more than we packed.** A pack-size disagreement, not a gain.
  Reported separately from shortfall because the remedy is a setup fix rather
  than a claim.
- **Amazon lists a SKU our packing list does not.** Reported by SKU and skipped.
  It is either a mislabel or a packing list that was never corrected.
- **A container Amazon never closes.** It sits in Receiving indefinitely,
  overstating inbound stock, with nothing drawing attention to it
  (`INV-CONT-003`).
- **A container that landed but was never received.** Amazon shows the units
  on-hand and nothing inbound, while Pulse still counts the container — the same
  units in two places. Detected and reported, never closed automatically.
- **Archived with no count at all.** 116 of the 120 archived containers have no
  count from either source, so history reports 1,245,478 units as lost
  (`INV-RECV-002`).

## Known gaps

- `INV-RECV-001` — no active container carries an Amazon shipment ID, so nothing reaches Receiving
- `INV-RECV-002` — archived containers with no count report as a total loss
- `INV-RECV-003` — per-SKU variance views ignore Amazon's count
- `INV-RECV-004` — a SKU with nothing received reports no shortfall
- `INV-RECV-005` — the receipt syncs are neither region-filtered nor scheduled outside the USA
- `INV-CONT-003` — no stall alert for a container stuck in Receiving
- `INV-CONT-004` — goods receipt writes AWD stock the sync then overwrites

## Related decisions

`INV-D-001` `INV-D-006` `INV-D-008` `INV-D-009` `INV-D-012` `INV-D-013`

## Related documents

- [containers.md](containers.md) — the container, its statuses and its history
- allocation-workbench.md *(pending)* — where the packed quantity comes from
- planner.md *(pending)* — what the un-received remainder does to cover
- transfers.md *(pending)* — goods-receipt variance against the purchase order
