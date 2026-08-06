# Financials — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

Never edit a decision to change its meaning. Add a new one, mark the old
`superseded by`, and say what changed.

| ID | Decision | Date | Status |
|---|---|---|---|
| `FIN-D-001` | Settled actuals first; operational fills the lag, labelled | 2026-07-10 | accepted |
| `FIN-D-002` | One canonical line list defines the statement | 2026-07-10 | accepted |
| `FIN-D-003` | Consolidation sums additive lines and recomputes the rest | 2026-07-10 | accepted |

---

## `FIN-D-001` · Settled actuals first; operational fills the lag, labelled

| | |
|---|---|
| **Date** | 2026-07-10 · **Status** accepted |

**Context** — Amazon settles roughly two weeks in arrears. A P&L for a month
that has just ended has no settlement behind it, and the business still needs to
see the month.

**Decision** — Settled actuals are used wherever they exist. Where a line has
none, the **operational figure** from Reporting's daily data fills it, and the
line is **labelled `operational`** rather than presented as settled. Every line
carries its source.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Show nothing until settlement lands | A blank statement for the two most recent weeks of every month, which is when it is most looked at |
| Fill from operational data without labelling | The whole point of this section is that its figures reconcile; an unlabelled operational figure silently breaks that promise |
| Estimate the settlement from historical fee ratios | Invents the number the settlement will later contradict, and the contradiction would look like a defect |

**Reason** — A visible provisional figure is useful and honest. The label is
what keeps this a reconciliation document rather than a second operational one.

**Consequences** — A month's figures change as settlement lands, which is
correct and must be expected by anyone comparing statements taken on different
days. It is also **the only point where Reporting's numbers enter Financials**,
and that bridge is deliberate — it is not a merging of the two domains, and a
line marked `operational` is a placeholder rather than an answer.

**Affected documents** — [pnl.md](pnl.md), [reporting/daily.md](../reporting/daily.md)

---

## `FIN-D-002` · One canonical line list defines the statement

| | |
|---|---|
| **Date** | 2026-07-10 · **Status** accepted |

**Context** — The statement's structure is needed by five things: the engine
that fills automatic lines, the manual entry form, the Excel importer, the
renderer, and the storage schema. Each could carry its own copy.

**Decision** — **One list** defines every line — its key, label, section, kind,
sign and indent — and all five read it. The list mirrors the client's own
statement of operations, label for label.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Define the structure per consumer | Five copies of one fact; a new line would appear in some places and not others, and the first symptom would be a statement that does not add up |
| Store the structure in the database | Makes the shape editable by anyone at any time, and a P&L whose structure moves cannot be compared month to month |

**Reason** — The structure *is* the agreement with the accountant. Matching
their labels verbatim means a line in Pulse and a line in their statement are
the same line, without translation.

**Consequences** — Adding a line is a code change, deliberately. The label text
is not cosmetic and must not be "improved" — it is what makes the two statements
comparable.

**Affected documents** — [pnl.md](pnl.md)

---

## `FIN-D-003` · Consolidation sums additive lines and recomputes the rest

| | |
|---|---|
| **Date** | 2026-07-10 · **Status** accepted |

**Context** — Four regions trade in four currencies. A consolidated statement
needs one number per line in USD.

**Decision** — Convert each region at a **monthly** FX rate and **sum only the
additive lines**. Percentages, ratios and per-unit figures are **recomputed from
the consolidated totals**, never summed or averaged. A region with no rate for
the month is **reported**, not silently omitted.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Sum every line including percentages | A consolidated margin of 140% from four regions at 35% each — visibly absurd, and the same error is invisible on per-unit lines |
| Weight the percentages by revenue | Correct for margin, wrong for per-unit fees, and it invites the reader to trust a figure whose weighting they cannot see |
| Convert at a daily rate per transaction | More precise and unreconcilable — the statement would not match a monthly rate applied to the totals, which is what the accountant uses |

**Reason** — A ratio of sums is the only consolidated percentage that means
anything. A monthly rate matches how the business's own accounts are prepared.

**Consequences** — A missing FX rate makes the consolidation incomplete rather
than wrong, and says so. Regional statements and the consolidated one will not
tie line-for-line on any percentage, by design.

**Affected documents** — [pnl.md](pnl.md)
