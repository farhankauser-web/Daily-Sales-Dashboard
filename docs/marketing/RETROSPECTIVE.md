# Marketing retrospective

Written 2026-08-06, after the section was completed and frozen. Evidence for the
rules in [methodology.md](../methodology.md), not a second copy of them.

Scale: 7 feature documents, 10 open gaps, 11 decisions, 7 commits. **All 11
decisions were backfilled** — Marketing had no decision log at all, and every
choice recorded here was reconstructed from code and commit messages.

---

## What the Inventory method predicted, and got right

All three failure shapes Inventory predicted appeared, two of them repeatedly:

- **A docstring contradicting its function** — the EMA smoothing blend
  (`MKT-ALLOC-004`). Third instance across the project.
- **Two sources for one fact** — advertised-product data stored in two
  generations of table, with the allocator on the older one (`ARCH-009`).
- **A data state that looks like a defect and is not** — twice, and both were
  environment: allocation output stopping seven weeks ago, and 47% of rows being
  lowest-confidence equal splits. Neither was a code problem.

The verification queue also earned its place. `MKT-ALLOC-003` would have been
filed P3 and forgotten; instead it carries the production query that would make
it P1 today, and says so in its priority field.

---

## What Marketing added that Inventory did not have

**A correctness landmine with zero current blast radius.** `MKT-ALLOC-003` costs
nothing while every ASIN maps to one SKU, and becomes wrong silently the day one
does not — armed by a *catalogue* change, not a code change, so no deploy
gate would catch it. Inventory's gaps were all either live or dormant-by-design;
this is a third state, and the register now has to express "correct today, by
coincidence."

**Two attribution paths that are not rivals.** Campaign profit and SKU
allocation answer different questions from the same source, and reading them as
competing implementations would have produced a bad consolidation. The README
says so explicitly, because the temptation is real.

**A third category of "computed and unused"** — see below.

---

## The mistake worth recording

**I got the root cause of the two allocator gaps wrong, and the wrong answer was
more expensive than the right one.** I wrote that the campaign grain "does not
exist" and that the fix required re-requesting the report from Amazon and
re-fetching history. Both were false: `ads_detail_reports.py` requests
`campaignId` and `advertisedSku` explicitly, and a superset table has been
ingested daily since 2026-05-13.

What went wrong: I read the older service method (`get_advertised_product_summary`,
`groupBy: ['advertiser']`) and stopped. I did not check whether a *newer* path
existed for the same data. The habit that would have caught it — and now does —
is to ask **"is this the only writer of this fact?"** before concluding the fact
is unavailable.

Cost of the error: an expensive, wrong recommendation sitting in the register.
Cost of finding it: one grep, once the question was asked.

---

## Promoted from this section

**"Computed and unread"** now goes to [methodology.md](../methodology.md) as an
analysis pattern. It cleared the promotion bar with four instances across two
sections:

| Instance | The value exists | Nothing reads it for |
|---|---|---|
| `MKT-AMS-001` | `last_ingest_at` per subscription | whether a dataset has gone silent |
| `MKT-ADS-001` | the completeness log | which days never filled |
| `MKT-CAMP-001` | attribution coverage | flagging an estimated profit |
| `INV-SUP-002` | `POLineGroup.pcs` | anything at all |

The sharper half of the pattern is the second column: three of those four values
*are* read, but only for one of the two questions they can answer. "Is this
value used?" misses them; **"which questions could this value answer, and which
does anything ask?"** finds them.

Also sharpened: the existing *two sources for one fact* entry now names this
section's shape — **a newer source exists and a consumer was never repointed** —
because that is what made my wrong root cause plausible.

---

## Not promoted

- *Check whether a failure state later resolves before calling it a defect.*
  Used once, and it is an application of ground truth rather than a new rule.
- *Fix the checker when it flags a correct pattern.* Already stated in the
  methodology's sweep section; Marketing confirmed it rather than adding to it.
- *A proxy figure needs one home and pointers.* This is the existing one-home
  rule meeting a new case.

---

## For the next section

1. Ask **"is this the only writer of this fact?"** before concluding a fact is
   unavailable. It is the specific habit that would have prevented this
   section's one real analytical error.
2. Expect *computed and unread* — three of Marketing's ten gaps are that shape.
3. Backfilling every decision from scratch took roughly as long as one feature
   document. It is worth it, and it is worth doing while the code is open rather
   than at the end.
