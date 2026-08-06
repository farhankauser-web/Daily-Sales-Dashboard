# Inventory — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

Never edit a decision to change its meaning. Add a new one, mark the old
`superseded by`, and say what changed.

| ID | Decision | Date | Status |
|---|---|---|---|
| `INV-D-001` | Packed is the truth, not declared | 2026-08-03 | accepted |
| `INV-D-002` | One packing list describes a whole container | 2026-08-05 | accepted |
| `INV-D-003` | Uploads ask for the SKU only | 2026-08-04 | accepted |
| `INV-D-004` | FOB per unit, in the region's currency, required | 2026-08-05 | accepted |
| `INV-D-005` | FOB is snapshotted at allocation | 2026-08-05 | accepted |
| `INV-D-006` | Amazon's count never overwrites a human count | 2026-08-05 | accepted |
| `INV-D-007` | Existing containers are not backfilled — forward only | 2026-08-05 | accepted |
| `INV-D-008` | Amazon closing a shipment archives the container; no loss is valued | 2026-08-05 | accepted |
| `INV-D-009` | In Transit and Receiving are a partition, keyed off receipts | 2026-08-04 | accepted |
| `INV-D-010` | Re-uploading a packing list replaces the lines | 2026-08-05 | accepted |
| `INV-D-011` | Opening balance is consumed before PO balance | 2026-08-05 | accepted, not built |

---

## `INV-D-001` · Packed is the truth, not declared

| | |
|---|---|
| **Date** | 2026-08-03 · **Status** accepted |

**Context** — A shipment is created on Amazon to generate labels, then the
container is packed. The two rarely match: the packing list is finalised after
the label, so what ships differs from what was declared.

**Decision** — Variance is **packed − received**. The declared figure is
recorded and shown, never used to compute loss.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Use Amazon's declared figure | We always declare at least what we pack, so it invents a shortage that does not exist |
| Reconcile to whichever is lower | Hides real losses whenever the declaration was low |

**Reason** — Business call. The packing list is what physically left the
factory, and it is the number a claim is argued from.

**Consequences** — Amazon's own discrepancy report will look worse than ours,
and that difference must be explained rather than reconciled away. Over-declaring
is reported separately so it is visible but harmless.

**Affected documents** — [containers.md](containers.md), receiving.md *(pending)*

---

## `INV-D-002` · One packing list describes a whole container

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — 17 of 131 containers carry goods from two factories. The upload
originally took one supplier from a form, so a second factory needed a second
upload.

**Decision** — The packing list carries a **Supplier column per row**. One file
describes the container, whoever made the goods. The form no longer asks.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Append mode — upload each supplier separately | Asks the user to split a document that is not split in reality, and forgetting step two leaves a container short with no signal |
| One container number per supplier | Misrepresents the physical container |

**Reason** — A container is one physical thing with one packing list. The file
should match the document.

**Consequences** — Re-upload replaces (`INV-D-010`), so the file must always be
complete. An unrecognised supplier is refused by name rather than guessed.

**Affected documents** — allocation-workbench.md *(pending)*, [containers.md](containers.md)

---

## `INV-D-003` · Uploads ask for the SKU only

| | |
|---|---|
| **Date** | 2026-08-04 · **Status** accepted |

**Context** — Templates asked for Category, product Name and other values the
catalogue already holds against the SKU, and the two could disagree.

**Decision** — Anything derivable from the SKU is **derived**: category, product
name, FNSKU. A typed value on an older file still wins, so nothing breaks.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Keep the columns, validate them | Still two sources of one fact; validation only reports the disagreement |
| Derive, and reject files that carry the column | Breaks every file already in use |

**Reason** — One source of truth per fact. The duplicate column exists only to
be wrong.

**Consequences** — A SKU the catalogue has never seen cannot be enriched, so it
is named back to the user rather than landing blank.

**Affected documents** — allocation-workbench.md *(pending)*, purchase-orders.md *(pending)*, suppliers.md *(pending)*

---

## `INV-D-004` · FOB per unit, in the region's currency, required

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — Cash flow priced every container at zero. Container payments need
a rate, and each region's ledger is denominated in that region's currency.

**Decision** — The packing list carries **FOB per unit** in the **region's**
currency. It is **required**; a missing rate is refused by SKU.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Infer from the PO when blank | PO rates are in the *supplier's* currency. A UK container would inherit a USD rate into a GBP ledger and understate by about a quarter |
| Enter in supplier currency and convert | Requires an FX rate per container per date; nobody wants to maintain it |
| Optional, default zero | A container priced at zero silently understates what we owe — the failure we were fixing |

**Reason** — Business call: each region uploads in its own currency so nothing
is ever converted.

**Consequences** — Rates are typed for every container. The template header
names the currency it expects. FOB must never be summed across regions.

**Affected documents** — allocation-workbench.md *(pending)*, cashflow.md *(pending)*

---

## `INV-D-005` · FOB is snapshotted at allocation

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — The rate could be read live from the PO whenever a container is
priced.

**Decision** — The rate is **copied onto the container line** when the container
is created, and never re-read.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Read live from the PO line | A PO re-imported at a new price would silently restate the cost of a container that shipped months earlier |
| Read live, keep a price history on the PO | Solves it, at the cost of a versioned price table nobody asked for |

**Reason** — Accounting: a container shipped in May keeps May's price. It also
lets a line be priced when it resolves to no PO at all.

**Consequences** — Correcting a rate means re-uploading the packing list. Two
containers can legitimately carry different rates for the same SKU.

**Affected documents** — [containers.md](containers.md), cashflow.md *(pending)*

---

## `INV-D-006` · Amazon's count never overwrites a human count

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — Two sources count the same units: the Goods Receipt screen, and
the Amazon receipt syncs.

**Decision** — They are stored in **separate fields**. Where both exist the
human figure wins; where only Amazon's exists, that is used.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| One field, last write wins | A sync would erase somebody's physical count |
| Prefer Amazon always | Amazon is the seller's record, not the warehouse's; a disagreement is itself worth seeing |

**Reason** — A disagreement between the two is information, and destroying one
side destroys it.

**Consequences** — Anything reporting a shortfall must read the derived figure,
not the manual field. Reading the manual field alone made every auto-closed
container look like a total loss.

**Affected documents** — [containers.md](containers.md), receiving.md *(pending)*

---

## `INV-D-007` · Existing containers are not backfilled — forward only

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — 188 in-transit lines predate the FOB column and have no rate.

**Decision** — They stay unpriced. Only containers created from now on carry
rates.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Backfill a rate by SKU from the PO or opening balance | Produces a number without the mechanism — the balance still is not drawn down, and the attribution stays fake |
| Re-upload all 11 containers now | Real work; deferred, not rejected. Still the only route to real numbers |

**Reason** — Business call: not worth the disruption today.

**Consequences** — Cash flow understates by those containers until their packing
lists are re-uploaded. Tracked as `INV-CONT-001` so the understatement is known
rather than discovered.

**Affected documents** — cashflow.md *(pending)*, [gaps.md](gaps.md)

---

## `INV-D-008` · Amazon closing a shipment archives the container

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — Closing a container is an accounting act: the difference between
shipped and counted becomes a loss.

**Decision** — When Amazon reports CLOSED the container moves to history
automatically. **The app values nothing.** Units short remain on the line as
packed minus counted; the separate COGS system values them.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Require a human to close | Containers sit in Receiving indefinitely; the five that prompted this had been closed by Amazon for weeks |
| Book a write-off automatically | Needs a unit cost and a claim lifecycle the business does not want here |

**Reason** — Business call: cost of lost units is handled elsewhere, by a
system that already classifies loss types.

**Consequences** — Shortfall is visible in history but never valued in this app.
A container that Amazon never closes needs a stall alert — `INV-CONT-003`.

**Affected documents** — [containers.md](containers.md), receiving.md *(pending)*

---

## `INV-D-009` · In Transit and Receiving are a partition

| | |
|---|---|
| **Date** | 2026-08-04 · **Status** accepted |

**Context** — A container appeared in both tabs at once. Membership was decided
by a status field that only moves when an optional flag is set.

**Decision** — Membership is derived from the **receipts themselves**: a
container with any counted units, or the receiving status, belongs to Receiving.
In Transit is everything else active.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Fix the status transitions and key off status | Depends on an optional flag being enabled; a container with real receipts would still show in both |
| Show it in both, labelled | The duplication is what the Receiving stage exists to remove |

**Reason** — A derived partition cannot drift. A stored one can.

**Consequences** — A container moves the moment Amazon counts a unit, whatever
its status says. The planner is unaffected — it counts the un-received remainder
either way.

**Affected documents** — [containers.md](containers.md), receiving.md *(pending)*

---

## `INV-D-010` · Re-uploading a packing list replaces the lines

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — Uploading the same container number twice had to do something
predictable.

**Decision** — It **replaces** the container's lines and releases the previous
allocation.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Append | Uploading the same file twice would double the container. With `INV-D-002` there is nothing to append |
| Refuse a second upload | Correcting a packing list is routine |

**Reason** — The file is the complete statement of what is in the container.

**Consequences** — Always send the full list; a partial re-upload silently drops
what it omits. The form says so, and re-upload releases the container's own
units first so it does not compete with itself.

**Affected documents** — allocation-workbench.md *(pending)*

---

## `INV-D-011` · Opening balance is consumed before PO balance

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted, **not built** |

**Context** — Opening balance is backlog a supplier owed before the system went
live. Today it is a static display figure that nothing draws down, so a packing
list deducts from PO balances even when backlog exists.

**Decision** — A packing list draws from **opening balance first**, oldest first,
then from PO lines FIFO.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Leave opening balance as a display figure | Balances overstate for as long as backlog exists |
| Convert opening balance into a synthetic PO | Invents a purchase order that was never issued, and pollutes PO reporting |

**Reason** — Business call: it reflects how the factory actually settles — old
commitments first.

**Consequences** — Opening balance becomes a consumable bucket, which means it
needs allocation tracking, and re-uploading a balance that has been drawn against
must be refused rather than replacing it. Tracked as `INV-CONT-002`.

**Affected documents** — suppliers.md *(pending)*, allocation-workbench.md *(pending)*
