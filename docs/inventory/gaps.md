# Inventory — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

| ID | Title | Priority | Status |
|---|---|---|---|
| `INV-CONT-001` | In-transit lines carry no FOB rate | P1 | open |
| `INV-CONT-002` | Opening balance is not consumable | P1 | open |
| `INV-RECV-001` | No active container carries an Amazon shipment ID | P1 | open |
| `INV-RECV-002` | Archived containers with no count report as a total loss | P1 | open |
| `INV-CONT-003` | No stall alert for a container stuck in Receiving | P2 | open |
| `INV-RECV-003` | Per-SKU variance views ignore Amazon's count | P2 | open |
| `INV-RECV-004` | A SKU with nothing received reports no shortfall | P2 | open |
| `INV-SUP-001` | Opening balance has no rate, so Outstanding FOB understates | P2 | open |
| `INV-CASH-001` | Opening-balance backlog never reaches cash flow | P2 | open |
| `INV-CONT-004` | Goods receipt writes AWD stock the sync overwrites | P3 | open |
| `INV-RECV-005` | Receipt syncs are neither region-filtered nor scheduled outside the USA | P3 | open |
| `INV-SUP-002` | `POLineGroup.pcs` is written and never read | P3 | open |

Closed gaps are at the end. They keep their ids and their rows.

---

## `INV-CONT-001` · In-transit lines carry no FOB rate

| | |
|---|---|
| **Priority** | P1 |
| **Status** | open — deliberate, see `INV-D-007` |
| **Dependencies** | none |

**Current behaviour** — 188 of 188 active USA container lines have no FOB rate
and no PO link, so every container in transit prices at zero in cash flow.

**Expected behaviour** — Every container line carries a rate, so the region
ledger shows what we actually owe.

**Evidence**
```python
InTransitLine.objects.filter(shipment__in=active_usa, po_line__isnull=True).count()
# 188, of 188 total
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

**Related documents** — [containers.md](containers.md), cashflow.md *(pending)*
**Related decisions** — `INV-D-004`, `INV-D-005`, `INV-D-007`

---

## `INV-CONT-002` · Opening balance is not consumable

| | |
|---|---|
| **Priority** | P1 |
| **Status** | open — decided, not built |
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
| **Dependencies** | none |

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

## `INV-RECV-001` · No active container carries an Amazon shipment ID

| | |
|---|---|
| **Priority** | P1 |
| **Status** | open |
| **Dependencies** | none |

**Current behaviour** — Both receipt syncs iterate open containers that carry an
Amazon shipment ID. There are none. Every container that has ever been linked is
already archived, so the Receiving page is permanently empty and no container
has ever been reconciled against Amazon's count.

**Expected behaviour** — Every container dispatched under an Amazon shipment
carries that shipment's ID from creation, so its receipts arrive on the next
sync.

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
so a claim against Amazon has no supporting number.

**Technical impact** — None. The mechanism works; it has nothing to work on.
The consequence is that every receiving code path is unexercised in production,
so `INV-RECV-003` and `INV-RECV-004` sit undiscovered rather than visible.

**Recommendation** — Two parts. Make the shipment ID part of creating a
container rather than an edit afterwards, and run
`backfill_container_shipment_ids` against the current Containers Summary
workbook for the 11 open containers. The backfill reports before it writes and
never replaces an existing ID.

**Related documents** — [receiving.md](receiving.md), [containers.md](containers.md)
**Related decisions** — `INV-D-009`

---

## `INV-RECV-002` · Archived containers with no count report as a total loss

| | |
|---|---|
| **Priority** | P1 |
| **Status** | open |
| **Dependencies** | `INV-RECV-001` (prevents recurrence, does not fix the history) |

**Current behaviour** — Container History shows shipped, received and the
difference. 116 of the 120 archived containers were closed without a count from
either source, so received reads zero and the difference reads as the whole
container, in red.

**Expected behaviour** — A container archived without a count is reported as
**not counted**, distinct from a container counted at zero. Only a real count
produces a discrepancy figure.

**Evidence**
```python
arch = InTransitShipment.objects.filter(status='received')     # 120
sum(1 for sh in arch if sh.total_received == 0)                # 116
sum(sh.total_units for sh in arch if sh.total_received == 0)   # 1,245,478
```

**Business impact** — The history page reports 1,245,478 units lost that were
not lost. Any figure taken from that page — loss rate, supplier performance,
claim totals — is wrong by the whole of it.

**Technical impact** — Small. The distinction is between a zero and an absent
value, which the data already supports: no count exists in either column.

**Recommendation** — Show "—" rather than a discrepancy where neither count
exists, and exclude those containers from any aggregate over discrepancies.
Do not backfill counts — the units were received years of containers ago and
no record of the count survives.

**Related documents** — [receiving.md](receiving.md), [containers.md](containers.md)
**Related decisions** — `INV-D-006`, `INV-D-008`

---

## `INV-RECV-003` · Per-SKU variance views ignore Amazon's count

| | |
|---|---|
| **Priority** | P2 |
| **Status** | open |
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
