# Gap index

Every open gap across all sections. **This file is an index.** The detail lives
in each section's `gaps.md`, which is the source of truth — evidence, impact and
recommendation are there, not here.

Updated in the same commit as the section file. If it starts drifting, generate
it rather than maintain it by hand.

Last reconciled: 2026-08-06.

Inventory gaps carry a **Classification** — missing implementation, bug,
configuration, missing operational process or legacy data — and say whether a
code change alone would close them. See [inventory/gaps.md](inventory/gaps.md).

## Open

| ID | Title | Priority | Section |
|---|---|---|---|
| `INV-CONT-001` | In-transit lines carry no FOB rate | P1 | [inventory](inventory/gaps.md) |
| `INV-CONT-002` | Opening balance is not consumable | P1 | [inventory](inventory/gaps.md) |
| `INV-RECV-001` | No active container carries an Amazon shipment ID | P1 | [inventory](inventory/gaps.md) |
| `INV-RECV-002` | Archived containers with no count report as a total loss | P1 | [inventory](inventory/gaps.md) |
| `INFRA-001` | `deploy/crontab.txt` cannot be installed on EC2; scheduled jobs never run | P1 | *(deployment.md pending)* |
| `INV-CONT-003` | No stall alert for a container stuck in Receiving | P2 | [inventory](inventory/gaps.md) |
| `INV-CONT-011` | The status-workbook import deletes every container in the region | P2 | [inventory](inventory/gaps.md) |
| `INV-RECV-003` | Per-SKU variance views ignore Amazon's count | P2 | [inventory](inventory/gaps.md) |
| `INV-RECV-004` | A SKU with nothing received reports no shortfall | P2 | [inventory](inventory/gaps.md) |
| `INV-SUP-001` | Opening balance has no rate, so Outstanding FOB understates | P2 | [inventory](inventory/gaps.md) |
| `INV-CASH-001` | Opening-balance backlog never reaches cash flow | P2 | [inventory](inventory/gaps.md) |
| `FIN-001` | Referral fee computed on gross revenue, never checked against a settlement | P2 | *(financials pending)* |
| `BA-001` | Three Brand Analytics reports submitted, never collected | P2 | *(brand-analytics pending)* |
| `WM-001` | Every non-JSON response reported as "session expired" | P2 | *(walmart pending)* |
| `INFRA-004` | VAPT: dependency bumps and `json_script` for four templates | P2 | *(deployment.md pending)* |
| `INFRA-005` | No SMTP, so password reset does not work | P2 | *(deployment.md pending)* |
| `SET-001` | AE/SA marketplace ids missing, blocking the UAE P&L | P2 | *(settings pending)* |
| `INV-CONT-004` | Goods receipt writes AWD stock the sync overwrites | P3 | [inventory](inventory/gaps.md) |
| `INV-RECV-005` | Receipt syncs are neither region-filtered nor scheduled outside the USA | P3 | [inventory](inventory/gaps.md) |
| `INV-SUP-002` | `POLineGroup.pcs` is written and never read | P3 | [inventory](inventory/gaps.md) |
| `INFRA-002` | Kernel upgrade pending, needs a reboot | P3 | *(deployment.md pending)* |
| `INFRA-003` | HSTS still 86400 | P3 | *(deployment.md pending)* |
| `INT-001` | Command Center phase 3 — per-widget config, resize | P3 | *(intelligence pending)* |

Gaps in sections that have no `gaps.md` yet are held here until that section is
written, then moved. They keep their ids.

## Architecture mismatches

Structural disagreements between the code and the business are **not** gaps.
They live in [architecture-mismatches.md](architecture-mismatches.md) —
`ARCH-001` to `ARCH-008`.

## Recently closed

Closed gaps keep their rows in the section file, with the commit that closed
them. Nine inventory gaps were closed in the week to 2026-08-05; see
[inventory/gaps.md](inventory/gaps.md).

| ID | Title | Closed by |
|---|---|---|
| `FIN-003` | Margins divided by gross revenue while the numerator was ex-VAT | `b6a8603` |
| `FIN-004` | Cash-flow page hardcoded `$` for every region | `fd6af91` |
| `WM-002` | Reprocess crashed on `request.user.username` | `bf26090` |
