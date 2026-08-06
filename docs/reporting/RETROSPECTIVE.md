# Reporting retrospective

Written 2026-08-06, after the section was completed and frozen. Evidence for the
rules in [methodology.md](../methodology.md), not a second copy of them.

Scale: 6 feature documents, 1 open gap, 5 decisions. **Every decision was
backfilled.** The single gap is the lowest count of any section so far, and that
is the finding rather than an oversight — see below.

---

## The section that mostly did not need gaps

Inventory produced 18 gaps, Marketing 10, Reporting 1. That is not because
Reporting was examined less carefully; it is because **most of what looks wrong
here is already recorded as an architecture mismatch**, and mismatches are not
gaps.

`ARCH-001`, `ARCH-003`, `ARCH-004`, `ARCH-006` and `ARCH-007` all land in this
section. Each would have been filed as a gap by a reader who did not know the
register existed. Keeping the distinction — *a gap is missing or wrong
behaviour, a mismatch is a structure that costs more than it should* — is what
stopped this section's register filling with things that are not defects.

---

## Canonical-first, applied before anything was written

The instruction to name the canonical implementation before recommending a
refactor was applied to `ARCH-007` as step zero, and it changed the entry
materially:

- **Consolidation had already begun and was never recorded.** One of the three
  paths had been migrated onto the canonical builder, with a code comment saying
  so. The register had it as four untouched peers.
- **One of the four was not a builder of that table at all.**
  `product_line_analysis` answers a different question over a different window
  from a different source. Counting it overstated the duplication.
- **The old recommendation would have made things worse before making them
  better** — extract a new module, migrate four callers — creating a fourth
  implementation on the way to removing three.

The revised entry is one straggler, one canonical function that already carries
the business rules, and an explicit instruction not to design a third. Effort
fell from 2–3 sessions to 1–2.

**The general lesson, now in the methodology:** when the same fact is produced
twice, ask *"which one is already right?"* before asking *"how do we merge
these?"* The canonical one is usually the newer, wider, more rule-complete one,
and usually already has a consumer.

---

## What this section contributed

**Suppression as a design principle, stated three times independently.** Hourly
Patterns excludes an incomplete day entirely (`REP-D-003`); Historical ends at
yesterday (`REP-D-005`); the Marketing completeness contract suppresses rather
than estimates (`MKT-D-007`). All three trade a visible hole for a wrong number,
and all three carry the same consequence: **a hole reads as a bug to anyone who
does not know the rule.** That consequence is now written into each decision,
because it is the predictable support question.

**"The only estimation permitted here."** `REP-D-004` distributes Brands and
Display spend uniformly across hours, and says in the record that it is the only
estimate in the view and why uniformity was chosen over anything shaped —
uniformity adds the least information. A decision that names itself as the sole
exception is easier to defend than one buried among behaviours.

---

## Not promoted

- *Suppression over estimation.* Three instances, but all are applications of
  the existing rule that documents state what is true rather than what is
  convenient. It is a business decision that recurs, not a method.
- *Mismatches are not gaps.* Already in `docs/README.md` and the templates;
  Reporting confirmed it rather than adding to it.

---

## For the next section

1. Check `architecture-mismatches.md` **before** filing anything structural. In
   this section it accounted for five findings that would otherwise have become
   gaps.
2. Expect the canonical-first question to change a recommendation, not just
   confirm it. It has now done so twice.
3. Financials is next and carries `ARCH-001` and `ARCH-005`. **Do not go
   looking for duplication between the two.** Reporting is built from the daily
   order reports and answers *what is happening*; Financials is built from
   Amazon's Flat File V2 and answers *what Amazon recognised and settled*. Both
   expose Revenue, Profit and Margin, and those are different measurements of
   the same trade, not copies. An earlier draft of this file suggested
   `product_line_analysis` might duplicate the Financials P&L; that framing was
   wrong and is corrected in [methodology.md](../methodology.md).
