# Inventory retrospective

Written 2026-08-06, after the section was completed and frozen. This is about
**the process**, not about Inventory.

**The rules this produced now live in [methodology.md](../methodology.md)** —
that is the file to read before opening a section. This one is the evidence
behind them: what actually happened, including the mistakes, which is what makes
the rules worth following.

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
findings had to say *code* or *local data*, the strong claims separated cleanly
from the provisional ones. "No container carries a shipment ID" reads like a
defect and is a fact about a development database that runs no scheduled jobs.

It did not stop enough of them. Several gaps still reasoned from local state —
timestamps, row counts, "this has not run since" — and had to be reclassified
once the environment was stated plainly (see below). Naming the source is
necessary; **knowing what the source can and cannot support** is the rest.

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
assumption that a destructive import runs routinely. **No template links it** —
that is code evidence and it holds. **Grep the templates before assessing
severity.**

**Then downgrading it on evidence that could not support the claim.** The same
gap was argued down to P2 partly because "it has not run since 2026-07-20" — a
local timestamp on a machine that runs nothing on a schedule. That says nothing
about whether production ops upload the workbook weekly. The severity is now
explicitly provisional, resting on the code evidence alone.

**Blaming a defect for a state it did not cause.** It was also first written as
having wiped a backfill, which local timestamps disproved. The conclusion was
right and the reasoning was still local-only. **Check whether the accused code
could have executed — and remember that "it did not run here" is not "it does
not run".**

**Not asking which database, early — and not asking what runs there.** Several
hours of findings were built on `db.sqlite3` before anyone asked whether it was
production. It is not, and more importantly **no scheduled job has ever run
against it**: cron, workers and automated imports execute only on the deployed
server, and the laptop is not always on.

That single fact invalidated a chain of reasoning. "The backfill was never
applied", "this import has not run since the seed", "no container was ever
created through the workbench" are all statements about a developer's laptop,
and none of them describe the business. Four gaps were reclassified once it was
stated. **Ask both questions in the first five minutes: which database, and what
executes against it.**

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

**Separate five layers before calling anything a defect.** Code implementation ·
local development state · deployment configuration · scheduled execution ·
production behaviour. Most apparent defects live in the middle three, and each
has a different remedy — a code change cannot fix a job that is not scheduled,
and a schedule cannot fix a laptop that is switched off. Only the first layer
supports a defect claim on its own.

**Two sources for one fact is always a defect.** Found three times: lead times
in two places (`INV-PLAN-001`), the human count read where the derived figure
belongs (`INV-RECV-003`), and a docstring against its own function
(`INV-PLAN-002`). Worth actively looking for in every section.

**Ordering constraints belong in the recommendation.** Two remedies create
exactly the data two latent defects destroy. A register that lists both without
saying which comes first invites losing the work.

---

## What was promoted, and where

Everything general from this section now lives at project level, so it applies
to every domain rather than one:

| Pattern | Promoted to |
|---|---|
| The eight-step section process | [methodology.md](../methodology.md) |
| Ground truth, the five layers, evidence precedence | [methodology.md](../methodology.md), `CLAUDE.md` |
| Read the function not the docstring · grep templates before judging severity · fingerprint data provenance | [methodology.md](../methodology.md) |
| One machine per document, split on discovery | [methodology.md](../methodology.md) |
| State the intended rule, link the gap | [methodology.md](../methodology.md) |
| Root cause · classification · code-alone-fixes-it | [templates/gap.md](../templates/gap.md) |
| Production verification queue, worked at implementation time | [templates/gap.md](../templates/gap.md), `CLAUDE.md` |
| *Observations — not gaps*, and a classified gap table | [templates/feature.md](../templates/feature.md) |

**Do not add general rules here.** A pattern that outlives this section belongs
in `methodology.md` or a template; this file keeps only the Inventory evidence
for why.

---

## What would have saved time

1. **Ask which database, what runs against it, and how it was seeded — before
   measuring anything.** The development machine runs no scheduled jobs at all,
   so every timestamp on it records a manual run. One query on primary-key
   ranges and `updated_at` clustering showed the dataset came from a single seed
   import, and that reframed four gaps.
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

## How Marketing has borne this out

Recorded here because a prediction that held is worth as much as a lesson
learned. Marketing hit all three predicted failure shapes within two documents:

- **A docstring contradicting its function** — the smoothing blend
  (`MKT-ALLOC-004`), the third instance.
- **Two sources for one fact** — none yet, but the campaign→product-group map
  living in a view module (`MKT-ALLOC-001`) is the same class of problem.
- **A data state that looks like a defect and is not** — twice, and both were
  environment rather than code: allocation output stopping seven weeks ago, and
  47% of rows being lowest-confidence equal splits.

It also produced a shape Inventory did not: **a correctness landmine whose blast
radius is currently zero** (`MKT-ALLOC-003`). Amazon's own SKU attribution is
discarded and re-derived, which costs nothing while every ASIN maps to one SKU
and becomes wrong silently the day one does not. Filed at P3 with the production
query that would change it to P1 — a pattern now in
[methodology.md](../methodology.md) as the verification queue.
