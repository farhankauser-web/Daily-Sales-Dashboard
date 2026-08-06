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
| **Dependencies** | `<other id>`, or none |

**Current behaviour**
<!-- What happens today. Present tense, specific, no blame. -->

**Expected behaviour**
<!-- What should happen. If a decision fixed this expectation, cite it. -->

**Evidence**
<!-- The query, count or file path that proves the gap, so anyone can re-check
     it. "188 of 188 lines" beats "most lines". -->

**Business impact**
<!-- What it costs the business: wrong numbers, manual work, a decision made on
     bad data. If the answer is "none today", say so — that is why it is open. -->

**Technical impact**
<!-- What it costs the codebase: duplicated logic, a trap the next change will
     hit, a silent failure mode. -->

**Recommendation**
<!-- The fix, specific enough to start from. If it is deliberately deferred,
     say what has to be true before it is worth doing. -->

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
