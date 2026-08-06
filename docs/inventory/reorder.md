# Reorder

files: `apps/inventory_planning/{reorder,views,models}.py`
       `templates/inventory_planning/reorder.html`
verified against: `3c62773` · 2026-08-06

Reorder answers the purchasing question: **what do we need to buy that does not
exist yet, and from whom.** Approving a suggestion creates a draft purchase
order.

## Purpose

The loading plan asks what to ship to one region. Reorder asks what to
manufacture, and that question cannot be asked per region — a factory run serves
every market at once, and four regions each ordering their own shortfall would
place four small orders where one belongs. So reorder **pools demand across all
regions** and nets against the whole open-PO book.

It is the only machine in the section that creates a commitment rather than
describing one.

## Scope

Covers suggestion generation, review, and the draft POs approval produces.

**Not covered:**
- the position suggestions are computed from — [planner.md](planner.md)
- per-region shipping quantities — [loading-plan.md](loading-plan.md)
- what happens to a draft PO afterwards — [purchase-orders.md](purchase-orders.md)

## Business workflow

```
Regenerate → pooled demand vs pooled supply → Suggestions
                                                  ↓
                                    Approve → draft PO per supplier
                                    Dismiss → recorded, not re-suggested blindly
```

## Actors

| Actor | Does |
|---|---|
| Planning | regenerates suggestions, reviews, approves or dismisses |
| Purchasing | works the resulting draft POs into real orders with the factory |
| The system | pools, nets, picks a supplier, sizes the quantity, dates readiness |

## Business rules

1. **Demand is pooled across all four regions.** A SKU's need is the sum of what
   every region requires over its own lead time and tier target — regions have
   different shipping lanes, so each contributes its own figure.
2. **Supply is pooled and region-blind**: on-hand everywhere, in transit
   everywhere, plus **all** open PO balance. Unshipped factory commitment serves
   whichever region ends up needing it.
3. **A suggestion is the gap, or there is no suggestion.** Where pooled supply
   covers pooled demand, nothing is proposed.
4. **Quantities round up to a whole carton, then to the minimum ship quantity** —
   the same rounding as the loading plan, for the same reason.
5. **The supplier is proposed, not assumed.** The candidate pool is every
   factory holding open balance for that SKU; where none does, every factory
   that has ever supplied it. Within that pool the **cheapest agreed rate**
   wins, ties broken by the most recent order. A rate of zero is treated as
   unknown rather than free, so an unpriced line never wins on price. The
   recommendation is reviewable and the reviewer overrides it.
6. **A suggestion with no supplier is never silently dropped.** It is shown, and
   approval skips it with a message naming the count — a SKU nobody has ever
   bought needs a human to choose a factory.
7. **Approval creates a draft purchase order per supplier**, grouped into one
   category block per product category, with a production plan per block —
   the same shape a PO workbook import produces.
8. **A draft PO is a proposal, not a commitment.** It carries no balance the
   business acts on until purchasing confirms it. See
   [purchase-orders.md](purchase-orders.md).
9. **Regenerating replaces un-actioned suggestions and preserves history.**
   Approved and dismissed suggestions survive, so a dismissal is a decision on
   record rather than a row that quietly reappears.

## States

| State | Meaning | Leaves when |
|---|---|---|
| Suggested | proposed, awaiting review | approved, dismissed, or replaced by a regeneration |
| Approved | became a line on a draft PO | terminal |
| Dismissed | reviewed and rejected | terminal |

## User actions

| Action | Who | Precondition | Result |
|---|---|---|---|
| Regenerate | planning | — | un-actioned suggestions rebuilt from the current position |
| Override cover days | planning | — | a flat horizon replaces each SKU's tier target |
| Approve | planning | suggestion has a supplier | draft PO created or extended |
| Dismiss | planning | — | recorded as reviewed and rejected |

## System behaviour

- Regeneration runs the projection **for all four regions** and pools the
  results, so it is the most expensive read in the section and is triggered
  deliberately rather than on page load.
- Each suggestion stores its own working — demand per day, on-hand, in transit,
  open PO, and the per-region breakdown — so a reviewer can see why the number
  is what it is without re-deriving it.
- Target-ready date is today plus the **supplier's** production lead time.
- Draft PO numbers are `DRAFT-<supplier>-<date>`, suffixed if that number
  already exists, so approving twice in a day does not collide.

## Data model

- **Reorder suggestion** — one SKU's proposed purchase: quantity, proposed
  supplier and rate, the netting it came from, the per-region breakdown, and a
  review status.
- Approval writes into the purchase-order structure; reorder owns nothing after
  that point.

## Edge cases

- **A SKU with no purchase history.** No supplier can be proposed. The
  suggestion is shown with the quantity and skipped on approval, with the count
  reported.
- **Reorder and the loading plan disagree.** Expected, and not a defect. The
  loading plan nets against balance **reserved to one region**; reorder nets
  against **all** open balance. A SKU can therefore need loading in the USA while
  needing no new purchase, because the units already exist on a PO reserved
  elsewhere. Read the loading plan to ship, reorder to buy.
- **Approving suggestions for one supplier twice in a day.** A second draft PO
  is created with a suffixed number rather than extending the first, so the two
  batches stay separable.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INV-PLAN-001` | lead times exist twice — reorder uses the supplier's, the projection uses region constants | bug |
| `INV-PLAN-002` | the supplier-choice docstring describes a rule the code does not follow | bug — stale docs |

## Related decisions

`INV-D-017`

## Related documents

- [planner.md](planner.md) — the position every suggestion is derived from
- [loading-plan.md](loading-plan.md) — the per-region shipping view
- [purchase-orders.md](purchase-orders.md) — what a draft PO becomes
- [suppliers.md](suppliers.md) — the lead times that date a suggestion
