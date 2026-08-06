# Methodology

How we document and analyse this system, in any section. Distilled from
Inventory, extended by each section that follows.

`docs/README.md` says what the documents are. `templates/` says what they look
like. **This file says how the work is done**, and it is the one to read before
opening a new section.

---

## The section process

Eight steps, in order. Skipping step 1 has cost the most.

1. **Establish the current state.** Which database, what executes against it,
   how the data got there. See *Ground truth* below.
2. **Document the business machines.** One machine per document.
3. **Discover and classify gaps.** Root cause before recommendation, always.
4. **Backfill architectural decisions** from the code and commit history, while
   the code is open.
5. **Perform a section review** — `python docs/check_docs.py <section>` plus a
   read for sense. The script is not optional: it has caught stale pending
   markers, orphaned ids and duplicated rules in sections that read fine.
6. **Write a retrospective** capturing what generalises.
7. **Promote anything broadly useful** into this file or the templates.
8. **Freeze the section** and move on.

---

## Ground truth

**The laptop is the development environment. Production runs every scheduled job
continuously; the laptop runs none and is not always on.**

So these are expected locally and are **never on their own evidence of a
defect** — only the code can prove one:

stale timestamps · missing scheduled executions · empty or partly populated
tables · absent background processing · jobs that look like they never ran

**Separate five layers** before calling anything a defect:

| Layer | Fixed by |
|---|---|
| Code implementation | a code change |
| Local development state | nothing — it is not the business |
| Deployment configuration | a deploy or a config change |
| Scheduled execution | a cron or worker change |
| Production behaviour | whichever of the above the evidence points to |

Most apparent defects live in the middle three. Only the first supports a defect
claim on its own.

**Evidence precedence** — code → business rules → production data → local
development data. Name the source in the finding. Anything not drawn from code,
architecture or documentation is **provisional** until checked against
production, and says so. Full statement in
[templates/README.md](templates/README.md).

**Two questions in the first five minutes of any section:** which database, and
what executes against it.

---

## Analysis patterns

**Read the function, not the docstring.** Three docstrings in this codebase
describe behaviour their own functions do not implement, and one of them put a
wrong rule into two documents before it was caught. A comment is not code, and a
stale one is worse than none because it is trusted.

**Grep the templates before assessing severity.** A routed endpoint is not a
live endpoint. Two priority calls have turned on whether any page links it.

**Fingerprint data provenance.** Contiguous primary-key ranges, clustered
`updated_at`, and a derived field equalling its source identify
machine-generated rows in seconds — and separate "the business did this" from
"an importer did this". One such check reframed four gaps.

**Two sources for one fact is always a defect.** Found four times so far: lead
times in two places, a human count read where a derived figure belongs, and two
docstrings against their own functions. Worth actively hunting in every section.

**Absence of data is not a defect.** Establish why the state exists before
recommending anything: missing implementation · bug · configuration · missing
operational process · legacy data.

---

## Documentation patterns

**One machine, one document.** The test is not length — it is whether the
document answers one question. **Split on discovery**: the moment a second
machine appears while writing, split immediately rather than finishing something
that will need splitting later. Applied twice — Inventory's planner became
three documents, Marketing's hourly table turned out to have two writers.

**State the intended business rule; link the gap where the code disagrees.** The
document stays a description of the business; the register carries the debt.
Writing today's quirk as though it were the rule makes the defect permanent by
describing it as intent.

**Document the disagreements that look like bugs.** Where two correct machines
legitimately produce different numbers, say so as an edge case. It pre-empts a
bug report and explains a design at once.

**Carry the failure in the rule.** "AWD reports cases, FBA reports eaches —
applying the AWD conversion to an FBA payload reads 1,440 units as 34,560." The
number is what makes a rule stick.

**Record observations that are not gaps.** A striking data finding that turns
out to be correct behaviour belongs in the document under *Observations — not
gaps*, with its source and why it is not a defect. Otherwise the next reader
rediscovers it and files it.

**Cite the incident that settles a decision.** A decision record carrying the
real failure it prevents — a day that collapsed from $7,618 to $289 — survives
a future simplification. One that only states the rule does not.

**A pending document is named, never linked.** Broken links teach readers to
distrust the links that work.

---

## Registers

**Gaps** carry a root cause, a classification, whether code alone fixes it, and
evidence with a named source. The section `gaps.md` is the source of truth; the
root `gaps.md` indexes it. Template: [templates/gap.md](templates/gap.md).

**Every section `gaps.md` carries a Production verification queue** — each
finding that rests on local data, reduced to the single query that settles it.
The queue is worked at **implementation** time, not documentation time: run the
query when the gap is picked up, update the classification if the evidence
moves, then close or re-prioritise. Never block a document on it.

**Decisions** are recorded when a future session could reasonably choose
differently, with the alternatives and why they lost. Backfill them from commit
history while the code is open — nine of Inventory's twenty and six of
Marketing's first six were reconstructed this way, and in an older area they
would not have been recoverable.

**Ordering constraints belong in the recommendation.** Where one fix creates the
data another latent defect destroys, say which comes first.

---

## The consistency sweep

```bash
python docs/check_docs.py            # every section
python docs/check_docs.py marketing  # one section
```

Eight checks, ordered by how often each has actually caught something: broken
links · gap ids defined, listed and indexed · required gap fields including
Classification · decision ids defined, listed and cited · ids referenced but
never defined · business rules stated in two documents · implementation detail
in business prose · `*(pending)*` markers naming a document that now exists.

Non-zero exit on failure, so it can gate a commit. **Fix the script when it is
wrong** — it found two bugs in itself on first run, and a checker nobody trusts
is worse than none.

## Definition of done

No change is complete until, in the **same commit**: the feature document
including its `verified against` line, the gap register, the decision log where
a choice was made, `architecture-mismatches.md` where one was created or closed,
and every index and cross-reference affected.

---

## Section retrospectives

- [inventory/RETROSPECTIVE.md](inventory/RETROSPECTIVE.md) — the first, and the
  source of most of the above
