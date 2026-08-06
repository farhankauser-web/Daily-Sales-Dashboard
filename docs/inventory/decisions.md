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
| `INV-D-012` | Amazon's case pack wins over ours | 2026-08-05 | accepted |
| `INV-D-013` | One receipt sync per Amazon API, never merged | 2026-08-05 | accepted |
| `INV-D-014` | Units draw FIFO — oldest purchase order first | 2026-08-04 | accepted |
| `INV-D-015` | Suppliers are never created implicitly | 2026-08-05 | accepted, PO upload not yet compliant |
| `INV-D-016` | Wastage closes balance permanently | 2026-07-27 | accepted |
| `INV-D-017` | Demand is PDS where set, otherwise the 7-day average | 2026-07-21 | accepted |
| `INV-D-018` | Amazon stock is never written by hand | 2026-07-24 | accepted |
| `INV-D-019` | Amazon inflows are projected from settlements, not sales | 2026-07-24 | accepted |
| `INV-D-020` | A human edit locks a generated cash-flow row | 2026-07-24 | accepted |

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

**Affected documents** — [containers.md](containers.md), [receiving.md](receiving.md)

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

**Affected documents** — [allocation-workbench.md](allocation-workbench.md), [containers.md](containers.md)

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

**Affected documents** — [allocation-workbench.md](allocation-workbench.md), [purchase-orders.md](purchase-orders.md), [suppliers.md](suppliers.md)

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

**Affected documents** — [allocation-workbench.md](allocation-workbench.md), [cashflow.md](cashflow.md)

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

**Affected documents** — [containers.md](containers.md), [cashflow.md](cashflow.md)

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

**Affected documents** — [containers.md](containers.md), [receiving.md](receiving.md)

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

**Affected documents** — [cashflow.md](cashflow.md), [gaps.md](gaps.md)

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

**Affected documents** — [containers.md](containers.md), [receiving.md](receiving.md)

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

**Affected documents** — [containers.md](containers.md), [receiving.md](receiving.md)

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

**Affected documents** — [allocation-workbench.md](allocation-workbench.md)

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

**Affected documents** — [suppliers.md](suppliers.md), [allocation-workbench.md](allocation-workbench.md)

---

## `INV-D-012` · Amazon's case pack wins over ours

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — AWD reports receipts in cases and states the eaches-per-case in
the same payload. That figure sometimes disagrees with the pack we shipped, and
one of the two has to be used to convert Amazon's count into units.

**Decision** — Amazon's case pack is used. The disagreement lands inside the
variance rather than being corrected out of it.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Convert with our own pack size | Produces a unit count Amazon will not recognise, so the number cannot be used in a claim |
| Refuse to convert where the packs disagree | Loses the receipt entirely, which is worse than a receipt that needs explaining |

**Reason** — Business call: Amazon's count is what can actually be sold, so it
is the figure the business plans against.

**Consequences** — A pack-size disagreement shows up as a shortfall or an
over-receipt. The syncs report those two separately, because the remedy differs
— a claim versus a setup fix. Where Amazon states EACHES, nothing is multiplied.

**Affected documents** — [receiving.md](receiving.md)

---

## `INV-D-013` · One receipt sync per Amazon API, never merged

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted |

**Context** — AWD containers and containers consigned straight to a fulfilment
centre are reconciled through different Amazon APIs. The two look like one job
doing the same thing twice.

**Decision** — Two separate commands, routed by the shape of the shipment ID.
`STAR-…` goes to AWD; anything else goes to FBA Inbound, which explicitly skips
`STAR-`.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| One command that tries both APIs per container | The ids do not resolve across APIs, so every container costs a failed call, and the case-pack conversion would have to be conditional inside shared code — exactly where it would eventually be applied to the wrong payload |
| Route on the destination warehouse instead of the ID | A container whose destination was never set would be skipped entirely |

**Reason** — The unit difference is the failure mode worth designing against:
AWD reports cases, FBA Inbound reports eaches, and applying the case conversion
to an FBA payload multiplies every figure by the pack size. Keeping the
conversion in a file that only ever sees cases makes that mistake impossible
rather than merely unlikely.

**Consequences** — Two commands, two cron entries, and a shared reporting format
that must be kept in step by hand. Routing on the ID prefix means an AWD id
typed without its prefix is silently sent to the wrong API.

**Affected documents** — [receiving.md](receiving.md), [deployment.md](../deployment.md)

---

## `INV-D-014` · Units draw FIFO — oldest purchase order first

| | |
|---|---|
| **Date** | 2026-08-04 · **Status** accepted |

**Context** — A packing-list row names a SKU and a quantity. That supplier may
have the same SKU open on several purchase orders, and a row rarely carries a PO
number. Something has to decide which commitment the units come off.

**Decision** — Units draw from that supplier's **oldest open purchase order
first**, by order date, splitting across POs where one cannot cover the row. A
PO number on the row overrides it with an exact match. A split is reported as a
warning, never silently.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Require a PO number on every row | The factory's packing list does not carry one, so it would be typed from memory — a guess with the authority of an entry |
| Newest PO first | Leaves the oldest commitment open indefinitely, so a balance never closes and the supplier's ageing is meaningless |
| Refuse to guess; make the operator allocate each row | Hundreds of rows a container. The override exists for the cases that need it |

**Reason** — Business call: it matches how the factory settles, oldest
commitment first, and it is the only rule under which a purchase order reliably
reaches zero.

**Consequences** — A row can span several purchase orders, so one packing-list
line becomes several container lines, each keeping its own attribution. Where
the same SKU is open on two suppliers, the FIFO pool is filtered to the row's
own supplier — an unfiltered list would let a line be drawn against the wrong
factory's balance. Once `INV-D-011` is built, opening balance is consumed ahead
of any PO, and FIFO applies within each tier.

**Affected documents** — [allocation-workbench.md](allocation-workbench.md), [purchase-orders.md](purchase-orders.md)

---

## `INV-D-015` · Suppliers are never created implicitly

| | |
|---|---|
| **Date** | 2026-08-05 · **Status** accepted — PO upload not yet compliant (`INV-SUP-004`) |

**Context** — Suppliers used to come into existence as a side effect of
importing a PO workbook, keyed on a code derived from the typed name. A typo
minted a second factory, and there was no way to add one deliberately.

**Decision** — A supplier is created only **explicitly**, through Add Supplier.
Everywhere else an unknown name is **refused by name**, with near-matches
suggested. All creation paths derive the code from the name identically, so an
explicit add and any legacy implicit path land on the same record.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Keep implicit creation everywhere | A typo silently strands a PO or packing list against a phantom factory, and the error surfaces weeks later as a balance nobody recognises |
| Fuzzy-match instead of refusing | Guessing between "Roomi" and "Rustam" wrongly is worse than asking; the near-match suggestion gives the human the same speed without the risk |

**Reason** — A supplier is an attribution anchor: balances, allocations and
cash all hang off it. Minting one from a typo corrupts attribution silently,
and the cost of refusal is one trip to Add Supplier.

**Consequences** — A genuinely new factory must be added before its first
document imports. The PO upload still violates the rule (`INV-SUP-004`) and is
brought in line rather than the rule being weakened.

**Affected documents** — [suppliers.md](suppliers.md), [allocation-workbench.md](allocation-workbench.md)

---

## `INV-D-016` · Wastage closes balance permanently

| | |
|---|---|
| **Date** | 2026-07-27 · **Status** accepted |

**Context** — Factories report fault units against an order. Something has to
happen to the balance those units represented: it is either still owed, or it
is not.

**Decision** — Wastage **permanently reduces the outstanding balance**:
remaining = ordered − wastage − allocated. We do not pay for the units and the
factory does not remake them under the same order. Wastage lands FIFO across
the scoped open lines, oldest PO first — the same draw order as allocation.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Keep balance open until remade | The factory does not remake under the same PO; the balance would sit open forever and overstate what is owed |
| Track wastage but exclude it from balance | Reported and ignored is worse than absent — every consumer of "remaining" would need to know to subtract it |

**Reason** — Business call: it matches the commercial arrangement. A remake, if
negotiated, arrives as new units on a new or existing order, not as the old
balance reopening.

**Consequences** — Outstanding-to-supplier falls the moment a wastage file is
uploaded, with no money movement — the value column falls with it. Short-close
then distinguishes what remains: a closed line's unallocated remainder is
production shortage, never wastage.

**Affected documents** — [purchase-orders.md](purchase-orders.md), [suppliers.md](suppliers.md)

---

## `INV-D-017` · Demand is PDS where set, otherwise the 7-day average

| | |
|---|---|
| **Date** | 2026-07-21 · **Status** accepted |

**Context** — Every cover day, stockout date and reorder quantity divides by a
demand figure. Two candidates exist: PDS, the potential daily sale the sales
team sets per SKU, and the live selling average from Amazon.

**Decision** — **PDS wins wherever it is set**, on a dated basis. Where no PDS
exists, the live **7-day** average is used and the SKU is flagged *no PDS*. A
SKU with neither is not planned at all.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Always use the selling average | A SKU that stocked out sells nothing, so its average collapses and the planner stops reordering it — the failure feeds itself |
| Blend PDS and the average | Nobody could explain the resulting number, and neither team would own it |
| 30- or 90-day average as the fallback | Too slow to react to a launch or a step change; the 7-day is the responsive one, and 30 and 90 are shown alongside for context |

**Reason** — Business call: PDS is the sales team's intent, and the planner
should buy for the plan, not for the past. It is also the only figure that works
for a SKU with no history.

**Consequences** — A wrong or stale PDS is believed absolutely, which is why the
*no PDS* count is a headline figure and why 7-, 30- and 90-day averages are
shown next to it for comparison. PDS is dated, so a seasonal plan projects
correctly rather than flattening to one number.

**Affected documents** — [planner.md](planner.md), [loading-plan.md](loading-plan.md), [reorder.md](reorder.md)

---

## `INV-D-018` · Amazon stock is never written by hand

| | |
|---|---|
| **Date** | 2026-07-24 · **Status** accepted |

**Context** — Shipping an FBA transfer, or receiving a container at an Amazon
warehouse, is the moment units become Amazon's. The obvious move is to add them
to the Amazon stock figure there and then.

**Decision** — **Nothing writes Amazon stock except Amazon's own sync.** A
transfer draws down the source warehouse and stops there. The units are
invisible until Amazon reports them as inbound, and then as fulfillable.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Add to FBA stock on ship | The sync replaces the figure wholesale on its next run, so the write survives hours at most — and during those hours the units are counted in both places |
| Add to FBA, and teach the sync to merge | Requires reconciling our guess against Amazon's count on every run, to produce a number Amazon already tells us |

**Reason** — Amazon's warehouse is Amazon's record. A second writer to a figure
one system owns produces a disagreement with no tiebreak.

**Consequences** — Units are briefly in neither column: drawn from the source,
not yet reported by Amazon. That gap is real and is shown as in-transit units
on the transfers page rather than hidden by an optimistic write. The container
goods-receipt path still violates this rule — `INV-CONT-004`.

**Affected documents** — [transfers.md](transfers.md), [planner.md](planner.md)

---

## `INV-D-019` · Amazon inflows are projected from settlements, not sales

| | |
|---|---|
| **Date** | 2026-07-24 · **Status** accepted |

**Context** — The forecast needs to know what Amazon will pay us and when. Sales
data is richer and more current than payout history.

**Decision** — Inflows are projected from **actual settlement events**: the
average of recent real disbursements, at the cadence those disbursements
actually arrive. Sales are not used.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Project from sales less estimated fees | Requires modelling fees, refunds, reserves and Amazon's holdback — every one an assumption, and the answer is a number Amazon will contradict |
| Use a run rate over all payouts | Reserve releases and partial disbursements drag the average below a typical settlement, making the forecast pessimistic and the low point unreliable |

**Reason** — Business call: the forecast is a bank-balance question, and the
only figures that answer it are the ones that hit the bank.

**Consequences** — A region with no payout history projects no inflows at all,
and its ledger is worst-case by construction. Same-cycle top-ups are collapsed
into one event before averaging, and tiny off-cycle disbursements are excluded,
or the cadence and the amount would both be wrong.

**Affected documents** — [cashflow.md](cashflow.md)

---

## `INV-D-020` · A human edit locks a generated cash-flow row

| | |
|---|---|
| **Date** | 2026-07-24 · **Status** accepted |

**Context** — Container payments and inflow estimates are generated and
refreshed. Finance also knows things the generator does not — a renegotiated
payment date, a part-payment, a settlement already agreed.

**Decision** — Editing a generated row **locks** it. Refresh never touches a
locked row again; everything unlocked is regenerated freely.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Regenerate everything, always | Destroys the correction the moment anyone presses refresh, and the loss is silent |
| Never regenerate once a ledger exists | The estimates are the point of the ledger; a stale forecast is not a forecast |
| Keep both and show the difference | A two-value ledger nobody can total |

**Reason** — A person editing a forecast row knows something the generator does
not. That knowledge is the more valuable of the two.

**Consequences** — A locked row can go stale: later changes to the container's
FOB or freight no longer reach it. The lock is visible in the ledger so it can
be unlocked deliberately, and the count of skipped rows is reported on refresh.

**Affected documents** — [cashflow.md](cashflow.md)
