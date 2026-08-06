<!--
GAP TEMPLATE — one entry in a section's gaps.md.

A gap is missing or wrong functionality measured against the business, not a
bug report. Bugs get fixed; gaps get tracked because they are known, accepted
for now, and someone decided when to close them.

Evidence is required. A gap that cannot be re-checked in thirty seconds is one
nobody will trust enough to act on.
-->

## `<SECTION>-<AREA>-<nnn>` · <Title>

| | |
|---|---|
| **Priority** | P1 / P2 / P3 |
| **Status** | open · in progress · blocked · closed (`<commit>`) |
| **Classification** | missing implementation · bug · configuration · missing operational process · legacy data |
| **Code alone fixes it** | yes · no — <what else has to happen> |
| **Dependencies** | `<other id>`, or none |

**Current behaviour**
<!-- What happens today. Present tense, specific, no blame. -->

**Expected behaviour**
<!-- What should happen. If a decision fixed this expectation, cite it. -->

**Root cause**
<!-- WHY the system is in this state, with evidence. Absence of data is not
     itself a defect: a feature can be correct and unused, correct and never
     configured, or correct and fed by a process nobody performs. Decide which
     before recommending anything.

       missing implementation      the code to do it was never written
       bug                         the code is wrong or destroys data
       configuration               built, correct, not switched on or not scheduled
       missing operational process the software works; nobody performs the step
       legacy data                 rows that predate the mechanism; code is fine

     More than one may apply — say which is primary. If two causes compound,
     say which must be fixed first, because fixing the other alone will not
     hold. -->


**Evidence** — source: code · business rules · production · dev snapshot
<!-- The query, count or file path that proves the gap, so anyone can re-check
     it. "188 of 188 lines" beats "most lines".

     NAME THE SOURCE, and use the precedence in templates/README.md. A finding
     read from the code holds in production. A finding counted out of the local
     SQLite snapshot is PROVISIONAL — say so, and say what to re-measure —
     because whole feature paths have never run against that database, so an
     empty table there means "never used locally", not "broken". -->

**Business impact**
<!-- What it costs the business: wrong numbers, manual work, a decision made on
     bad data. If the answer is "none today", say so — that is why it is open. -->

**Technical impact**
<!-- What it costs the codebase: duplicated logic, a trap the next change will
     hit, a silent failure mode. -->

**Recommendation**
<!-- The fix, specific enough to start from. It must follow from the root
     cause: a code change cannot close a process gap, and a process change
     cannot survive a defect that erases its work. If it is deliberately
     deferred, say what has to be true before it is worth doing. -->

**Related documents** — [<doc>](<doc>.md)
**Related decisions** — `<SECTION>-D-<nnn>`

---

<!--
LIFECYCLE

  open         known, evidenced, nobody working on it
  in progress  being worked now
  blocked      waiting on a dependency or a business answer — say which
  closed       fixed; record the commit and KEEP THE ROW

A closed gap is never deleted. The register is the history as well as the
backlog, and "why is this like that" is answered by the closed rows.
-->
