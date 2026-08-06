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

## Known gaps

<!-- IDs and one-line titles ONLY. The detail lives in gaps.md. -->

- `<SECTION>-<AREA>-<nnn>` — <title>

## Related decisions

- `<SECTION>-D-<nnn>` — <title>

## Related documents

- [<doc>](<doc>.md) — <why a reader would go there next>
