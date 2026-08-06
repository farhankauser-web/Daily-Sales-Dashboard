# Inventory — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

| ID | Title | Priority | Status |
|---|---|---|---|
| `INV-CONT-001` | In-transit lines carry no FOB rate | P1 | open |
| `INV-CONT-002` | Opening balance is not consumable | P1 | open |
| `INV-CONT-003` | No stall alert for a container stuck in Receiving | P2 | open |
| `INV-CONT-004` | Goods receipt writes AWD stock the sync overwrites | P3 | open |
| `INV-SUP-001` | Opening balance has no rate, so Outstanding FOB understates | P2 | open |
| `INV-SUP-002` | `POLineGroup.pcs` is written and never read | P3 | open |
| `INV-CASH-001` | Opening-balance backlog never reaches cash flow | P2 | open |

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

**Related documents** — receiving.md *(pending)*, [containers.md](containers.md)
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

**Related documents** — receiving.md *(pending)*, transfers.md *(pending)*

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
