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
| `FIN-D-004` | Correcting a cost restates every figure derived from it | 2026-07-18 | accepted |
| `FIN-D-005` | Fee drift is reported, never auto-applied | 2026-07-02 | accepted |
| `FIN-D-006` | A payout is money received, never a P&L line | 2026-07-10 | accepted |
| `FIN-D-007` | Monthly targets are pro-rated to the window viewed | 2026-06-20 | accepted |

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

---

## `FIN-D-004` · Correcting a cost restates every figure derived from it

| | |
|---|---|
| **Date** | 2026-07-18 · **Status** accepted |

**Context** — Cost of goods is frozen into several stored layers at write time:
the P&L cost line, per-SKU daily margins, daily totals, hourly figures and
campaign profit. A COGS upload corrects the input after all of them exist.

**Decision** — A COGS upload **restates every derived layer** for the affected
month, using formulas that mirror the original write path exactly.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Apply new costs forward only | Two margins for the same month depending on when you looked, with nothing saying which is current |
| Compute margins live from cost rather than storing them | Correct, and it makes every dashboard read join across several tables per SKU per day; the storage exists for a reason |
| Restate only the P&L | The P&L would then disagree with the SKU table it is meant to summarise |

**Reason** — A cost correction is a correction of a *fact*, and every figure
that used the wrong fact is wrong until restated. Partial restatement produces
disagreement between views, which is worse than either state alone.

**Consequences** — The recalculation must mirror the original formulas exactly;
if the two ever diverge, a correction introduces a discrepancy rather than
removing one. That coupling is deliberate and is the reason the formulas are
duplicated rather than approximated. Restating a settled month changes the cost
line only — settled revenue and fee lines are Amazon's figures and are not ours
to restate.

**Affected documents** — [cogs.md](cogs.md), [pnl.md](pnl.md)

---

## `FIN-D-005` · Fee drift is reported, never auto-applied

| | |
|---|---|
| **Date** | 2026-07-02 · **Status** accepted |

**Context** — The fulfilment fee assumed for each product is uploaded by the
business. Settlement reports say what Amazon actually charged. Where they
differ, the system could simply adopt Amazon's figure.

**Decision** — Drift is **reported**, with a severity that combines percentage
and cash impact, and a corrected schedule is offered as an **export**. The
assumed fee is never overwritten automatically.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Adopt the settled fee automatically | A temporary adjustment or a one-off charge becomes a permanent assumption, and margins move with no human deciding they should |
| Alert on percentage alone | A 30% drift on a product selling two units a month buries a 3% drift on the best seller |
| Alert on cash impact alone | Misses a large proportional error on a product just starting to scale |

**Reason** — The uploaded fee is a business assumption, and changing an
assumption is a decision. Reporting the difference with its cash impact puts
that decision in front of someone with the information to make it.

**Consequences** — Drift persists until someone acts on it, so the report is
only useful if it is read — which is why severity and cash impact exist rather
than a flat list. Low-volume SKUs are excluded below a unit floor, so a genuine
drift on a slow product is invisible until it sells enough to matter.

**Affected documents** — [fee-drift.md](fee-drift.md)

---

## `FIN-D-006` · A payout is money received, never a P&L line

| | |
|---|---|
| **Date** | 2026-07-10 · **Status** accepted |

**Context** — Amazon disburses money periodically. That disbursement is the most
tangible financial event the business sees, and the temptation is to treat it as
the revenue figure.

**Decision** — A payout is recorded as **money received** and is **never** a P&L
line. The revenue it settles was recognised when Amazon posted it. The bank
transfer row in the transaction report is excluded from the statement entirely.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Recognise revenue on disbursement | Cash-basis accounting, which the business does not use, and it would put a month's revenue in whichever month Amazon happened to pay |
| Show payouts as a P&L line for visibility | Double-counts against the revenue already recognised, and no reader could tell which line to trust |

**Reason** — Recognition and payment are different events separated by Amazon's
reserve and settlement cycle. Conflating them makes the statement unreconcilable
with the accounts.

**Consequences** — Payout totals and monthly P&L revenue **will not agree**, and
should not be expected to. Answering "Amazon recognised this in June, where is
it?" requires reading both views, which is why they are documented as
neighbours.

**Affected documents** — [payouts.md](payouts.md), [pnl.md](pnl.md)

---

## `FIN-D-007` · Monthly targets are pro-rated to the window viewed

| | |
|---|---|
| **Date** | 2026-06-20 · **Status** accepted |

**Context** — Targets are set monthly. The dashboard is read over today,
yesterday, seven days, thirty days, month-to-date and custom ranges.

**Decision** — A monthly target shown against any window is **pro-rated by
days**: the monthly figure ÷ days in the month × days in the window. Matching
between a target and a product group **ignores pack-size formatting**.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Show the monthly target unchanged against any window | A seven-day view compared against a monthly target always reads as catastrophic failure |
| Show targets only on month-to-date views | Removes the comparison from every view people actually use daily |
| Weight the pro-rating by historical daily seasonality | More accurate and unexplainable; nobody could reproduce the target by hand |

**Reason** — A flat daily pro-rate is the only division anyone can verify
mentally, which matters for a number used to judge performance.

**Consequences** — A month with genuine intra-month seasonality is judged
against an even target, so early-month performance can look weak and recover.
Pack-size matching being lenient means a mistyped target still applies, which is
the intended trade — a missed target is worse than a loosely matched one.

**Affected documents** — [targets.md](targets.md), [reporting/daily.md](../reporting/daily.md)
