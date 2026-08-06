<!--
DECISION TEMPLATE — one entry in a section's decisions.md.

Record a decision when a future session could reasonably choose differently.
Not every choice needs an entry; a choice that took a conversation does.

The purpose is to stop the same ground being re-argued. That means the
alternatives and the reason matter more than the decision itself — without
them, a later reader assumes nobody considered the obvious option.
-->

## `<SECTION>-D-<nnn>` · <Title>

| | |
|---|---|
| **Date** | YYYY-MM-DD |
| **Status** | accepted · superseded by `<id>` · reversed |

**Context**
<!-- What made this a question. The constraint or the problem, not the history
     of the conversation. -->

**Decision**
<!-- What was decided, stated as a rule someone can follow. -->

**Alternatives considered**

| Option | Rejected because |
|---|---|
| <option> | <the actual reason> |

**Reason**
<!-- Why the chosen option won. If it was a business call rather than a
     technical one, say so — that tells a later reader they cannot overturn it
     on technical grounds alone. -->

**Consequences**
<!-- What this makes easy, and what it makes hard. Include the costs; a
     decision record that lists only benefits is advertising. -->

**Affected documents** — [<doc>](<doc>.md)
**Affected modules** — `apps/<app>/<module>.py`, `<Class>.<method>`

---

<!--
SUPERSEDING

Never edit a decision to change its meaning. Add a new one, set the old one to
"superseded by <id>", and say in the new record what changed. The old reasoning
is why the code looks the way it does, and deleting it costs more than it saves.
-->
