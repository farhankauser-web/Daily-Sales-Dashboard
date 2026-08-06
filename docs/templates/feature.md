<!--
FEATURE DOCUMENT TEMPLATE — one business machine per document.

Delete every section you do not need. Never write "N/A".
Core sections (keep unless genuinely irrelevant): Purpose, Business workflow,
Business rules, States, User actions, System behaviour, Known gaps.
Everything else is optional.

Implementation belongs in <feature>-tech.md, not here.
-->

# <Feature>

files: <the code this covers, e.g. apps/inventory_planning/{views,procurement}.py>
verified against: <commit> · <date>

<!-- One or two sentences. What this is, in the language the business uses. -->

## Purpose

<!-- What it exists to accomplish, and what would break without it. -->

## Data source

<!-- REQUIRED where the machine's source is not obvious, or where more than one
     pipeline feeds it. Name the source, its grain, and what it is authoritative
     for. Where several sources contribute, state the PRECEDENCE and what
     happens when they disagree — that is a reconciliation rule inside this
     domain, not duplication to be removed.

     Order matters in a feature document: business purpose, then data source,
     then business rules, then implementation. A reader who does not know where
     a number comes from cannot judge the rules that shape it. -->

| Source | Grain | Authoritative for |
|---|---|---|
| <source> | <per what> | <which figures> |

## Scope

<!-- What this document covers and, where readers get it wrong, what it does
     not. Link the neighbour that does. -->

**Not covered:** <topic> — see [<doc>](<doc>.md).

## Business workflow

<!-- Start to finish, as the business experiences it. A chain is usually
     clearer than prose. -->

```
Supplier → Production plan → Allocation → Container → Receiving → Inventory
```

<!-- Then a short paragraph per step, only where the step is not obvious. -->

## Actors

| Actor | Does |
|---|---|
| <role> | <what they do here> |

## Business rules

<!-- Every rule, numbered so they can be cited. These are the invariants: a
     change that breaks one is a change to the business, not a refactor. -->

1. <Rule, stated as an invariant. "A container may carry pick lines from
   multiple suppliers.">
2. <…>

## States

<!-- Only if the entity has a lifecycle. List every state and what causes each
     transition. -->

| State | Meaning | Entered when | Leaves when |
|---|---|---|---|
| <state> | <what is true> | <trigger> | <trigger> |

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| <action> | <role> | <what must be true> | <what changes> |

## System behaviour

<!-- What the software does on its own: scheduled work, automatic transitions,
     derived values. Name the rule, not the code. -->

## Data model

<!-- CONCEPTS and relationships, not tables or fields. -->

- **<Entity>** — <what it represents>. Carries <relationship> to <Entity>.

## Integrations

| System | Direction | What moves |
|---|---|---|
| <external system> | in / out | <what, and how often> |

## Dependencies

<!-- Other features this relies on, and features that rely on this. Link only. -->

## Validation rules

| Input | Rule | On failure |
|---|---|---|
| <field> | <what is required> | <what the user sees> |

## Edge cases

<!-- The situations that have actually caused trouble, and what should happen.
     Real cases only — do not invent hypotheticals. -->

## Observations — not gaps

<!-- OPTIONAL but valuable. A striking data finding that turns out to be correct
     behaviour goes here, with its SOURCE and why it is not a defect. Without
     this section the next reader rediscovers it and files it as a bug.

     Remember the environment: on the development machine nothing runs on a
     schedule, so stale timestamps, empty tables and never-run jobs are expected
     and belong here rather than in the gap register. -->

*Source: <code | local development data; provisional>.*

- **<the finding, stated as it would first strike a reader>.** <why it is
  correct behaviour.>

## Known gaps

<!-- A table: id, one-line title, classification. The detail lives in gaps.md —
     never copy it here. The classification column is what stops a reader
     assuming every row is a code defect. -->

| Gap | | Classification |
|---|---|---|
| `<SECTION>-<AREA>-<nnn>` | <title> | <missing implementation · bug · configuration · missing operational process · legacy data> |

## Related decisions

- `<SECTION>-D-<nnn>` — <title>

## Related documents

- [<doc>](<doc>.md) — <why a reader would go there next>
