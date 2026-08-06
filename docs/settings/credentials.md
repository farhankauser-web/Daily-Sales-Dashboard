# Credentials

files: `apps/amazon_api/{models,views,services}.py`
verified against: `82744aa` · 2026-08-06

The keys that make Amazon, the Ads platform and the AI provider reachable — one
configuration per marketplace.

## Purpose

Every figure in the application arrives through a credential. When one expires
or is rejected, the symptom appears far away — a report that never lands, a
sync that stops, a table that quietly stops growing — and looks like a defect in
whichever section noticed first.

This is where that cause lives, and where it is tested before it is trusted.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| Entered by an administrator | per marketplace | SP-API and Ads API access for that marketplace |
| Entered by an administrator | per AI provider | model access for briefings |

## Business rules

1. **Credentials are per marketplace.** Amazon issues them per region, and a
   marketplace without its own configuration cannot sync at all.
2. **Secrets are encrypted at rest** and never displayed after saving. A key can
   be replaced, not read back.
3. **A configuration can be tested before it is relied on**, so a bad key is
   found where it is entered rather than in a failing job hours later.
4. **A marketplace can be deactivated without deleting it**, which stops it being
   synced while keeping its history.
5. **Configuration is data, not deployment.** Rotating a key is an
   administrator's action and needs no release.

## Edge cases

- **A rejected credential.** Every job for that marketplace fails, each logging
  its own error — which is how one expired key produces thousands of error rows
  in an unrelated section (`WM-ERR-001` documents that shape).
- **A marketplace configured but never used.** Harmless; it simply never appears
  in data.
- **A marketplace with no configuration.** Its pages render empty, which is
  correct and indistinguishable from having no sales — the reason `SET-001`
  matters.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `SET-001` | AE and SA marketplace ids missing, blocking the UAE P&L | to be established |

`SET-001` is carried from the root index under the older two-part id scheme. It
asserts that the AE and SA marketplace identifiers are absent, which blocks
those regions' P&L. Locally all four marketplaces are configured and active,
which neither confirms nor refutes it — the ids in question are Amazon
marketplace identifiers inside the configuration, not the configuration's
existence. It gets a full entry when checked against production.

## Architecture mismatches

`ARCH-004` — this UI lives in `apps/amazon_api` alongside the Reporting engine
and the shared API clients. Moving it here is that entry's recommendation for
**when Settings is next worked**; `services.py` stays where it is, because a
shared client is correctly shared and only the app's name misleads.

## Related documents

- [users-roles.md](users-roles.md) — who may change these
- [architecture-mismatches.md](../architecture-mismatches.md) — `ARCH-004`
