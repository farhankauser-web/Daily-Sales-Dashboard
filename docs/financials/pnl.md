# Management P&L

files: `apps/dashboard/{pnl_engine,pnl_lines,pnl_importer,unified_txn_importer,finances_importer}.py`
       `apps/dashboard/views.py` — `pnl_statement`, `api_pnl_statement`
       `templates/dashboard/pnl_statement.html`
verified against: `eb5740a` · 2026-08-06

The monthly statement of operations, per region and consolidated. What Amazon
recognised, what it cost, and what was left.

## Purpose

The P&L is the statement the business is run on and the one an accountant reads.
It has to reconcile: every line traceable to something Amazon posted or a person
entered, in the region's own currency, for a closed month.

It exists because Amazon does not produce one. Amazon produces settlements,
transaction reports and fee breakdowns; turning those into a statement of
operations — with cost of goods, advertising, overhead and staff against them —
is this machine's job.

## Data source

Three pipelines write the same settled-actuals table, and a fourth path fills
the gap while a month is still settling.

| Source | Grain | Authoritative for |
|---|---|---|
| Unified transaction report | posted date, per transaction | the whole statement — it is the report that ties to the books |
| Finances API | posted date, per financial event | lines the unified report has not supplied |
| Settlement report | settlement period | fee-level detail, and reconciliation to a disbursement |
| Operational fallback | order date, from Reporting's daily figures | **only** lines no settlement has yet provided |

**Precedence: settled actuals first, operational fallback second, and every line
records which it used.** Where a settled figure exists it is used; where none
does, the operational figure fills the line and is labelled `operational` rather
than presented as settled. See `FIN-D-001`.

The operational fallback is the **only** point where Reporting's numbers enter
this section, and it is a deliberate bridge across the settlement lag — not a
merging of the two domains. A line marked `operational` is a placeholder for a
figure that has not settled, and it will change when it does.

## Business rules

1. **The statement structure is defined once.** One canonical line list — key,
   label, section, source type, sign, indent — is read by the engine, the manual
   entry form, the importer and the renderer, so they cannot drift apart. See
   `FIN-D-002`.
2. **Every line is one of four kinds**: automatic (from settled or operational
   data), manual (typed or imported), computed (derived from other lines), or a
   section header. A line's kind determines who may change it.
3. **A statement is produced in the region's own currency.** Nothing is
   converted per-region.
4. **Consolidation converts to USD at a monthly FX rate**, and **only additive
   lines are summed.** Percentages and per-unit figures are **recomputed** on the
   consolidated totals, never averaged. See `FIN-D-003`.
5. **A region with no FX rate for the month is reported, not silently dropped**
   from the consolidation.
6. **Tax is excluded entirely.** Collected equals remitted; it is pass-through
   and belongs in neither revenue nor cost.
7. **The bank transfer is not a P&L item.** A disbursement moves money that has
   already been recognised. See [payouts.md](payouts.md).
8. **Cost of goods is computed as (order units − refund units) × uploaded cost**,
   per SKU, matching how the business itself calculates it. See
   [cogs.md](cogs.md).
9. **Advertising prefers the allocator's total** for the month, falling back to
   the operational figure — the same preference the SKU table applies. See
   `REP-D-002`.

## States

A month moves through three states, and the statement says which:

| State | Meaning | What the lines read |
|---|---|---|
| Open | the month is still running | operational throughout |
| Settling | ended, settlement not yet landed | mixed — settled where available, operational elsewhere |
| Settled | settlement landed | settled actuals, with manual and computed lines on top |

The mix is visible per line rather than summarised, so a reader can see exactly
which figures are provisional.

## User actions

| Action | Who | Result |
|---|---|---|
| Read a month, per region or consolidated | finance | the statement with each line's source |
| Enter a manual line | finance | overhead, staff and other costs in regional currency |
| Set a monthly FX rate | finance | that currency can be consolidated |
| Import a P&L workbook | finance | manual lines filled in bulk |
| Import a unified transaction report | finance | settled actuals for the month |
| Sync a month from Amazon | finance | pulls what the API will give for that month |
| Recalculate cost of goods | finance | re-derives COGS from the current cost base |

## System behaviour

- **Auto lines are re-derived on every read**, so a month improves as settlement
  data lands without anyone re-running anything.
- **Manual lines persist across re-derivation** — they are the figures Amazon
  never supplies, and nothing automatic may overwrite them.
- **Computed lines are formulas, not stored values**, so a correction to any
  input propagates.
- Where no line exists at all, it reads zero with its source recorded as absent
  — distinguishing "nothing happened" from "we have no data".

## Data model

- **P&L line definition** — the statement's structure: what lines exist, in what
  order, of what kind. Not data; the shape data is poured into.
- **Settled line actual** — one per marketplace, month and line: the amount, the
  units behind it, and which pipeline supplied it.
- **Manual entry** — one per marketplace, month and line, in regional currency.
- **Monthly FX rate** — per currency per month, for consolidation only.

## Edge cases

- **A recent month with no settlement yet.** Renders from operational data
  throughout rather than blank, every line labelled. This is the settlement lag
  working as intended, not missing data.
- **A month where settlement covers some lines and not others.** Normal during
  settling; the sources are mixed within one statement and shown per line.
- **A region whose currency has no rate for the month.** Excluded from the
  consolidated figure and reported, so the total is never quietly short a region.
- **A refund posted in a later month than its sale.** Lands in the month Amazon
  posted it, which is what makes the statement reconcile and what makes it
  differ from Reporting's order-date view.

## Observations — not gaps

*Source: local development data; provisional.*

- **Three pipelines have written the settled-actuals table** — 136 rows from the
  unified report, 39 from the Finances API, 11 from settlement reports. That is
  the precedence design working, not duplication: each fills what the others do
  not.
- **No manual entries and no FX rates exist locally.** Both are entered by
  finance in production; their absence here says nothing about production.

## Known gaps

*None filed. The three-way source mix was examined and is by design.*

## Related decisions

`FIN-D-001` `FIN-D-002` `FIN-D-003` · and from Reporting: `REP-D-002`

## Related documents

- [cogs.md](cogs.md) — the cost base beneath the statement
- [fee-drift.md](fee-drift.md) — whether Amazon's fees match what was modelled
- [payouts.md](payouts.md) — money that actually arrived, and why it is not a P&L line
- [reporting/daily.md](../reporting/daily.md) — the operational view, and why it differs
