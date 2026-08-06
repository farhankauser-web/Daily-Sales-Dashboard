# Inventory — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**Where the evidence comes from.** Every count in this file was measured against
the **local SQLite database**, not production Postgres. That database is a
development copy seeded on 2026-07-20 and lightly used since: no container in it
was created through the Allocation Workbench, and 128 of its 131 containers have
not been written to since the seed. Findings about *code* transfer to production
unchanged. Findings about *data* — how many containers carry a shipment ID, how
many were received by hand — describe this copy and must be re-measured on
production before anyone acts on them. Each such gap says so.

**Absence of data is not a defect.** Before recommending anything, establish why
the state exists: code that was never written, code that is wrong, code that
works but was never switched on, software that works but a step nobody performs,
or rows that predate the mechanism. Each gap carries that as **Classification**,
and says whether a code change alone would resolve it. A recommendation that
does not follow from the cause is how a register turns into a wishlist.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|
| `INV-CONT-001` | In-transit lines carry no FOB rate | P1 | legacy data | open |
| `INV-CONT-002` | Opening balance is not consumable | P1 | missing implementation | open |
| `INV-RECV-001` | No active container carries an Amazon shipment ID | P1 | missing operational process | open |
| `INV-RECV-002` | Archived containers with no count report as a total loss | P1 | legacy data | open |
| `INV-CONT-003` | No stall alert for a container stuck in Receiving | P2 | missing implementation | open |
| `INV-CONT-011` | The status-workbook import deletes every container in the region | P2 | bug | open |
| `INV-RECV-003` | Per-SKU variance views ignore Amazon's count | P2 | bug | open |
| `INV-RECV-004` | A SKU with nothing received reports no shortfall | P2 | bug | open |
| `INV-SUP-001` | Opening balance has no rate, so Outstanding FOB understates | P2 | — | open |
| `INV-CASH-001` | Opening-balance backlog never reaches cash flow | P2 | — | open |
| `INV-CONT-004` | Goods receipt writes AWD stock the sync overwrites | P3 | bug | open |
| `INV-RECV-005` | Receipt syncs are neither region-filtered nor scheduled outside the USA | P3 | missing implementation · configuration | open |
| `INV-SUP-002` | `POLineGroup.pcs` is written and never read | P3 | — | open |

`—` means the cause has not been established yet. Those gaps predate the
Classification field and get one when the Suppliers and Cash Flow documents are
written; a guess would be worse than a blank.

Closed gaps are at the end. They keep their ids and their rows.

---

## `INV-CONT-001` · In-transit lines carry no FOB rate

| | |
|---|---|
| **Priority** | P1 |
| **Status** | open — deliberate, see `INV-D-007` |
| **Classification** | legacy data |
| **Code alone fixes it** | no — the packing lists must be re-uploaded, and `INV-CONT-011` fixed first |
| **Dependencies** | `INV-CONT-011` (would erase the re-uploaded rates) |

**Current behaviour** — 188 of 188 active USA container lines have no FOB rate
and no PO link, so every container in transit prices at zero in cash flow.

**Expected behaviour** — Every container line carries a rate, so the region
ledger shows what we actually owe.

**Root cause** — Legacy data, and more completely so than first recorded. These
lines do not merely predate the FOB column: **no container in this database was
ever created through the Allocation Workbench.** All 131 were created by the
seed import, which carries units and dates and nothing else. A rate, a PO link
and an FNSKU are all produced by the packing-list path, and that path has never
run here. The mechanism is correct and unexercised — see `INV-D-007`.

**Evidence**
```python
InTransitLine.objects.filter(shipment__in=active_usa, po_line__isnull=True).count()
# 188, of 188 active
InTransitLine.objects.filter(fob_rate__gt=0).count()      # 0 of 2,615
InTransitLine.objects.exclude(po_line=None).count()       # 0 of 2,615
InTransitLine.objects.exclude(fnsku='').count()           # 0 of 2,615
```

**Business impact** — Cash flow understates outflows by the full value of
everything currently on the water. A funding decision taken on that ledger would
be wrong in the dangerous direction.

**Technical impact** — None ongoing. The mechanism works for new containers;
these rows simply predate it.

**Recommendation** — Re-upload these containers' packing lists through the
Allocation Workbench with FOB in the file. That gives real attribution, real
rates and drawn-down balances. A backfilled rate by SKU would produce a number
without the mechanism and is not worth doing.

Fix `INV-CONT-011` first. The re-upload creates exactly the fields the
status-workbook import deletes, so doing it in the other order risks losing the
work to a single upload.

**Related documents** — [containers.md](containers.md), cashflow.md *(pending)*
**Related decisions** — `INV-D-004`, `INV-D-005`, `INV-D-007`

---

## `INV-CONT-002` · Opening balance is not consumable

| | |
|---|---|
| **Priority** | P1 |
| **Status** | open — decided, not built |
| **Classification** | missing implementation |
| **Code alone fixes it** | yes |
| **Dependencies** | `INV-SUP-001` (a rate makes the drawdown valuable) |

**Current behaviour** — A packing list draws only from PO lines. Opening balance
is a static figure that nothing decrements, so units are deducted from purchase
orders even when backlog exists.

**Expected behaviour** — Opening balance first, oldest first, then PO lines
FIFO. See `INV-D-011`.

**Evidence** — `_open_lines_for()` in `procurement.py` queries `POLine` only.

**Business impact** — PO balances fall faster than they should while backlog
sits untouched, so outstanding-to-supplier figures misstate which commitment is
actually being worked off.

**Technical impact** — Opening balance must become an allocatable source: a link
from the container line, allocation counting rather than a decrementing counter,
and a guard so re-uploading a drawn-against balance is refused rather than
replacing it.

**Recommendation** — Build the two-tier supply pool. Mirror the PO-line pattern
exactly — remaining = units − allocations — so nothing new has to be invented.

**Related documents** — suppliers.md *(pending)*, allocation-workbench.md *(pending)*
**Related decisions** — `INV-D-011`

---

## `INV-CONT-003` · No stall alert for a container stuck in Receiving

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open — threshold undecided |
| **Classification** | missing implementation |
| **Code alone fixes it** | no — the threshold is a business judgement, and see `INFRA-001` for the schedule |
| **Dependencies** | `INV-RECV-001` (nothing reaches Receiving to stall) |

**Current behaviour** — A container Amazon starts counting but never closes
stays in Receiving indefinitely, with nothing drawing attention to it.

**Expected behaviour** — An alert once Amazon's counted figure has not moved for
a set number of days.

**Evidence** — No stall check exists; the receipt syncs advance a container only
on new receipts or on CLOSED.

**Business impact** — A part-received container silently overstates inbound
stock for as long as it sits there. Five containers previously sat past ETA
unnoticed for weeks.

**Technical impact** — Small: a scheduled check over containers in the receiving
stage comparing counted units against the last sync date.

**Recommendation** — Alert at 14 days without movement. Needs confirmation —
the threshold is a business judgement about how long Amazon reasonably takes.

**Related documents** — [receiving.md](receiving.md), [containers.md](containers.md)
**Related decisions** — `INV-D-008`

---

## `INV-CONT-004` · Goods receipt writes AWD stock the sync overwrites

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | bug — harmless today |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — Confirming a goods receipt adds the counted units to the
destination warehouse's stock for both AWD and 3PL. The Amazon stock sync then
replaces the AWD figures wholesale from the API.

**Expected behaviour** — The manual write applies only to warehouses the API
does not feed — 3PL and factory.

**Evidence** — `api_receive_container` writes for `kind in ('awd','3pl')`;
`sync_planning_inventory` replaces every AWD row for the region.

**Business impact** — None visible. The API figure is correct and wins.

**Technical impact** — Misleading code: a reader reasonably concludes the manual
count feeds AWD stock, and would build on that.

**Recommendation** — Restrict the write to `3pl` and `factory`, and say in the
UI that AWD comes from Amazon.

**Related documents** — [receiving.md](receiving.md), transfers.md *(pending)*

---

## `INV-CONT-011` · The status-workbook import deletes every container in the region

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open — latent; has not run since 2026-07-20 |
| **Classification** | bug — data loss, not currently firing |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — Uploading ops' status workbook destroys and recreates
every container in the region from the Transit sheet, where each container is a
column. Only what the sheet carries survives: container number, vendor,
destination, two dates and the shipment ID from row 6. Everything Pulse knows
that the workbook does not is deleted with the row —

| Lost | Which decision or mechanism it belongs to |
|---|---|
| Amazon shipment ID typed in Pulse or set by the backfill | `INV-RECV-001` |
| FOB rate snapshotted onto each line | `INV-D-004`, `INV-D-005` |
| PO line attribution, and with it the PO's drawn-down balance | `INV-D-011`, allocation |
| Human receipt counts, and the `received_by` / `received_at` audit trail | `INV-D-006` |
| Amazon's counted units and case packs | receiving |
| Variance reasons | goods receipt |
| Any status ops set by hand | [containers.md](containers.md) |

**Expected behaviour** — The import updates the containers the sheet describes
and leaves everything else intact, as `import_containers` already does: it
upserts by container number, replaces only that container's lines, and never
touches a field the file does not carry.

**Root cause** — A deliberate simplification that predates every mechanism now
hanging off a container. The Transit sheet is a wide, positional layout with no
stable key, so a full replace was the cheap way to make re-imports idempotent —
`# full replace for the region` sits directly above the delete. When it was
written a container held units and dates and nothing else, so there was nothing
to lose. Seven mechanisms have since been attached to the container record, and
the delete was never revisited.

**Evidence**
```python
# apps/inventory_planning/importer.py, inside the Transit-sheet branch
InTransitShipment.objects.filter(region=region).delete()
```
`InTransitLine.shipment` cascades, so the lines go with it; `POLine.allocations`
is derived from surviving lines, so `allocated_units` falls and `remaining_units`
rises by the same amount — PO balances silently inflate.

The current rows carry the fingerprint of exactly this: 131 containers occupy a
contiguous primary-key range of 392–523, one creation batch, with ids 1–391
consumed and deleted. 120 of the 131 statuses reproduce the importer's date rule
exactly, and 116 of the 120 archived containers have `received_date` equal to
`eta_destination`, which only the importer writes.

**Why it is P2 and not P1** — It is not firing. The endpoint is routed but no
template links it, so it is reachable only by a direct POST; 128 of the 131
containers have not been written to since the seed import on 2026-07-20 16:01.
Nothing it would destroy currently exists either: 0 of 2,615 container lines
carry a FOB rate, a PO link or an FNSKU, because no container in this database
was created through the Allocation Workbench.

**Business impact** — None so far. The exposure is entirely forward-looking, and
it is aimed squarely at the two remedies this register recommends: re-uploading
packing lists to fix `INV-CONT-001` produces FOB rates and PO attribution, and
`INV-RECV-001` produces shipment IDs — precisely the fields the sheet does not
carry and the import therefore erases. PO balances would rise with no entry
explaining why, so a supplier would look owed goods that had already shipped.

**Technical impact** — The container has become the join point for procurement,
cash flow and receipts, and one upload truncates it. Nothing warns; the import
reports a success count for the rows it created, not a loss count for the rows
it removed.

**Recommendation** — Upsert by container number instead of deleting, matching
`import_containers`, which already solves the same problem for the long-format
file. Never write a field the sheet does not carry: a blank row 6 must leave an
existing shipment ID alone rather than clearing it. Containers in the region
that the sheet omits should be reported, not deleted — a container missing from
one upload is far more likely to be a workbook edit than a cancelled shipment.

The window matters more than the severity: this must land **before** the
`INV-CONT-001` and `INV-RECV-001` remedies, not after, because those two create
the data it destroys. Until it does, treat a status-workbook upload as
destructive.

**Related documents** — [containers.md](containers.md), [receiving.md](receiving.md)
**Related decisions** — `INV-D-004`, `INV-D-005`, `INV-D-006`, `INV-D-010`

---

## `INV-RECV-001` · No active container carries an Amazon shipment ID

| | |
|---|---|
| **Priority** | P1 |
| **Status** | open |
| **Classification** | missing operational process |
| **Code alone fixes it** | no — a command that exists has to be run, and ops have to record the ID when a container is booked |
| **Dependencies** | none. `INV-CONT-011` would destroy the result if it ever runs again |

**Current behaviour** — Both receipt syncs iterate open containers that carry an
Amazon shipment ID. There are none. Every container that has ever been linked is
already archived, so the Receiving page is permanently empty and no container
has ever been reconciled against Amazon's count.

**Expected behaviour** — Every container dispatched under an Amazon shipment
carries that shipment's ID from creation, so its receipts arrive on the next
sync.

**Root cause** — Not a defect in receiving. The capability is complete and
correct: the field has existed since the app's first migration, the container
form captures it, `backfill_container_shipment_ids` links it in bulk from ops'
Containers Summary workbook, and both syncs work. It has never had anything to
act on, for two compounding reasons.

*Primary — the step is not part of anyone's process.* All 11 active containers
are consigned to Amazon AWD USA, so each one does have a STAR- shipment,
and ops record those IDs — in the Containers Summary workbook, not in Pulse.
Until 2026-08-03 the field was labelled "Shipment ID / optional (FBA STA)",
which nobody read as applying to AWD containers, and AWD is all of them
(`8205fb1`). The backfill written that day reported 63 containers to link; 14
carry an ID today, and all 14 are archived.

*The backfill has never been run with `--apply`.* All 14 IDs that exist arrived
in the original seed import on 2026-07-20 at 16:01, from row 6 of the Transit
sheet — they are primary keys 392–406, the leftmost and oldest columns of the
sheet. 128 of the 131 containers have not been written to since that moment. Had
the backfill been applied on 2026-08-03, 63 containers would carry IDs and would
show it in their timestamps.

So the ID is knowable, recorded by ops, and reachable by a command that was
written for exactly this — and the command has never been run.

**Evidence**
```python
active = (InTransitShipment.objects.filter(region='usa')
          .exclude(status__in=['received', 'cancelled']))
active.count()                            # 11
active.exclude(shipment_id='').count()    # 0  — nothing for either sync to poll
InTransitShipment.objects.exclude(shipment_id='').count()          # 14, all archived
InTransitLine.objects.filter(amazon_received_units__gt=0).count()  # 0 of 2,615
InTransitShipment.objects.exclude(amazon_synced_at=None).count()   # 0
# every active container is AWD-bound, so every one of them has a STAR- shipment
{sh.destination.code for sh in active}                             # {'AWD-USA'}
```
`8205fb1` records the backfill's dry run: 73 usable rows, 63 to link, 7 already
set. Migration `0010`, which adds the receipt fields, was applied 2026-08-03 —
the machinery is three days old.

**Evidence**
```python
active = (InTransitShipment.objects.filter(region='usa')
          .exclude(status__in=['received', 'cancelled']))
active.count()                            # 11
active.exclude(shipment_id='').count()    # 0  — nothing for either sync to poll
InTransitShipment.objects.exclude(shipment_id='').count()          # 14
InTransitShipment.objects.exclude(shipment_id='').exclude(
    status__in=['received', 'cancelled']).count()                  # 0
InTransitLine.objects.filter(amazon_received_units__gt=0).count()  # 0 of 2,615
InTransitShipment.objects.exclude(amazon_synced_at=None).count()   # 0
```

**Business impact** — 92,130 units across 188 lines are counted as fully inbound
until somebody receives them by hand, which overstates cover and suppresses
reorder suggestions exactly when they are needed. No shortfall can be measured,
so a claim against Amazon has no supporting number. Five of the 11 are already
past their ETA.

**Technical impact** — None from receiving itself. The mechanism works; it has
nothing to work on. The consequence is that every receiving code path is
unexercised, so `INV-RECV-003` and `INV-RECV-004` sit undiscovered rather than
visible.

**Recommendation** — Confirm against production before doing anything: this
register's evidence is the local database (see the note at the top of this
file), and if ops have been entering IDs on the live system there is nothing
here to fix. If production matches:

1. Run `backfill_container_shipment_ids` against the current Containers Summary
   workbook. It reports before it writes and never replaces an existing ID, so
   the dry run costs nothing. This is the whole fix for the 11 containers on the
   water, and it is an operational step, not a code change.
2. Have ops record the ID when the inbound is booked, not when the container
   lands. The Containers Summary workbook already holds it; the Transit sheet's
   row 6 does not, which is why the seed import could not carry it.
3. **Then** the code change: warn on an active AWD- or FBA-bound container with
   no shipment ID. The absence is currently silent, which is how a whole feature
   came to sit idle without anyone noticing.

Step 3 is the only code in this list, and on its own it fixes nothing — it only
makes the next occurrence visible.

**Related documents** — [receiving.md](receiving.md), [containers.md](containers.md)
**Related decisions** — `INV-D-009`

---

## `INV-RECV-002` · Archived containers with no count report as a total loss

| | |
|---|---|
| **Priority** | P1 |
| **Status** | open |
| **Classification** | legacy data, surfaced by a display defect |
| **Code alone fixes it** | yes — the reported figure is wrong, not the data |
| **Dependencies** | none |

**Current behaviour** — Container History shows shipped, received and the
difference. 116 of the 120 archived containers were closed without a count from
either source, so received reads zero and the difference reads as the whole
container, in red.

**Expected behaviour** — A container archived without a count is reported as
**not counted**, distinct from a container counted at zero. Only a real count
produces a discrepancy figure.

**Root cause** — These containers were never received by anybody. The
status-workbook import synthesises the status from the dates alone — an ETA more
than 21 days old becomes `received`, with `received_date` set to the estimated
arrival — and creates lines carrying packed units and nothing else. There was no
count to record, because the containers landed before the system existed: their
arrival dates run from 2023-04-10, and the app's first migration is 2026-07-20.

The display then treats "no count recorded" and "counted zero" as the same
thing, and reports the difference against packed as a loss.

**Evidence**
```python
arch = InTransitShipment.objects.filter(status='received')     # 120
zero = [s for s in arch if s.total_received == 0]              # 116
sum(s.total_units for s in zero)                               # 1,245,478
sum(1 for s in zero if s.received_by_id is None)               # 116 — no UI receipt
sum(1 for s in zero if s.received_at is None)                  # 116 — no audit trail
sum(1 for s in zero if s.received_date == s.eta_destination)   # 116 — the importer's signature
min(s.received_date for s in zero)                             # 2023-04-10
```
The 4 exceptions each carry a real audit trail — received by Farhan on
2026-07-21 and 2026-07-24, through the UI — and every one shows counted exactly
equal to packed. `importer.py` sets `received_date=eta_dest` for anything the
date rule marks received; that equality is what distinguishes an imported
container from a received one.

**Business impact** — The history page reports 1,245,478 units lost that were
not lost. Any figure taken from that page — loss rate, supplier performance,
claim totals — is wrong by the whole of it.

**Technical impact** — Small. The distinction is between a zero and an absent
value, which the data already supports: no count exists in either column, and
the empty `received_by`/`received_at` pair identifies the rows exactly.

**Recommendation** — Show "—" rather than a discrepancy where neither count
exists, and exclude those containers from any aggregate over discrepancies.
**Do not backfill counts.** The units were counted, if at all, in a warehouse
years ago; no record of the count exists anywhere, and a manufactured figure
would be indistinguishable from a real one.

**Related documents** — [receiving.md](receiving.md), [containers.md](containers.md)
**Related decisions** — `INV-D-006`, `INV-D-008`

---

## `INV-RECV-003` · Per-SKU variance views ignore Amazon's count

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | bug |
| **Code alone fixes it** | yes |
| **Dependencies** | `INV-RECV-001` (nothing has an Amazon count until it is fixed) |

**Current behaviour** — Two per-SKU views read the human count field alone:
expanding an archived container in Container History, and Tab B of the Goods
Receipt page. The container's own discrepancy on the row above reads the derived
figure, which falls back to Amazon's count. For a container Amazon closed by
itself the two disagree: the row is right and every SKU under it reads as a
total loss.

**Expected behaviour** — Everything reporting a shortfall reads the same derived
figure. See `INV-D-006`.

**Evidence** — In `api_container_history`, the row's `discrepancy` sums
`l.counted_units - l.units` while its `lines[].received` is `l.received_units`.
`build_variance` computes `disc = l.received_units - l.units` for the same
reason. This is the failure `INV-CONT-006` closed at container level, left in
place everywhere below it.

**Business impact** — None today: no container carries an Amazon count, so
neither figure exists. It becomes visible the moment `INV-RECV-001` is fixed and
the first container is auto-closed — and Goods Receipt is where a variance
reason gets attributed, so a wrong figure there becomes a wrong claim.

**Technical impact** — Two readings of one concept, one of them inside a single
function, which is what `counted_units` exists to prevent.

**Recommendation** — Read `counted_units` at both sites. Do the history change
with `INV-RECV-002`, which touches the same response.

**Related documents** — [receiving.md](receiving.md), [containers.md](containers.md)
**Related decisions** — `INV-D-006`

---

## `INV-RECV-004` · A SKU with nothing received reports no shortfall

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | bug |
| **Code alone fixes it** | yes |
| **Dependencies** | `INV-RECV-001` |

**Current behaviour** — On the Receiving page, a SKU's shortfall is suppressed
when Amazon has counted none of it. A line with 1 of 1,000 received reports a
shortfall of 999; the same line with 0 of 1,000 reports zero, and sorts to the
bottom of the list. The container-level variance does include it, so the SKU
column does not sum to the container figure above it.

**Expected behaviour** — Once a container has started being counted, every SKU
reports packed minus received. A SKU nobody has counted is the most likely to be
missing entirely, not the least.

**Evidence** — In `api_receiving`, the per-line figure is
`max(0, b - c) if c else 0`. `InTransitLine.shortfall_units` carries no such
condition, so the model and the page disagree.

**Business impact** — The one SKU that arrived not at all is the one hidden.
None today, because nothing reaches Receiving (`INV-RECV-001`).

**Technical impact** — The page recomputes a figure the model already derives,
and derives it differently.

**Recommendation** — Drop the condition and use the container's `started` flag,
which already exists and already gates the container-level variance for exactly
this reason.

**Related documents** — [receiving.md](receiving.md)
**Related decisions** — `INV-D-001`

---

## `INV-RECV-005` · Receipt syncs are neither region-filtered nor scheduled outside the USA

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | missing implementation (the filter) and configuration (the schedule) |
| **Code alone fixes it** | no — the schedule is a cron change, and see `INFRA-001` |
| **Dependencies** | none |

**Current behaviour** — `--marketplace` selects which SP-API credentials to use
and nothing else; neither sync filters containers by region. Both are scheduled
for `usa` only. A UK container would therefore never be polled, and running
either command for another marketplace would query USA containers against that
marketplace's credentials.

**Expected behaviour** — A sync polls the containers of the region whose
credentials it is using, and every region with containers is scheduled.

**Evidence** — Neither `sync_awd_receipts` nor `sync_fba_receipts` references
`region` in its queryset. `deploy/crontab.txt` schedules both with
`--marketplace usa`. 131 of 131 containers are region `usa`, which is why
nothing has gone wrong.

**Business impact** — None today. It becomes a wrong-credentials lookup the
first time a non-USA container is created, which the Receiving page already
invites by offering all four regions.

**Technical impact** — A command whose scoping argument does not scope. AWD is
USA-only, so the FC sync is the one that needs the other regions.

**Recommendation** — Filter both querysets on `region=marketplace`, and schedule
`sync_fba_receipts` per region once a non-USA container exists. Do it with the
first non-USA container, not before.

**Related documents** — [receiving.md](receiving.md), deployment.md *(pending)*

---

## `INV-SUP-001` · Opening balance has no rate

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Dependencies** | none |

**Current behaviour** — Opening-balance units count toward a supplier's Balance
but contribute nothing to Outstanding FOB, because there is no rate to price
them at.

**Expected behaviour** — Opening balance carries a per-unit rate, and the money
column matches the units column.

**Evidence** — In `api_suppliers`, `remaining` includes the opening figure while
`value` accumulates only from PO lines.

**Business impact** — Outstanding FOB understates by the whole backlog. Invisible
today only because no opening balance has been uploaded yet.

**Technical impact** — One field on the opening-balance record, a column on the
template, and the value rolled into two aggregations.

**Recommendation** — Add the rate. Note it is **not** needed for cash flow —
that is solved by the packing-list FOB (`INV-D-004`) — so this is purely about
the Suppliers page.

**Related documents** — suppliers.md *(pending)*
**Related decisions** — `INV-D-004`

---

## `INV-SUP-002` · `POLineGroup.pcs` is written and never read

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Dependencies** | none |

**Current behaviour** — The PO import writes a `pcs` value that no view,
template or calculation ever reads. It duplicated Units.

**Expected behaviour** — The column does not exist.

**Evidence** — `grep -rn "\.pcs\b" apps templates` returns no read sites.

**Business impact** — None.

**Technical impact** — A field that looks meaningful and is not. The upload no
longer asks for it and falls back to Units.

**Recommendation** — Drop the column in a migration next time the PO models are
touched.

**Related documents** — purchase-orders.md *(pending)*

---

## `INV-CASH-001` · Opening-balance backlog never reaches cash flow

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Dependencies** | `INV-SUP-001` |

**Current behaviour** — Cash-flow outflows are built from containers. Opening
balance has no container and no PO, so no payment is ever scheduled for it.

**Expected behaviour** — Backlog owed to a supplier appears in the forecast on
its expected payment date.

**Evidence** — `refresh_region()` iterates containers only.

**Business impact** — The forecast omits money genuinely owed, so the lowest
projected balance is optimistic.

**Technical impact** — Needs a payment date for backlog, which no record
currently holds.

**Recommendation** — Decide the business rule first: backlog is paid when the
units ship, in which case it reaches cash flow via the container that carries
them and this closes itself once `INV-CONT-002` is built. Confirm before
building anything.

**Related documents** — cashflow.md *(pending)*, suppliers.md *(pending)*

---

## Closed

| ID | Title | Closed by |
|---|---|---|
| `INV-CONT-005` | Cash flow priced every container at zero | `fd6af91` |
| `INV-CONT-006` | Auto-closed containers reported as a total loss in history | `90f011b` |
| `INV-CONT-007` | In Transit and Receiving both listed the same container | `6d587f4` |
| `INV-CONT-008` | FC containers never produced receipts | `4febb33` |
| `INV-CONT-009` | No way to add a 3PL warehouse from the shipment form | `d1b7e56` |
| `INV-CONT-010` | Container delete was unreachable and reported success on failure | `4febb33` |
| `INV-SUP-003` | No way to add a supplier | `d885737` |
| `INV-ALLOC-001` | A second supplier's upload silently replaced the first | `ad3e6dd` |
| `INV-ALLOC-002` | Re-upload made a container compete with its own allocation | `ad3e6dd` |
