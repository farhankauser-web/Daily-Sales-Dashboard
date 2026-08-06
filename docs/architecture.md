# Architecture

files: `infinitee/settings.py` · `apps/*/`
verified against: `9038729` · 2026-08-06

How the applications fit together, where data enters, and which boundaries are
real. The map from a business section to the code that serves it.

## Purpose

The documentation is organised by **business section** because that is how the
business thinks. The code is organised by **Django app**, and the two do not
line up. This document is the translation, and it is honest about where the
mismatch costs something.

## The shape

Nine documented business sections are served by eight applications.

| Application | Serves | Note |
|---|---|---|
| `core` | shared decorators and helpers | infrastructure |
| `users` | Settings — accounts, roles, access | clean boundary |
| `dashboard` | **Reporting · Financials · Marketing · Brand Analytics · Intelligence · Settings** | `ARCH-001` |
| `amazon_api` | the SP-API and Ads clients · the Reporting engine · the credentials UI | `ARCH-004` |
| `inventory_planning` | Inventory, entire | clean boundary, and the reference pattern |
| `walmart_mcf` | Walmart, entire | clean boundary |
| `atlas` | Supply Chain, entire | clean boundary |
| `command_center` | the widget board | reads every section |
| `sqp` | nothing — superseded | `ARCH-003` |

**Five of the eight are clean**: one application, one business section, a
boundary a reader can trust. The cost is concentrated in two — `dashboard`,
which serves six sections from one module, and `amazon_api`, which mixes an
integration, an engine and a settings page.

## Where data enters

Everything the business sees originates outside it. Six inbound paths:

| Source | Arrives as | Lands in |
|---|---|---|
| Amazon SP-API — orders, finances, settlements | reports, polled | Reporting, Financials |
| Amazon Ads API — nine detail reports | reports, submitted and polled | Marketing |
| Amazon Marketing Stream | events, continuously via S3 | Marketing |
| Amazon MCF | fulfilment status | Walmart |
| Walmart Marketplace API | orders, and **outbound** shipping updates | Walmart |
| Spreadsheet uploads | files, by hand | Inventory, Financials, Marketing |

Only one path writes **outward** on the business's behalf — Walmart. That is why
its section's decisions are about correctness under concurrency rather than
display. See [walmart/README.md](walmart/README.md).

## How data flows

```
Amazon orders ──→ daily + hourly snapshots ──→ Reporting
                          │                        │
Ads reports ──→ campaign & SKU attribution ────────┤
                          │                        ↓
Settlements ──→ settled actuals ──→ Financials   Intelligence
                                       ↑              ↑
Spreadsheets ──→ costs, targets ───────┘              │
                                                      │
Spreadsheets ──→ POs, containers ──→ Inventory ───────┘

Walmart orders ──→ MCF ──→ tracking ──→ Walmart     (independent)
Trade customers ──→ quotes ──→ POs ──→ invoices     (independent)
```

Two of the nine sections are **islands**: Walmart and Supply Chain share only
this application with everything else. The other seven form one dependency web
with Reporting and Financials at its centre.

## Boundaries that are real, and one that is not

**Real, and enforced by the documentation:**

- **Reporting and Financials measure the same trade differently** — daily order
  reports against Flat File V2, operational against settled. Both expose
  Revenue, Profit and Margin, and merging them would destroy both. The single
  legitimate coupling is the P&L's labelled fallback across the settlement lag.
  See [financials/README.md](financials/README.md).
- **Marketing is paid, Brand Analytics is earned.** Both speak of search terms;
  they measure different populations.
- **Inventory is our own supply chain; Supply Chain is the B2B arm.** The naming
  is the reverse of what anyone expects.

**Not real, and recorded as such:** the boundary between the six sections inside
`dashboard`. A reader cannot tell from the file layout where Reporting ends and
Financials begins, which is `ARCH-001`.

## Shared infrastructure

- **The SP-API and Ads clients** are correctly shared — every section that talks
  to Amazon goes through them. They live in an app named for an integration,
  which is the only thing wrong with them.
- **Permission and marketplace-access checks** are shared decorators plus an
  explicit per-request check. See [settings/users-roles.md](settings/users-roles.md).
- **The completeness log** is written by every ingestion and read by the views
  that decide whether a day may be shown. See
  [marketing/ads-api.md](marketing/ads-api.md).

## Architecture mismatches

Nine are recorded, and this document does not restate them. The register is
[architecture-mismatches.md](architecture-mismatches.md).

Two things worth knowing about how they are used:

- **Refactoring happens when a feature is touched**, never to satisfy the
  register. An entry sitting open for months is fine if nobody is working there.
- **Two entries have already paid for themselves** by naming the canonical
  implementation and the right moment to act — `ARCH-003` saved Brand Analytics
  an analysis, and `ARCH-004` told Settings what to move and what to leave.

## The reference pattern

`inventory_planning` is the shape the rest should move toward as they are
touched: business rules in named service modules — procurement, planning, cash
flow — with thin views that parse a request, call a service and render.

`dashboard` is the opposite: margin arithmetic, VAT handling and table building
inline in views, exercisable only through HTTP. That is `ARCH-008`, and its
recommendation is a rule rather than a project — **new business logic goes in a
service module**, and logic in a view gets extracted when that view is edited
for another reason.

## Related documents

- [deployment.md](deployment.md) — how this runs, and the two environments
- [architecture-mismatches.md](architecture-mismatches.md) — the debt register
- [methodology.md](methodology.md) — how any of it gets documented
