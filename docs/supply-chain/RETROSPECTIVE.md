# Supply Chain retrospective

Written 2026-08-06, after the section was completed and frozen. Evidence for the
rules in [methodology.md](../methodology.md), not a second copy of them.

Scale: 4 feature documents, **0 gaps**, 7 decisions — all backfilled.

---

## The section with a specification

Atlas is the only section built against a written **Statement of Work**, and the
code cites its section numbers — pricing at §15, backorders at §91–93, the RFQ
turnaround at §13. That changed the work in one specific way: the decision log
was **easier to backfill and harder to be confident about**.

Easier, because the reasoning was often right there in a docstring citing a
clause. Harder, because a cited clause tells you *what* was decided and not
*what else was considered* — and a decision record without its alternatives is
the thing this project has repeatedly said is worthless.

So the seven decisions here reconstruct alternatives from the shape of the code
rather than from commit messages. `SC-D-002` is the clearest case: full
snapshots rather than diffs is visible in the model, but *why* required reasoning
about what reconstructing version four from three diffs would cost when a
customer queries a price weeks later.

**A specification is not a decision log.** It records the conclusion; the log
records why the conclusion beat the alternatives. Both are needed.

---

## Zero gaps, for a different reason than Financials

Financials had none because everything that looked like a gap was a recorded
decision. Atlas has none because **the section is new and has been exercised
exactly once**.

One customer, one product, one RFQ answered, one quotation won with three
revisions, one purchase order received through seven stages, one backorder
resolved, one over-commitment logged, and **no invoices at all**.

That is a smoke test, and it is exactly what local data is for: it confirms every
path executes. It cannot show what breaks at volume, and this retrospective
should not pretend otherwise. **The absence of gaps here is weaker evidence than
in any previous section**, and that is worth stating plainly rather than
presenting four documents and a clean register as though they were equivalent to
Walmart's 616 orders.

The honest summary: nothing in the code looked wrong, and almost nothing has been
run.

---

## What Atlas contributed

**A naming collision documented at both ends.** *Supply Chain* in this
application means the B2B arm; the physical supply chain for the Amazon brand is
Inventory. Both READMEs now say so in their opening lines. This is the third
naming collision found — after the two "MCF"s and the two "cash flow"s — and the
only one where the confusing name is a whole section rather than a page.

**A business rule that is a formula.** `SC-D-001` — price derived from
dimensions and GSM rather than typed — is the first decision in the project
where the rule *is* an equation. Documenting it as a rule rather than as
implementation was the right call: the equation is the commercial agreement, and
a future session that "simplifies" it to an editable price field would break the
agreement, not the code.

---

## Nothing promoted

Two candidates, both rejected:

- *Where a written specification exists, cite it and treat disagreement as a
  finding.* One section. Recorded inside this section's README instead, where it
  applies.
- *State when a clean register is weak evidence.* Genuinely useful and arguably
  general, but it is an application of naming the evidence source — which the
  methodology already requires. Adding it would restate an existing rule in
  different words.

---

## For the next sections

Three remain, all small: Brand Analytics, Intelligence and Settings.

1. **Brand Analytics carries `ARCH-003`, and its canonical implementation is
   already named in that entry** — the dashboard's BA models, not `apps/sqp`,
   which holds no rows. Read the entry; do not re-derive it. `BA-001` is carried
   in the root index and gets its entry when the document that proves it exists.
2. **Intelligence carries `INT-001`**, already registered.
3. **Settings carries `ARCH-004` and `SET-001`.** `ARCH-004` overlaps work
   already recommended in Reporting — the credentials UI moving out of an app
   named for an integration — so read that entry before proposing anything.
