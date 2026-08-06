# Documentation templates

Copy the skeleton, fill it in, delete what does not apply. Four types:

| Type | Template | Used for |
|---|---|---|
| Feature | `feature.md` | one business machine — `containers.md`, `receiving.md`, `sku-allocation.md` |
| Section README | `section-readme.md` | `inventory/README.md`, `marketing/README.md` … |
| Gap | `gap.md` | entries in a section's `gaps.md` |
| Decision | `decision.md` | entries in a section's `decisions.md` |

---

## Four rules that shape every template

**Delete empty sections; never write "N/A".**
A feature document has seventeen possible headings. Most features need eight or
ten. A page of empty headings buries the content that matters and trains the
reader to skim. If a section has nothing to say, remove it.

**Data Model means concepts, not tables.**
Feature documents name business entities and how they relate — *a container
carries pick lines drawn from production plans*. Django models, fields and
migrations belong in a `-tech.md`. If you find yourself writing a field type,
you are in the wrong document.

**Gaps carry evidence.**
The spec's field list does not include one; it should. A gap without a query or
a `file:path` cannot be re-checked, so within a month nobody trusts it enough to
act on. `INV-CONT-001` says "188 of 188" *and* the queryset that proves it.
Evidence is what separates a register from a wishlist.

**Decisions carry a status.**
Decisions get superseded. Without a status field a reader cannot tell a live
rule from a historical one, which is exactly the confusion decision logs exist
to prevent.

---

## Writing style

This reads as an engineering handbook, not notes.

- **Present tense.** "A container carries pick lines." Not "will carry".
- **Business first.** What it does and why, before how.
- **No repetition.** One source of truth per concept — link, do not restate.
- **No speculation.** Every sentence describes current behaviour, desired
  behaviour, or a recorded decision. If it is none of those, cut it.
- **No hedging.** "Receiving cannot exceed packed quantity", not "should
  generally not normally exceed".
- **Numbers over adjectives.** "188 of 188 lines", not "most lines".
- **No implementation detail** outside a `-tech.md`.

## Cross-referencing

Each concept has one home. Everything else links to it.

- Feature → feature: `See [receiving](receiving.md).`
- Feature → gap: `INV-CONT-002` — the id only, never a copy of the text
- Feature → decision: `INV-D-004`
- Feature → mismatch: `ARCH-007`

If two documents describe the same rule, one of them is wrong — and it will be
the one nobody remembers to update.

## Identifiers

| Kind | Shape | Example |
|---|---|---|
| Gap | `<SECTION>-<AREA>-<nnn>` | `INV-CONT-001`, `MKT-AMS-003` |
| Decision | `<SECTION>-D-<nnn>` | `INV-D-001` |
| Mismatch | `ARCH-<nnn>` | `ARCH-007` |

Three digits, never reused, never renumbered. A closed gap keeps its id and its
row so the register doubles as history.

Section prefixes: `INV` `MKT` `REP` `FIN` `WM` `BA` `INT` `SC` `SET`.

## Where gaps live

The section `gaps.md` is the **source of truth**. The root `docs/gaps.md` is an
index — id, title, priority, status, link — and is updated in the same commit.
If the index starts drifting from the sections, we generate it instead of
maintaining it by hand.

## Definition of done

No change is complete until, in the **same commit**:

- the feature document is updated, including its `verified against` line
- the gap register is updated — closing a gap keeps the row and records the commit
- a decision is recorded if one was made
- `architecture-mismatches.md` is updated if a mismatch was created or resolved
