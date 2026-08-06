# Open gaps

Every known difference between how Pulse works and how it should. One row per
gap, each with an ID, the evidence that proves it, and a status.

**The ID is the unit of work.** Say "do `INV-CONT-02`" and a session reads that
one doc rather than the codebase. Commits close a gap by ID.

**Evidence is not optional.** A gap without a query or a `file:line` cannot be
re-checked later, so nobody trusts it enough to act. State what proves it.

When a gap is closed, keep the row and record the commit — the table becomes
the history as well as the backlog.

Seeded 2026-08-05 from the session ending `fd6af91`. Rows marked *carried over*
were reported earlier and have **not** been re-verified against current code.

---

## Inventory — containers, allocation, receiving

`docs/inventory/containers.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `INV-CONT-01` | 188 in-transit container lines carry no FOB rate, so cash flow prices those containers at zero | `InTransitLine.objects.filter(shipment__in=active_usa, po_line__isnull=True).count()` → 188 of 188 | high | **open** — forward-only by decision; clears only when their packing lists are re-uploaded through the workbench with FOB |
| `INV-CONT-02` | Opening balance is not consumable. A packing list never draws it down, so units are deducted from PO balances even when backlog exists | `_open_lines_for()` queries `POLine` only — `procurement.py` | high | **open** — agreed design ("deduct opening first, then PO"), not built |
| `INV-CONT-03` | A container that starts receiving and never CLOSES sits in Receiving indefinitely with no alert | no stall check exists; `sync_awd_receipts` advances only on receipts or CLOSED | medium | **open** — threshold undecided, 14 days suggested |
| `INV-CONT-04` | Manual Goods Receipt writes AWD stock that `sync_planning_inventory` then overwrites wholesale — the write is pointless for AWD and only meaningful for 3PL | `views.py:337` writes for `kind in ('awd','3pl')`; the AWD sync replaces all rows for that warehouse | low | **open** |

## Inventory — suppliers, purchase orders

`docs/inventory/suppliers-pos.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `INV-SUP-01` | Opening-balance units count toward a supplier's Balance but contribute nothing to Outstanding FOB, so the money figure understates the units figure | `api_suppliers`: `'remaining': op + a['remaining']` but `a['value']` only sums PO lines | medium | **open** — needs a rate on `SupplierOpeningBalance` |
| `INV-SUP-02` | `POLineGroup.pcs` is written on import and read nowhere | `grep -rn "\.pcs\b" apps templates` → no read sites | low | **open** — dead column, drop with a migration |

## Financials

`docs/financials.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `FIN-01` | Amazon referral fee is computed on **gross** revenue (`rev * 0.15`) while every margin is measured ex-VAT. Never checked against a real UK settlement | `sync.py` and the SKU builders in `amazon_api/views.py` | medium | **open** — needs one UK settlement report to settle it |
| `FIN-02` | Opening-balance backlog never appears in cash flow: it has no PO and no container, so no payment is ever scheduled for it | `refresh_region()` builds outflows from containers only | medium | **open** — separate from `INV-SUP-01` |

## Marketing / Brand Analytics

`docs/brand-analytics.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `BA-01` | Three Brand Analytics reports were submitted and never collected; there is no weekly submit + resume cron | reported during the BA build; no BA entries in `deploy/crontab.txt` | medium | **open** — *carried over* |

## Walmart

`docs/walmart.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `WMT-01` | The orders page reports **every** non-JSON response as "session expired", hiding 403s and server errors. It masked a crash in Reprocess for the button's whole life | `orders.html:287` and the same check in `runJob()` | medium | **open** — fix offered and declined; reopen when wanted |

## Infrastructure / deployment

`docs/deployment.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `INFRA-01` | `deploy/crontab.txt` is a **macOS** template (`/Users/...`, `/opt/anaconda3/...`) and cannot be installed on EC2. The live crontab is hand-maintained and has drifted: `sync_fba_receipts`, the 07:10 stock sync and `--advance-status` have never run on the server | paths in `deploy/crontab.txt`; `ONEU1765583` sat at `at_port` with Amazon reporting `CLOSED` | **high** | **open** — needs a Linux crontab that is actually installed |
| `INFRA-02` | Kernel upgrade pending (7.0.0-1008 → -1009), needs a reboot | *carried over* | low | **open** |
| `INFRA-03` | HSTS still 86400; raise to 31536000 once stable | *carried over* | low | **open** |
| `INFRA-04` | VAPT: dependency bumps (M1) and `json_script` for the four templates using `\|safe` (M4) | `|safe` in `dashboard/{index,ppc,historical}.html`, `command_center/command_center.html` | medium | **open** |
| `INFRA-05` | No SMTP configured, so password reset does not work | *carried over* | medium | **open** |

## Reporting

`docs/reporting.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `REP-01` | `apps/dashboard/views.py` is 7,590 lines and serves five sidebar sections, so it cannot be read and the doc boundary does not match the code boundary | `wc -l apps/dashboard/views.py` | medium | **open** — split into a `views/` package, one commit per slice |

## Settings

`docs/settings.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `SET-01` | AE/SA marketplace IDs missing, so the UAE P&L cannot be produced | *carried over* | medium | **open** |

## Intelligence

`docs/intelligence.md`

| ID | Gap | Evidence | Severity | Status |
|---|---|---|---|---|
| `INT-01` | Command Center Phase 3 — per-widget config, resize, polish | task #158 | low | **open** |

---

## Recently closed

| ID | Gap | Closed by |
|---|---|---|
| `INV-CONT-05` | Cash flow priced every container at zero because `_container_fob` only read `po_line.group.fob_rate` | `fd6af91` — FOB on the packing list, snapshotted to `InTransitLine.fob_rate` |
| `INV-CONT-06` | Container History reported every auto-closed container as a total loss, reading the manual count only | `90f011b` — `counted_units` prefers the human figure, falls back to Amazon's |
| `INV-CONT-07` | In Transit and Receiving both listed the same container | `6d587f4` — the two lists are now a partition keyed off receipts |
| `INV-CONT-08` | FC containers never produced receipts; only the AWD endpoint existed | `4febb33` — `sync_fba_receipts` |
| `FIN-03` | CM%, GM% and TACoS divided by gross revenue while the numerator was ex-VAT | `b6a8603` |
| `FIN-04` | Cash-flow page hardcoded `$` for every region | `fd6af91` |
| `WMT-02` | Reprocess crashed on `request.user.username`; the model is email-based | `bf26090` |
| `INV-SUP-03` | No way to add a supplier — they only appeared as a side effect of a PO import | `d885737` |
| `INV-CONT-09` | No way to add a 3PL warehouse from the shipment form | `d1b7e56` |
