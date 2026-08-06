# Settings

Who may use the application, what it may reach, and what it sells. Credentials,
users and roles, and the product catalogue.

## Purpose

Three things every other section depends on and none of them owns: the API
credentials that make Amazon and the Ads platform reachable, the users and
permissions that decide who sees which marketplace, and the catalogue that gives
a SKU its name, ASIN and identity.

Nothing here is a business outcome. Everything here is a precondition for one.

**This section is complete and frozen** except for future feature changes. Three
features are documented; the process lessons are in
[RETROSPECTIVE.md](RETROSPECTIVE.md).

## Features

| Document | Covers | Open here when |
|---|---|---|
| [credentials.md](credentials.md) | Amazon, Ads and AI provider configuration · `ARCH-004` | a marketplace stops syncing or a key needs rotating |
| [users-roles.md](users-roles.md) | accounts, roles, permission flags, marketplace access | someone sees too much or too little |
| [catalog.md](catalog.md) | the product catalogue | a SKU's name, ASIN or grouping is wrong |

## Ground truth

*Source: local development data; provisional.*

| | Local |
|---|---|
| Marketplace configurations | 4 — usa, uk, ae, sa, all active |
| Users | 2 |
| Products | 494 |

## Architecture mismatches

`ARCH-004` — `apps/amazon_api` carries three unrelated responsibilities: the
credentials UI, which is a **Settings** concern; the dashboard data engine,
which is **Reporting**; and the shared API clients, which are correctly shared
infrastructure. Its own recommendation is to move the credentials UI **when
Settings is next worked**, and to leave `services.py` alone — only its naming
misleads.

## Navigation

| Working on… | Load |
|---|---|
| a marketplace that stopped syncing | `CLAUDE.md` · this README · [credentials.md](credentials.md) · `gaps.md` |
| someone's access | `CLAUDE.md` · this README · [users-roles.md](users-roles.md) · `gaps.md` |
| a SKU's identity | `CLAUDE.md` · this README · [catalog.md](catalog.md) · `gaps.md` |

## Related sections

Every section depends on this one. None of them owns any of it.
