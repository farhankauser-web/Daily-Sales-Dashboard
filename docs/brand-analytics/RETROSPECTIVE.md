# Brand Analytics retrospective

Written 2026-08-06, after the section was completed and frozen.

Scale: 2 feature documents, 1 gap, 1 decision.

---

## The map was wrong about the boundaries, and the data said so

The build order planned three leaves: search queries, market share, baskets.
Market share turned out to be **derived from the search-query rows** rather than
separately reported — the API that serves the market-share page reads
`BASearchQueryWeekly`.

So it is two documents. One table, one machine, one document; a page is not a
machine. This is the third time the plan and the machine boundary disagreed —
after Inventory's planner splitting into three and Marketing's hourly table
turning out to have two writers — and the third time the **data** settled it
rather than the nav.

---

## Canonical-first cost nothing because it was already done

`ARCH-003` names the canonical Search Query Performance implementation
explicitly: the dashboard's BA models, not `apps/sqp`, which holds no rows in
any of its three tables. The retrospective from Supply Chain said to read the
entry rather than re-derive it, and that was correct — it took one query to
confirm and no analysis at all.

**A well-written mismatch entry is worth more than the analysis that produced
it**, because the analysis happens once and the entry is read every time.

---

## What the section contributed

**A dead table found by asking who writes it.** `BABrandShareWeekly` exists in
the models and its migration and **nowhere else** — no writer, no reader. It is
the purest instance yet of the *computed and unread* family, and the sharpest,
because it was never computed either.

It was found by the habit the Marketing retrospective promoted: asking *which
questions could this value answer, and which does anything ask?* For this table
the answer to both was none.

The recommendation is to **drop it**, not to build a writer, because market
share already comes from the report Amazon actually provides per ASIN.

**A dataset limitation stated as a business rule.** Search Query Performance
measures how we do on queries where we appear, and says nothing about queries
where we do not. That is the most important thing to know about the dataset and
it is invisible in the data itself, so it is rule 4 rather than a footnote.

---

## Nothing promoted

`BA-001` was deliberately left unregistered — the third time. The local sync log
shows 32 pending against 12 collected, which is consistent with the claim *and*
with a machine that stopped running its poller. Those are indistinguishable
without production, and the poller runs there.

That pattern — carry the id, describe it in prose, register it when the evidence
exists — has now happened in Financials, Walmart and here. It is already covered
by the production verification queue rule; noting it rather than promoting it.
