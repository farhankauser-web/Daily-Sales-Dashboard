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
| `INV-ALLOC-003` | The container-manifest import strips FOB and PO attribution | P2 | bug | open |
| `INV-PLAN-001` | Lead times exist twice, and the two disagree | P2 | bug | open |
| `INV-SUP-001` | Opening balance has no rate, so Outstanding FOB understates | P2 | missing implementation | open |
| `INV-SUP-004` | The PO upload takes free text for the supplier and mints one on a typo | P2 | bug | open |
| `INV-CASH-001` | Opening-balance backlog never reaches cash flow | P2 | missing implementation · blocked | blocked |
| `INV-CONT-004` | Goods receipt writes AWD stock the sync overwrites | P3 | bug | open |
| `INV-RECV-005` | Receipt syncs are neither region-filtered nor scheduled outside the USA | P3 | missing implementation · configuration | open |
| `INV-ALLOC-004` | Append mode is unreachable and its docstring misleads | P3 | bug | open |
| `INV-PLAN-002` | The supplier-choice docstring describes a rule the code does not follow | P3 | bug | open |
| `INV-SUP-002` | `POLineGroup.pcs` is written and never read | P3 | legacy schema | open |

Every gap carries a classification and says whether a code change alone would
close it.

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

**Related documents** — [containers.md](containers.md), [cashflow.md](cashflow.md)
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

**Related documents** — [suppliers.md](suppliers.md), [allocation-workbench.md](allocation-workbench.md)
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

**Related documents** — [receiving.md](receiving.md), [transfers.md](transfers.md)
**Related decisions** — `INV-D-018` (the rule this path breaks)

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

**Evidence** — source: **code**. In `api_container_history`, the row's
`discrepancy` sums `l.counted_units - l.units` while its `lines[].received` is
`l.received_units`. `build_variance` computes `disc = l.received_units - l.units`
for the same reason. And the model layer itself: `POLine.transit_shortage`,
`over_receipt` and `receipt_variance` all sum `a.received_units` over received
containers — so an auto-closed container reports its whole allocation as
transit shortage on the Goods Receipt variance screens. This is the failure
`INV-CONT-006` closed at container level, left in place everywhere below it.

**Business impact** — None today: no container carries an Amazon count, so
neither figure exists. It becomes visible the moment `INV-RECV-001` is fixed and
the first container is auto-closed — and Goods Receipt is where a variance
reason gets attributed, so a wrong figure there becomes a wrong claim.

**Technical impact** — Two readings of one concept, one of them inside a single
function, which is what `counted_units` exists to prevent.

**Recommendation** — Read `counted_units` at every site: the history drawer,
`build_variance`, and the three `POLine` shortage properties. Do the history
change with `INV-RECV-002`, which touches the same response.

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

## `INV-PLAN-001` · Lead times exist twice, and the two disagree

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | bug — two sources for one concept |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — Lead time is held in two places. `planning.LEAD_LEGS`
hardcodes it per **region** — production 90, sea 45 or 15, port-to-warehouse 10
— and drives the planner's order-by and ship-by dates and the loading plan's
target pipeline. `Supplier.production_lead_days`, `sea_lead_days` and
`port_to_wh_days` are per **supplier**, editable on the Suppliers page, and
drive the reorder engine's target-ready date and the sourcing view's lead
column.

So the date telling you *when to order* is computed from a constant that ignores
which factory will make it, while the date telling you *when it will be ready*
uses that factory's own figure.

**Expected behaviour** — One source. Production lead belongs to the supplier —
it is a fact about a factory. The shipping legs belong to the region lane — sea
time is a fact about a route, not a factory. The planner should combine the two
rather than carry a second copy of production lead.

**Root cause** — The planner was built first, against ops' spreadsheet, which
used flat regional assumptions. Per-supplier lead times arrived with the
supplier registry and were wired into the machines built after it — reorder and
sourcing. The planner was never revisited, so both survive.

**Evidence** — source: **code**, so it holds in production.
```python
# planning.py — region constants, drive order_by / ship_by / loading plan
LEAD_LEGS = {'usa': {'production': 90, 'sea': 45, 'port_to_wh': 10}, ...}

# reorder.py — the supplier's own figure, dates the suggestion
lead = supplier.production_lead_days if supplier else 90
```

**Business impact** — Editing a supplier's lead times on the Suppliers page
changes reorder dates and the sourcing view but **not** the order-by date the
planner shows, which is the number a planner actually acts on. A factory that
genuinely takes 120 days still shows a 90-day order-by, so the order is placed
a month late and the page gives no hint. The reverse — a fast factory — shows a
false urgency.

*Provisional, dev snapshot:* all 13 suppliers currently sit at the defaults
90/45/10, which happen to equal the USA constants, so nothing disagrees today.
The divergence appears the moment anyone edits a supplier. Re-measure on
production.

**Technical impact** — Two definitions of one business concept, in two modules,
with no comment in either acknowledging the other.

**Recommendation** — Make the planner read the supplier's production lead for
each SKU — via the same "best supplier" resolution reorder already uses — and
keep the sea and port-to-warehouse legs as region constants, which is what they
correctly are. Where a SKU resolves to no supplier, fall back to the region
constant and say so in the row.

**Related documents** — [planner.md](planner.md), [reorder.md](reorder.md), [suppliers.md](suppliers.md)

---

## `INV-PLAN-002` · The supplier-choice docstring describes a rule the code does not follow

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | bug — stale documentation in code |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — `_supplier_and_fob` in `reorder.py` is documented as
"the one holding open PO balance → else the most recent PO supplier → else
cheapest historical", which reads as a three-step priority chain. The code
narrows to the suppliers holding open balance — or every supplier that has ever
supplied the SKU, where none does — and then picks the **cheapest** within that
pool, ties broken by the most recent order.

The difference matters: given two suppliers both holding open balance, the
docstring implies the more recent one wins, and the code picks the cheaper one.

**Expected behaviour** — The docstring states the rule the code implements.

**Root cause** — The docstring describes an earlier design. The selection was
later narrowed to a pool-then-cheapest form — the inline comment one line above
the `min()` says so correctly — and the docstring above it was not updated.

**Evidence** — source: **code**.
```python
open_lines = [l for l in lines if l.remaining_units > 0 and l.po.status not in (...)]
pool = open_lines or lines
best = min(pool, key=lambda l: (float(l.group.fob_rate) or 9e9))
```
There is no branch on recency; `order_by('-po__order_date')` only makes the
`min()` stable, which is the tie-break.

**Business impact** — None to the running system: the code's behaviour is the
defensible one.

**Technical impact** — Real and demonstrated. This docstring was taken at face
value while writing [suppliers.md](suppliers.md) and [reorder.md](reorder.md),
and put the wrong rule into both; it was caught only on re-reading the function
during the section review. A comment that contradicts its function is worse than
none, because it is trusted.

**Recommendation** — Rewrite the docstring: candidate pool is the open-balance
holders, else everyone who has supplied it; cheapest agreed rate wins; a zero
rate sorts last, so an unpriced line never wins on price. Two lines, and it
stops the next reader repeating the mistake.

**Related documents** — [reorder.md](reorder.md), [suppliers.md](suppliers.md)

---

## `INV-ALLOC-003` · The container-manifest import strips FOB and PO attribution

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | bug — data loss |
| **Code alone fixes it** | yes |
| **Dependencies** | none. Same shape as `INV-CONT-011`, but this one is exposed in the UI |

**Current behaviour** — Uploading a container manifest deletes and rebuilds the
lines of every container it names. The manifest carries container number,
vendor, destination, two dates, status, SKU and units — and nothing else — so
the rebuilt lines lose the FOB rate, the PO line, the FNSKU, the human receipt
count, Amazon's count and any variance reason. The container row itself
survives, and with it the Amazon shipment ID.

**Expected behaviour** — An import updates what its file describes and leaves
the rest alone. A file with no FOB column is not a statement that the FOB is
zero.

**Root cause** — The manifest predates procurement. When it was written a
container line held a SKU and a quantity, so replacing the set of lines was the
same as replacing the units, and `update_or_create` on the container plus a
wholesale line rebuild was the simplest correct thing. Attribution, rates and
counts were later added to the same row, and the rebuild was never revisited.

Note the contrast with a packing-list re-upload, which also replaces lines
(`INV-D-010`) but **rebuilds** attribution and rates as it goes because the file
carries them. The manifest replaces without rebuilding.

**Evidence** — source: **code**, so this holds in production regardless of what
any database currently contains.
```python
# apps/inventory_planning/importer.py, import_containers()
sh.lines.all().delete()
InTransitLine.objects.bulk_create([
    InTransitLine(shipment=sh, sku=s, units=u) for s, u in g['lines'].items()])
```
The parser recognises only container, SKU, units, vendor, destination,
departure, ETA and status. There is no Supplier, PO, FOB or FNSKU column to
carry, and no field is preserved from the row being replaced.

**Business impact** — None measurable yet, and it is the same forward-looking
exposure as `INV-CONT-011`: it destroys precisely what the `INV-CONT-001` remedy
creates. Re-upload eleven packing lists to price the containers on the water,
then upload a routine manifest for any of them, and the rates are gone with no
warning. Unlike `INV-CONT-011` this path is reachable from two pages.

**Technical impact** — Two import paths and one upload path all "replace the
lines", meaning three different things by it. A reader cannot tell which is safe
without reading each.

**Recommendation** — Update lines in place by SKU instead of rebuilding: adjust
units, leave every other field untouched, and delete only lines the file drops.
Where a container already carries PO attribution, refuse the manifest and say
the packing list is the right file — a container built through the workbench
should not be edited by a file that cannot express what it holds.

**Related documents** — [allocation-workbench.md](allocation-workbench.md), [containers.md](containers.md)
**Related decisions** — `INV-D-005`, `INV-D-010`

---

## `INV-ALLOC-004` · Append mode is unreachable and its docstring misleads

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | bug — stale documentation on a superseded path |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — `commit_packing_list` supports `mode='append'` and both
endpoints accept it, but no page offers it: the mode selector was removed in
`abe7df2` once the packing list carried a Supplier column per row. The
docstring still describes append as "how a container loaded from two suppliers
is recorded: upload supplier A, then append supplier B" — the exact workflow
`INV-D-002` replaced.

**Expected behaviour** — The code says what the business does. One file
describes the whole container.

**Root cause** — Deliberate removal, incompletely finished. `ad3e6dd` added
append to fix a second supplier's upload silently replacing the first
(`INV-ALLOC-001`); `0cd9e7a` then made the packing list carry Supplier per row,
which solved the same problem better; `abe7df2` dropped the selector and said
so. The service layer and its docstring were left as they were.

**Evidence** — source: **code**. `grep -c append templates/inventory_planning/allocation.html`
returns 0, while `commit_packing_list` still branches on `mode == 'append'` and
documents it as the two-supplier route.

**Business impact** — None. The path cannot be reached from the UI.

**Technical impact** — The most authoritative-looking comment in the module
describes a workflow the decision log forbids. A reader who follows it
reintroduces a two-upload flow whose second half is easy to forget, which is
what `INV-D-002` exists to prevent.

**Recommendation** — Decide one way and make the code agree. Either delete the
append path and its `mode` argument, or keep it as an API-only capability and
rewrite the docstring to say that one file describes the whole container and
append exists only for corrections. Deleting is preferable — nothing calls it.

**Related documents** — [allocation-workbench.md](allocation-workbench.md)
**Related decisions** — `INV-D-002`, `INV-D-010`

---

## `INV-SUP-001` · Opening balance has no rate

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | missing implementation |
| **Code alone fixes it** | mostly — the field and aggregation are code; the rates themselves must come from ops in the upload |
| **Dependencies** | none |

**Current behaviour** — Opening-balance units count toward a supplier's Balance
but contribute nothing to Outstanding FOB, because there is no rate to price
them at.

**Expected behaviour** — Opening balance carries a per-unit rate, and the money
column matches the units column.

**Root cause** — The opening-balance record was designed to answer "how many
units is the factory behind on", and it does. Money was added to the supplier
ledger later, priced from PO groups — which backlog has none of. No rate field
was ever added; nothing was removed or broken.

**Evidence** — In `api_suppliers`, `remaining` includes the opening figure while
`value` accumulates only from PO lines.

**Business impact** — Outstanding FOB understates by the whole backlog. Invisible
today only because no opening balance has been uploaded yet.

**Technical impact** — One field on the opening-balance record, a column on the
template, and the value rolled into two aggregations.

**Recommendation** — Add the rate. Note it is **not** needed for cash flow —
that is solved by the packing-list FOB (`INV-D-004`) — so this is purely about
the Suppliers page.

**Related documents** — [suppliers.md](suppliers.md)
**Related decisions** — `INV-D-004`

---

## `INV-SUP-004` · The PO upload takes free text for the supplier and mints one on a typo

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
| **Classification** | bug — an inconsistency with the intended rule |
| **Code alone fixes it** | yes |
| **Dependencies** | none |

**Current behaviour** — The PO upload form asks for the supplier as a free-text
field, and the import `get_or_create`s a supplier from whatever was typed. The
opening-balance and wastage uploads on the same page use a dropdown of existing
suppliers, and the packing list refuses an unknown name outright.

**Expected behaviour** — One rule everywhere: a supplier exists before anything
references it, and an unknown name is refused with near-matches suggested. See
`INV-D-015`.

**Root cause** — The PO import predates the rule. Implicit creation was the
convenience that bootstrapped the registry; the packing list then adopted
refusal (`0cd9e7a`), and Add Supplier made refusal workable by giving new
factories a front door (`d885737`). The PO path was never brought in line.

**Evidence** — source: **code**.
```python
# procurement.py, import_po_workbook()
code = ''.join(ch for ch in supplier_name.upper() if ch.isalnum())[:32]
supplier, _ = Supplier.objects.get_or_create(code=code, defaults={'name': supplier_name})
```
`suppliers.html` renders `<input name="supplier" placeholder="AKT" required>`
for the PO upload, against a `<select>` of known suppliers for the other two.
The code derivation absorbs punctuation and case ("J.Sons" and "j sons" both
fold to `JSONS`) but not a real typo: "Jsonss" mints `JSONSS`.

**Business impact** — A misspelt PO upload creates a phantom factory carrying a
real purchase order. Its balance appears under a name nobody recognises, the
genuine supplier's balance understates, and the packing list — which resolves
suppliers correctly — cannot draw against the misfiled PO.

**Technical impact** — Two contradictory answers to "what happens to an unknown
supplier name" in one module, one of which the docstring of `supplier_index`
explicitly forbids.

**Recommendation** — Replace the free-text field with the same dropdown the
other uploads use. `import_po_workbook` keeps `get_or_create` semantics for
callers but should refuse a name that resolves to no existing supplier, naming
near-matches the way the packing list does.

**Related documents** — [suppliers.md](suppliers.md), [purchase-orders.md](purchase-orders.md)
**Related decisions** — `INV-D-015`

---

## `INV-SUP-002` · `POLineGroup.pcs` is written and never read

| | |
|---|---|
| **Priority** | P3 |
| **Status** | open |
| **Classification** | legacy schema — dead field, no wrong behaviour |
| **Code alone fixes it** | yes — one migration |
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

**Related documents** — [purchase-orders.md](purchase-orders.md)

---

## `INV-CASH-001` · Opening-balance backlog never reaches cash flow

| | |
|---|---|
| **Priority** | P2 |
| **Status** | blocked — needs a business rule before anything is built |
| **Classification** | missing implementation, blocked on a business decision |
| **Code alone fixes it** | no — nobody has decided when backlog is paid, and no record holds a date |
| **Dependencies** | `INV-SUP-001` (a rate to value it), `INV-CONT-002` (may close this outright) |

**Root cause** — The forecast is assembled from containers because a container
is the only thing in the section carrying a payment date. Opening balance is
units owed with an as-of date, not a due date; no record anywhere states when
that money leaves the bank. This is not an oversight in the ledger — the
information does not exist to put in it.

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

**Related documents** — [cashflow.md](cashflow.md), [suppliers.md](suppliers.md)

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
