# Settings — decision log

Decisions that a future session could reasonably choose differently. Recorded so
the same ground is not re-argued.

| ID | Decision | Date | Status |
|---|---|---|---|
| `SET-D-001` | Permission and marketplace access are separate axes | 2026-06-10 | accepted |

---

## `SET-D-001` · Permission and marketplace access are separate axes

| | |
|---|---|
| **Date** | 2026-06-10 · **Status** accepted |

**Context** — Two different questions have to be answered about every request:
*may this person do this*, and *may they see this marketplace*. They could be
one model — a role per marketplace — or two.

**Decision** — **Two independent axes.** A role carries capability flags; the
account carries the marketplaces it may reach. Marketplace access is enforced
**per request**, not only by hiding navigation.

**Alternatives considered**

| Option | Rejected because |
|---|---|
| A role per marketplace | Multiplies roles by regions, and every new marketplace duplicates the whole permission set |
| Marketplace access implied by role | A cost manager for the UK and one for the USA need identical capabilities and different data; one role cannot express that |
| Hide the navigation and trust it | The marketplace is a request parameter, so hiding a link hides nothing from anyone who changes it |

**Reason** — Capability and scope are genuinely independent, and modelling them
together forces one to be duplicated across the other.

**Consequences** — Every view that takes a marketplace must check access
itself, which is a discipline rather than a mechanism — a new view that forgets
is a hole. The reference pattern is a decorator plus an explicit check, and it
is why a user with no access to a marketplace falls back to one they do have
rather than seeing an error.

**Affected documents** — [users-roles.md](users-roles.md)
