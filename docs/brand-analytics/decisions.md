# Brand Analytics — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

| ID | Decision | Date | Status |
|---|---|---|---|
| `BA-D-001` | Reports are requested per ASIN, for sellers only | 2026-06-05 | accepted |

---

## `BA-D-001` · Reports are requested per ASIN, for sellers only

| | |
|---|---|
| **Date** | 2026-06-05 · **Status** accepted |

**Context** — Amazon retired the brand-aggregate variants of the Brand Analytics
reports, so each must be requested per ASIN per week. Our catalogue holds
hundreds of ASINs, most of which sell little or nothing in a given week.

**Decision** — Submit **one report per ASIN per week**, and default the ASIN list
to **those that sold at least one unit in the last thirty days**. A manual list
can override it.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| Request every catalogue ASIN | Burns API quota on reports that return nothing, and quota is the binding constraint on how much Brand Analytics we can collect at all |
| Request only the top sellers by revenue | An emerging product is exactly the one whose search data is most worth watching, and it is not yet a top seller |
| Request on demand when a page is opened | These reports take minutes to build; a page cannot wait for one |

**Reason** — Recent sales is the cheapest available proxy for "worth asking
about", and it includes new products from their first sale rather than after
they become significant.

**Consequences** — A product that has not sold for a month collects no data, so
its search decline is invisible in exactly the period someone would want to
diagnose it. The override exists for that case and must be used deliberately.

**Affected documents** — [README.md](README.md)
