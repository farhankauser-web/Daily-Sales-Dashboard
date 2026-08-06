# Intelligence — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

| ID | Decision | Date | Status |
|---|---|---|---|
| `INT-D-001` | The model writes prose; it never computes a figure | 2026-07-05 | accepted |
| `INT-D-002` | An alert is resolved by a person, never expired | 2026-06-28 | accepted |

---

## `INT-D-001` · The model writes prose; it never computes a figure

| | |
|---|---|
| **Date** | 2026-07-05 · **Status** accepted |

**Context** — The briefings describe yesterday's trading. A language model could
be given raw data and asked to analyse it, or given finished figures and asked
to explain them.

**Decision** — Every figure is **computed in code before the prompt is built**
and passed in complete. The model summarises, ranks and explains. It is never
asked to calculate, and no number it produces is stored.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Give it raw data and let it analyse | An arithmetic error arrives in fluent prose, which is the hardest kind to catch, and every figure in the business would need re-checking |
| Let it compute and validate afterwards | The validation is the calculation; having done it, there is no reason to have asked twice |
| Have it write queries against the database | Same failure with more privilege |

**Reason** — A figure and a sentence have different failure modes. A wrong
figure computed in code is reproducible and testable; a wrong figure in
generated prose is neither, and it is stated confidently.

**Consequences** — The briefing bundle must be assembled before any prompt runs,
so a new briefing means extending the bundle rather than changing a prompt. The
model cannot answer a question the bundle does not already contain — which is a
limitation and a guarantee at the same time.

**Affected documents** — [ai-briefings.md](ai-briefings.md)

---

## `INT-D-002` · An alert is resolved by a person, never expired

| | |
|---|---|
| **Date** | 2026-06-28 · **Status** accepted |

**Context** — Alerts are raised by scheduled checks. A condition often stops
being true on its own — stock arrives, a campaign is paused, a fee corrects.

**Decision** — An alert stays open until **a person resolves it**. Nothing
auto-closes on the condition clearing.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Auto-close when the condition clears | The alert disappears without anybody knowing it existed, which is precisely the case worth reviewing — something went wrong and recovered |
| Expire after a fixed period | Time is not evidence that anyone looked |
| Auto-close and keep a history | Better, and the history is then a second list nobody reads; the open list is the one people actually look at |

**Reason** — The alert list is a work queue, not a status display. An item
leaves a work queue when somebody deals with it.

**Consequences** — Stale alerts accumulate if nobody works the list, and the
list becomes less useful the less it is used. Raising the same condition once
rather than per run is what keeps that from compounding.

**Affected documents** — [alerts.md](alerts.md)
