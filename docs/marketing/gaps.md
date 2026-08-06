# Marketing — gaps

Source of truth for this section. The root [gaps.md](../gaps.md) indexes these.

**Where the evidence comes from.** Counts in this file are measured against the
**local SQLite database** unless stated otherwise. Findings drawn from *code*
transfer to production unchanged; findings drawn from *data* are provisional and
must be re-measured on production Postgres before anyone acts on them, and say
so. See [templates/README.md](../templates/README.md) for the precedence.

Unlike Inventory, this section's pipelines have genuinely run — the AMS stream
processed objects today — so a data finding here is more likely to describe the
business than an unexercised path. That makes it more useful and no less
provisional.

**Absence of data is not a defect.** Every gap carries a **Classification** —
missing implementation, bug, configuration, missing operational process, or
legacy data — a root cause, and whether a code change alone would close it.

| ID | Title | Priority | Classification | Status |
|---|---|---|---|---|

*No gaps filed yet. Two divergences are under investigation and will be filed
with a root cause when the relevant document is written:*

- *campaign snapshots trail the Ads API snapshots by eleven days*
- *SKU allocation last produced rows on 2026-06-16 while its inputs kept running*

Neither is filed as a defect yet, because neither has a cause. Both may be
configuration, a scheduling gap (`INFRA-001`), or deliberate.

---

## Closed

| ID | Title | Closed by |
|---|---|---|
