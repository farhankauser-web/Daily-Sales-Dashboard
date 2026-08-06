# Inventory retrospective

Written 2026-08-06, after the section was completed and frozen. This is about
**the process**, not about Inventory. Every section that follows should start
from here.

Scale, for calibration: 10 feature documents, 18 open gaps, 20 decisions, five
commits, one working session. **11 of the 18 open gaps were discovered by
writing the documentation**, not by hunting for bugs. **9 of the 20 decisions
were backfilled** from choices already implemented but never written down.

---

## What worked

**Documenting is the cheapest audit available.** Nobody set out to find defects.
Eleven surfaced anyway, including two that destroy data, because writing "here
is the rule" forces you to check whether the code agrees. Reading code to
summarise it is a different activity from reading code to review it, and it
catches different things.

**Classifying before recommending changed the answers, not just the wording.**
Four Receiving gaps were first written as software defects. On investigation two
were an operational step nobody performs and rows that predate the system. The
recommendations changed completely — from "write code" to "run the command that
already exists". Had they shipped as filed, the work would have been wasted.

**Naming the evidence source stopped a whole class of wrong conclusion.** Once
findings had to say *code* or *dev snapshot*, the strong claims separated
cleanly from the provisional ones. "No container carries a shipment ID" reads
like a defect and is actually a fact about a lightly-used development database.

**Splitting on discovery rather than on plan.** `planner.md` was one document in
the build order. Writing it revealed three machines with different scopes,
different netting and different outputs. Splitting immediately cost less than
finishing a document that would have needed splitting later.

---

## Documentation patterns worth reusing

**State the intended rule; link the gap where code disagrees.** This is the
single most valuable pattern the section produced. Business rule 1 of
`suppliers.md` says suppliers are never created implicitly — and links
`INV-SUP-004`, where the PO upload still does. The document stays a description
of the business; the register carries the debt. The alternative — writing "the
PO upload creates suppliers" as though it were the rule — would have made the
defect permanent by describing it as intent.

**Document the disagreements that look like bugs.** Reorder and the loading plan
produce different numbers for the same SKU, deliberately. Writing that down as
an edge case pre-empts a bug report and explains a design at the same time.

**Carry the failure in the rule.** "AWD reports cases, FBA reports eaches —
applying the AWD conversion to an FBA payload reads 1,440 units as 34,560." The
number is what makes the rule stick.

**A `Not covered` list with links.** Cheap to write, and it is what makes ten
documents navigable rather than ten places the same topic might be.

**Gap fields that force thinking**: Root cause · Classification · Code alone
fixes it. The third is the sharpest — answering "no" out loud is what surfaces
process and data problems disguised as engineering ones.

---

## Mistakes made, and the checks that now catch them

**Trusting a docstring over the function.** Twice. `_supplier_and_fob` is
documented as a three-step priority chain; it narrows to a pool and picks the
cheapest. That wrong rule went into two documents and was caught only on
re-reading the function during review (`INV-PLAN-002`). `commit_packing_list`
still documents a workflow the decision log forbids (`INV-ALLOC-004`).
**Read the function. A comment is not code, and a stale one is worse than none
because it is trusted.**

**Calling a routed endpoint "live".** `INV-CONT-011` was filed P1 on the
assumption that a destructive import runs routinely. No template links it, and
it had not run since the seed. It is P2, latent — still worth fixing, but not
what was first written. **Grep the templates before assessing severity.**

**Blaming a defect for a state it did not cause.** The same gap was first
written as having wiped a backfill. Timestamps disproved it: 128 of 131 rows
untouched since the seed, so the destructive path could not have run. **Check
whether the accused code could have executed in the window.**

**Not asking which database, early.** Several hours of findings were built on
`db.sqlite3` before anyone asked whether it was production. It is not. Ask in
the first five minutes of any section.

---

## Architectural principles to reuse

**One machine, one document.** The test is not length — it is whether the
document answers one question. Three planning documents each answer one; one
combined document would have answered three badly.

**Derived beats stored, and say which.** The supplier ledger, the planner
position and the In-Transit/Receiving partition are all computed on read and
therefore cannot drift. Where something *is* stored deliberately — a snapshotted
FOB rate — the reason belongs in a decision record, because the next reader will
otherwise "fix" it.

**Two sources for one fact is always a defect.** Found three times: lead times
in two places (`INV-PLAN-001`), the human count read where the derived figure
belongs (`INV-RECV-003`), and a docstring against its own function
(`INV-PLAN-002`). Worth actively looking for in every section.

**Ordering constraints belong in the recommendation.** Two remedies create
exactly the data two latent defects destroy. A register that lists both without
saying which comes first invites losing the work.

---

## Conventions that should now be project-wide

All of these are already written into the standards; this records why.

| Convention | Where | Why |
|---|---|---|
| Evidence precedence: code → business rules → production → dev snapshot | `templates/README.md`, `CLAUDE.md` | a data conclusion from the local DB is provisional and must say so |
| Root cause + classification + "code alone fixes it" | `templates/gap.md` | absence of data is not a defect; a code change cannot close a process gap |
| Business architecture first, quirks to gaps or `ARCH-` ids | `CLAUDE.md`, `docs/README.md` | a document that describes a quirk as intent makes it permanent |
| One machine per document, split on discovery | this file | length is not the test; a second question is |
| Document, gaps, decisions and indexes in one commit | `templates/README.md` | a register updated later is a register that drifts |

---

## What would have saved time

1. **Ask which database, and how it was seeded, before measuring anything.** One
   query on primary-key ranges and `updated_at` clustering told us the whole
   dataset came from a single seed import — and that fact reframed four gaps.
2. **Fingerprint data provenance early.** Contiguous PK ranges, identical
   timestamps, and a derived field equalling its source (`received_date ==
   eta_destination`) identify machine-generated rows in seconds and separate
   "the business did this" from "an importer did this".
3. **Read the function bodies of anything you are about to describe as a rule.**
   Docstrings were wrong twice in one module.
4. **Grep the templates for every endpoint** before judging how live a code path
   is. Two severity calls turned on it.
5. **Write the decision log while reading the code, not at the end.** Nine
   decisions were reconstructed from commit messages and comments. They were
   recoverable here; in an older area they would not be.
6. **Run a mechanical consistency sweep before declaring a section done.** Links,
   gap ids defined and indexed both ways, decisions cited somewhere, duplicated
   rules, implementation leakage. It found four real problems in a section that
   read fine.

---

## Applying this to the next section

Marketing is larger and less understood than Inventory. In order:

1. Establish which database, and whether the feature has ever run in production.
2. Read `views.py` and the service modules **before** writing any rule.
3. Expect the same three failure shapes: two sources for one fact, a docstring
   that contradicts its function, and a data state that looks like a defect and
   is a process gap.
4. Split documents the moment a second machine appears.
5. Backfill decisions from commit history while the code is open.
6. Run the consistency sweep before calling it complete.
