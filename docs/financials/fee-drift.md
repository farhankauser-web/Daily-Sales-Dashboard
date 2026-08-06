# FBA fee drift

files: `apps/dashboard/fba_drift.py` · `apps/dashboard/views.py` — `fba_fee_drift`
       `templates/dashboard/fba_drift.html`
verified against: `9227433` · 2026-08-06

Whether the fulfilment fee we assume for a product is the fee Amazon actually
charges. The check that stops a margin being confidently wrong.

## Purpose

Every margin in the application subtracts a fulfilment fee that the business
uploaded. Amazon changes its fees — by size tier, by season, by reclassifying a
product — and it does not announce which of our SKUs moved.

A fee that has drifted upward makes a product look profitable when it is not,
and the error is invisible precisely because the assumption is ours.

## Data source

| Source | Grain | Authoritative for |
|---|---|---|
| FBA fee rate upload | per SKU, effective from a date | the fee we **assume** |
| Settlement fee actuals | per SKU per day, from settlement reports | the fee Amazon **charged** |

The comparison is the point: one is an assumption, the other is a fact, and the
document exists to measure the distance between them.

## Business rules

1. **Drift is measured over a rolling window**, not a single day, because a
   single day's fee can be distorted by returns and adjustments.
2. **Low-volume SKUs are excluded**, below a floor of units in the window. A
   drift computed from two units is noise presented as a finding.
3. **Severity combines percentage and money.** A large percentage on a trivial
   product is not urgent; a small percentage on a high-volume one is. A SKU is
   flagged when either the percentage or the cash impact crosses its threshold.
   See `FIN-D-005`.
4. **The impact figure is what makes it actionable** — the fee difference
   multiplied by the units it applied to, so the list sorts by money rather than
   by percentage.
5. **A corrected fee schedule is exportable**, because the remedy is to re-upload
   the fees and the export is the file to upload.
6. **Drift is reported, never auto-applied.** Amazon's charge may be a temporary
   adjustment; overwriting the assumption automatically would launder a
   correction into an assumption. See `FIN-D-005`.

## Edge cases

- **A SKU with actuals and no uploaded rate.** Nothing to compare; it appears in
  the missing-rates list rather than as zero drift.
- **A product Amazon has reclassified.** Shows as sustained one-directional
  drift, which is the signal the reclassification happened.
- **A single day of unusual fees.** Damped by the window, deliberately.

## Observations — not gaps

*Source: local development data; provisional.* No FBA fee rates are uploaded
locally, so nothing can drift here. The comparison needs both sides.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `FIN-001` | referral fee computed on gross revenue, never checked against a settlement | to be established |

`FIN-001` is carried from the root index under the older two-part id scheme. It
asserts that the **referral** fee — a different fee from the fulfilment fee this
document compares — is modelled as a percentage of gross revenue and has never
been reconciled against what Amazon actually deducted. This machine already
does exactly that for the fulfilment fee, which is why the gap belongs here: the
mechanism exists and is pointed at one fee rather than two.

Its root cause and classification are established when someone extends the
comparison, not before.

## Related decisions

`FIN-D-005`

## Related documents

- [cogs.md](cogs.md) — where the assumed fees are uploaded
- [pnl.md](pnl.md) — where the settled fees land
