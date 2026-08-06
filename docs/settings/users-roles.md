# Users and roles

files: `apps/users/{models,views}.py` · `apps/core/decorators.py`
verified against: `82744aa` · 2026-08-06

Accounts, the roles they hold, and the two independent things a role controls:
what a person may do, and which marketplaces they may see.

## Purpose

The application holds cost prices, margins and supplier terms. Not everyone who
needs the daily sales figure should see what a towel costs to make.

## Business rules

1. **A user is identified by email.** There is no username field anywhere, and
   reading one raises — which has caused a real error reported to users as
   "session expired".
2. **Permission and marketplace access are separate axes.** A role grants
   capabilities; the account grants marketplaces. Someone may manage costs for
   the UK and see nothing of the USA. See `SET-D-001`.
3. **Marketplace access is enforced per request**, not only in the navigation.
   Changing a marketplace parameter to one you lack does not return data.
4. **A user is deactivated, never deleted**, so their audit trail and attributed
   actions survive.
5. **Capabilities are explicit flags**, not inferred from staff or superuser
   status, so a permission question has one answer rather than two.

## Edge cases

- **A user with access to no marketplace.** Pages fall back to the first
  marketplace they *are* allowed, rather than erroring.
- **A role changed while someone is signed in.** Takes effect on their next
  request, because the check is per request.

## Observations — not gaps

*Source: local development data; provisional.* Two accounts exist locally, which
is a development environment rather than a statement about the team.

## Related decisions

`SET-D-001`

## Related documents

- [credentials.md](credentials.md) — what an administrator can change
