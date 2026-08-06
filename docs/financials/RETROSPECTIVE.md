# Financials retrospective

Written 2026-08-06, after the section was completed and frozen. Evidence for the
rules in [methodology.md](../methodology.md), not a second copy of them.

Scale: 5 feature documents, **0 new gaps**, 7 decisions — all backfilled.

---

## The section with no gaps

Inventory 18, Marketing 10, Reporting 1, Financials 0. That trend is not
diminishing rigour; it is the register working as designed.

Financials produced no gaps because everything that *looked* like one turned out
to be a recorded decision. Three pipelines writing one table is a precedence
design. A P&L falling back to operational figures is a labelled bridge across
the settlement lag. Payouts not tying to monthly revenue is recognition versus
payment. Each was examined, each is deliberate, and each is now a decision
record rather than a gap.

**The one open item, `FIN-001`, was deliberately not registered.** It is carried
in the root index under the older two-part id scheme and asserts that the
referral fee is modelled on gross revenue and never reconciled. It belongs to
`fee-drift.md`, because that machine already performs exactly this comparison for
the fulfilment fee. Its root cause and classification are established when
someone extends the comparison — writing them now would be inventing an analysis.

---

## What the domain boundary did

The instruction that Reporting and Financials are intentionally different domains
arrived before this section was written, and it changed the work rather than
merely labelling it.

Without it, three findings would have been filed as duplication:

- Revenue, Profit and Margin appearing in both sections
- `product_line_analysis` producing a group-level P&L that resembles the
  Financials one
- The P&L reading Reporting's daily figures during the settlement lag

The third is the interesting one. It **is** a real coupling — the only place
Reporting's numbers enter Financials — and the boundary rule is what made it
possible to document it as a *deliberate bridge with a label* rather than as
contamination to be removed. A rule that only ever says "leave it alone" would
have been less useful than one that says what the exception looks like.

---

## The ordering principle earned its place immediately

Purpose → data source → business rules → implementation was directed before
`pnl.md` was written, and `pnl.md` is the first document with a real **Data
source** table. The effect was concrete: the precedence between four inputs had
to be stated *before* any rule that depends on it, which surfaced the question
"what happens when they disagree" as a structural requirement rather than an
afterthought.

Every subsequent document in the section names its source and its grain in the
same place. In `fee-drift.md` the table is the entire point of the machine — one
row is an assumption, the other is a fact, and the document exists to measure
the distance between them.

---

## Nothing promoted

The ordering principle is already recorded, having been directed rather than
discovered. Nothing else in this section reached two independent sections or
prevented a real error.

Two candidates considered and rejected:

- *A section with no gaps is a finding worth stating.* True here, and it is an
  application of "absence of data is not a defect" rather than a new rule.
- *Document the exception when stating a boundary.* Genuinely useful — it is
  what made the operational-fallback coupling documentable — but it has one
  instance. Revisit if a second section needs it.

---

## For the next section

1. Walmart and Supply Chain are next, then Brand Analytics, Intelligence and
   Settings. None carries an `ARCH-` entry except Brand Analytics (`ARCH-003`),
   where the canonical implementation is **already identified** in that entry —
   the dashboard models, not `apps/sqp`. Apply canonical-first by reading it,
   not by re-deriving it.
2. Expect Walmart to have a real state machine, and expect its gaps to be
   operational rather than structural — it is the only section that writes to an
   external system on the business's behalf.
3. `WM-001` is carried in the root index and, like `FIN-001`, should get its
   entry when the document that proves it is written.
