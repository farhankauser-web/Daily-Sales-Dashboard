# Suppliers

files: `apps/inventory_planning/{views,procurement,models,reorder}.py`
       `templates/inventory_planning/suppliers.html`
verified against: `873b658` · 2026-08-06

A supplier is a factory we buy from. This page is the registry — who they are,
on what terms, how long they take — and the ledger: what each factory still
owes us, in units and in money.

## Purpose

Two questions, asked at different moments. *Who can make this, and how fast?* —
the registry: lead times and monthly capacity, which the planner and reorder
engine plan against. *What are we owed?* — the ledger: every unit ordered and
not yet shipped, valued at the agreed FOB, plus any backlog carried in from
before Pulse existed.

The supplier is also the **attribution anchor**. A packing-list row resolves to
a supplier before it resolves to anything else, and a name not in this registry
is refused everywhere it appears. Whatever this page holds is the universe of
factories the rest of the system will recognise.

## Scope

Covers the registry, the per-supplier ledger and drill-down, and opening
balance.

**Not covered:**
- the purchase orders behind the ledger figures — [purchase-orders.md](purchase-orders.md)
- wastage — a PO-line event; its upload lives on this page for convenience but
  belongs to [purchase-orders.md](purchase-orders.md)
- how allocation consumes a supplier's balance — [allocation-workbench.md](allocation-workbench.md)
- what the lead times drive — planner.md *(pending)*

## Business workflow

```
Add supplier → Import POs against them → Balance builds → Packing lists draw it down
     ↑                                        ↓
Opening balance upload            Drill: supplier → category → PO → production plan
```

## Actors

| Actor | Does |
|---|---|
| Ops | maintains the registry, uploads opening balance, reads the ledger |
| The system | aggregates the ledger from PO lines; refuses unknown suppliers everywhere |
| Planner / reorder | read lead times and capacity to time and size purchase proposals |

## Business rules

1. **A supplier exists before anything references it.** An unknown name is
   refused rather than minted, so a typo cannot create a second factory. See
   `INV-D-015`. The PO upload does not yet honour this rule — `INV-SUP-004`.
2. **The code is the identity.** Every path that can create a supplier derives
   the same code from the name, so a factory added by hand and one first seen on
   a PO import land on the same record.
3. **A supplier's balance is the sum of its open PO lines** — ordered minus
   wastage minus allocated — **plus opening balance**. Nothing else feeds it.
4. **Opening balance is backlog from before the system went live.** It is owed
   in units, dated, and replaces cleanly on re-upload per supplier and date.
   It is consumed before PO balance once `INV-D-011` is built (`INV-CONT-002`).
5. **Outstanding FOB is the money value of the unit balance**, priced at each PO
   group's agreed rate, **in the supplier's currency**. It is a different figure
   in a different currency from a container's FOB (`INV-D-004`) and the two are
   never mixed.
6. **Wastage closes balance permanently.** We do not pay for factory-fault
   units and the factory does not remake them. See `INV-D-016`.
7. **Lead time is production + sea + port-to-warehouse**, per supplier. The
   reorder engine dates its proposals from these.
8. **A retired supplier keeps its history and leaves the totals.** Deactivation
   hides it from the default view and its balances from the KPIs; nothing is
   deleted, and the record can be reactivated.

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Add / edit a supplier | ops | a name; code derived if not given | registry row; duplicate code refused by name |
| Retire / reactivate | ops | supplier exists | hidden from default view and totals; history intact |
| Upload opening balance | ops | supplier chosen, as-of date | that supplier/date replaced; unknown SKUs reported back |
| Upload a PO workbook | ops | — | see [purchase-orders.md](purchase-orders.md) |
| Drill into a supplier | anyone | — | ledger by category, then by PO, then production plan |

## System behaviour

- The ledger is **derived, never stored**: aggregated from PO lines on every
  read, so it cannot drift from the documents beneath it.
- Opening balance **categorises from the catalogue** by SKU; a typed category in
  an older file still wins (`INV-D-003`). SKUs the catalogue does not know are
  reported back by name, not silently left blank.
- The reorder engine picks a SKU's supplier as: the one holding open PO balance,
  else the most recent PO's, else the cheapest historical — and dates the
  proposal by that supplier's production lead.

## Data model

- **Supplier** — identity (code, name, country, contact), commercial terms
  (currency, payment terms), planning inputs (three lead-time legs, monthly
  capacity), active flag.
- **Opening balance** — per supplier, SKU and as-of date: units owed from before
  the system. Carries no rate today (`INV-SUP-001`).
- The ledger itself is not an entity — it is the live aggregate of PO lines.

## Validation rules

| Input | Rule | On failure |
|---|---|---|
| Supplier name | required | refused |
| Supplier code | unique | refused, naming the supplier that owns it |
| Lead times, capacity | numeric | refused, naming the field |
| Opening balance file | a SKU column and a quantity column | refused, naming what is missing |
| Opening balance SKU | known to the catalogue | row imported; SKU reported back as uncategorised |

## Edge cases

- **Same supplier, two spellings.** Prevented by the shared code derivation —
  "J.Sons" typed anywhere always resolves to `JSONS`. The near-match suggestion
  on the packing list is the second line of defence.
- **Opening balance for a category with no PO.** Shown in the drill-down as its
  own row — the backlog is real even though no purchase order references it.
- **Re-uploading opening balance for the same date** replaces that upload
  cleanly. Once `INV-D-011` makes the balance consumable, a drawn-against upload
  must instead be refused (`INV-CONT-002`).
- **All lead times at their defaults.** On the development snapshot all 13
  suppliers carry 90+45+10 and zero capacity — *provisional, dev snapshot* —
  which would make every reorder proposal date identical per region. The values
  are ops inputs, not derived; if production matches, that is a data-entry task,
  not a gap.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INV-SUP-001` | opening balance has no rate, so Outstanding FOB understates | missing implementation |
| `INV-SUP-004` | the PO upload takes free text for the supplier and mints one on a typo | bug |
| `INV-CONT-002` | opening balance is not consumable | missing implementation |
| `INV-CASH-001` | opening-balance backlog never reaches cash flow | blocked on a business decision |

## Related decisions

`INV-D-003` `INV-D-011` `INV-D-015` `INV-D-016`

## Related documents

- [purchase-orders.md](purchase-orders.md) — the documents this ledger is built from
- [allocation-workbench.md](allocation-workbench.md) — how balance is drawn down
- planner.md *(pending)* — what lead times and capacity feed
