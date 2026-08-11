# Gap index

Every open gap across all sections. **This file is an index.** The detail lives
in each section's `gaps.md`, which is the source of truth — evidence, impact and
recommendation are there, not here.

Updated in the same commit as the section file. If it starts drifting, generate
it rather than maintain it by hand.

Last reconciled: 2026-08-06. **All nine sections are complete and frozen**; each
section's register is the backlog for that domain.

Gaps carry a **Classification** — missing implementation, bug, configuration,
missing operational process or legacy data — a root cause, and whether a code
change alone would close them. See [methodology.md](methodology.md).

Four ids use the older two-part scheme and are **carried, not registered**:
`FIN-001`, `WM-001`, `BA-001` and `SET-001`. Each is described in prose in its
section's register and gets a full entry when the evidence to classify it
exists — for all four that means a production check.

The five `INFRA-*` gaps are classified in
[deployment.md](deployment.md); `INFRA-001` is the highest-priority gap in the
project.

## Open

| ID | Title | Priority | Section |
|---|---|---|---|
| `INV-CONT-001` | In-transit lines carry no FOB rate | P1 | [inventory](inventory/gaps.md) |
| `INV-CONT-002` | Opening balance is not consumable | P1 | [inventory](inventory/gaps.md) |
| `INV-RECV-001` | No active container carries an Amazon shipment ID | P1 | [inventory](inventory/gaps.md) |
| `INV-RECV-002` | Archived containers with no count report as a total loss | P1 | [inventory](inventory/gaps.md) |
| `INFRA-001` | `deploy/crontab.txt` cannot be installed on EC2; scheduled jobs never run | P1 | [deployment](deployment.md) |
| `INV-CONT-003` | No stall alert for a container stuck in Receiving | P2 | [inventory](inventory/gaps.md) |
| `INV-CONT-011` | The status-workbook import deletes every container in the region | P2 | [inventory](inventory/gaps.md) |
| `INV-RECV-003` | Per-SKU variance views ignore Amazon's count | P2 | [inventory](inventory/gaps.md) |
| `INV-RECV-004` | A SKU with nothing received reports no shortfall | P2 | [inventory](inventory/gaps.md) |
| `INV-ALLOC-003` | The container-manifest import strips FOB and PO attribution | P2 | [inventory](inventory/gaps.md) |
| `INV-PLAN-001` | Lead times exist twice, and the two disagree | P2 | [inventory](inventory/gaps.md) |
| `MKT-ALLOC-002` | The allocator reads a superseded, campaign-blind copy of the advertised-product data | P2 | [marketing](marketing/gaps.md) |
| `MKT-ALLOC-001` | The campaign → product-group map is hardcoded in a view module | P2 | [marketing](marketing/gaps.md) |
| `MKT-AMS-001` | A dataset that stops delivering is silent | P2 | [marketing](marketing/gaps.md) |
| `MKT-ADS-001` | A report day that never resolves is invisible | P2 | [marketing](marketing/gaps.md) |
| `MKT-CAMP-001` | Nothing flags a campaign whose profit rests mostly on fallback margins | P2 | [marketing](marketing/gaps.md) |
| `MKT-STI-004` | Brand Analytics ingestion may not be scheduled in production | P2 | [marketing](marketing/gaps.md) |
| `MKT-STI-005` | Campaign names are unavailable for UAE and KSA | P3 | [marketing](marketing/gaps.md) |
| `REP-PROD-001` | The live fallback builds its own SKU table without the allocator | P2 | [reporting](reporting/gaps.md) |
| `WM-ERR-001` | The error log has no lifecycle, so a repeating failure buries every real one | P2 | [walmart](walmart/gaps.md) |
| `INV-SUP-001` | Opening balance has no rate, so Outstanding FOB understates | P2 | [inventory](inventory/gaps.md) |
| `INV-SUP-004` | The PO upload takes free text for the supplier and mints one on a typo | P2 | [inventory](inventory/gaps.md) |
| `INV-CASH-001` | Opening-balance backlog never reaches cash flow | P2 | [inventory](inventory/gaps.md) |
| `FIN-001` | Referral fee computed on gross revenue, never checked against a settlement | P2 | [financials](financials/gaps.md) |
| `BA-001` | Three Brand Analytics reports submitted, never collected | P2 | [brand-analytics](brand-analytics/gaps.md) |
| `WM-001` | Every non-JSON response reported as "session expired" | P2 | [walmart](walmart/gaps.md) |
| `INFRA-004` | VAPT: dependency bumps and `json_script` for four templates | P2 | [deployment](deployment.md) |
| `INFRA-005` | No SMTP, so password reset does not work | P2 | [deployment](deployment.md) |
| `SET-001` | AE/SA marketplace ids missing, blocking the UAE P&L | P2 | [settings](settings/gaps.md) |
| `INV-CONT-004` | Goods receipt writes AWD stock the sync overwrites | P3 | [inventory](inventory/gaps.md) |
| `INV-RECV-005` | Receipt syncs are neither region-filtered nor scheduled outside the USA | P3 | [inventory](inventory/gaps.md) |
| `INV-ALLOC-004` | Append mode is unreachable and its docstring misleads | P3 | [inventory](inventory/gaps.md) |
| `INV-PLAN-002` | The supplier-choice docstring describes a rule the code does not follow | P3 | [inventory](inventory/gaps.md) |
| `INV-SUP-002` | `POLineGroup.pcs` is written and never read | P3 | [inventory](inventory/gaps.md) |
| `BA-002` | `BABrandShareWeekly` has no writer and no reader | P3 | [brand-analytics](brand-analytics/gaps.md) |
| `MKT-ALLOC-003` | Amazon's own SKU attribution is discarded and re-derived | P3 | [marketing](marketing/gaps.md) |
| `MKT-ALLOC-004` | The smoothing docstring describes a blend the code does not perform | P3 | [marketing](marketing/gaps.md) |
| `MKT-AMS-002` | The legacy dataset map covers SP only outside North America | P3 | [marketing](marketing/gaps.md) |
| `MKT-UPL-001` | An unmatched campaign name on upload is logged, never reported | P3 | [marketing](marketing/gaps.md) |
| `MKT-TERM-001` | Signal thresholds are fixed in code and identical across marketplaces | P3 | [marketing](marketing/gaps.md) |
| `INFRA-002` | Kernel upgrade pending, needs a reboot | P3 | [deployment](deployment.md) |
| `INFRA-003` | HSTS still 86400 | P3 | [deployment](deployment.md) |
| `INT-001` | Command Center phase 3 — per-widget config, resize | P3 | [intelligence](intelligence/gaps.md) |

Every gap now has a home. The `INFRA-*` set is classified in
[deployment.md](deployment.md) rather than in a section register, because
deployment is a project concern rather than a business domain.

## Architecture mismatches

Structural disagreements between the code and the business are **not** gaps.
They live in [architecture-mismatches.md](architecture-mismatches.md) —
`ARCH-001` to `ARCH-009`.

## Recently closed

Closed gaps keep their rows in the section file, with the commit that closed
them. Nine inventory gaps were closed in the week to 2026-08-05; see
[inventory/gaps.md](inventory/gaps.md).

| ID | Title | Closed by |
|---|---|---|
| `FIN-003` | Margins divided by gross revenue while the numerator was ex-VAT | `b6a8603` |
| `FIN-004` | Cash-flow page hardcoded `$` for every region | `fd6af91` |
| `WM-002` | Reprocess crashed on `request.user.username` | `bf26090` |
