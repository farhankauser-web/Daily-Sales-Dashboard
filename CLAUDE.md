# Pulse

Ops dashboard for Infinitee Xclusives — home textiles sold on Amazon
USA / UK / UAE / KSA, plus Walmart via Amazon MCF, plus a B2B arm (Atlas).

Django 5 · Python 3.14 · PostgreSQL in production, SQLite locally.
Live at https://pulse.infinitee.biz (EC2 · nginx → gunicorn · Let's Encrypt).

## Apps and URL prefixes

| prefix | app | owns |
|---|---|---|
| `/dashboard/` | `dashboard` | daily & historical sales, hourly patterns, P&L, COGS, FBA fee drift, campaigns, search terms, Brand Analytics, AI insights |
| `/planning/` | `inventory_planning` | planner, suppliers, purchase orders, allocation workbench, containers, receiving, FBA transfers, region cash flow |
| `/api-config/` | `amazon_api` | SP-API + Ads API clients, credentials UI, the dashboard data endpoint |
| `/walmart/` | `walmart_mcf` | Walmart orders → Amazon MCF fulfilment |
| `/atlas/` | `atlas` | B2B quote-to-cash (the sidebar's "Supply Chain") |
| `/command-center/` | `command_center` | the widget dashboard |
| `/auth/` | `users` | email-based auth, roles, permission flags |

`apps/dashboard/views.py` is 7,590 lines and serves five sidebar sections.
Grep it; do not read it whole. `docs/` says which function to grep for.

## Invariants — these have each caused a real bug

- **Margins are measured on revenue EX-VAT.** Extract with `÷ (1 + rate)`,
  never `− rate%`. UK 20 · SA 15 · AE 5 · USA 0. Revenue displayed stays gross.
  Helper: `apps.dashboard.sync.net_factor(marketplace)`.
- **AWD reports CASES, FBA Inbound reports EACHES.** `sync_awd_receipts`
  converts using the case pack in the payload; `sync_fba_receipts` must not.
- **`User` is email-based — there is no `username` field.** Reading it raises
  `AttributeError` and the page reports "session expired".
- **The packing list is the truth, not what was declared to Amazon.** Variance
  is `packed − received`, never `declared − received`; we always declare at
  least what we pack, so a declared-based figure invents a shortage.
- **Containers are region-scoped; PO balances are region-blind.** FNSKU differs
  per region, the SKU does not.
- **Container FOB is entered in the REGION's currency** and nothing is
  converted. PO rates are in the SUPPLIER's currency. Never mix them.
- **A SKU implies its category, name and FNSKU.** Uploads ask for the SKU only;
  anything derivable is derived. A typed value wins when an older file has one.
- **Amazon's own figure never overwrites a human count.** `received_units` is
  manual, `amazon_received_units` is the API; `counted_units` prefers the human.
- **`CSRF_COOKIE_HTTPONLY` must stay `False`** or every fetch button breaks with
  "unexpected token '<'".
- **A Walmart order is archivable only on UNIT-level coverage, never on Amazon's
  MCF status alone.** `COMPLETEPARTIALLED` is ambiguous — Amazon returns it both
  for "some units are unfulfillable" and for "some units shipped, the rest are
  still processing". Comparing SKU *sets* is also not enough: 3 shipped of 5
  units of one SKU looks "covered". `apps/walmart_mcf/pipeline.py`
  `_order_fully_shipped()` is the single gate for all three archive paths
  (`upload_tracking` ×2, `reconcile`) — treat COMPLETE and the cancel statuses
  as terminal, everything else must pass `_order_units_covered()`. Archiving
  early hides un-shipped units from ops and from Walmart (PO 200015153699282).

## Commands

```bash
python manage.py check
python manage.py migrate
python manage.py runserver
```

Deploy (EC2):
```bash
cd /home/ubuntu/Daily-Sales-Dashboard && git pull origin main \
  && source venv/bin/activate && python manage.py migrate \
  && sudo systemctl restart infinitee-gunicorn
```

Scheduled jobs live in `deploy/crontab.txt` — **a macOS template**. The EC2
crontab is maintained by hand and has drifted; see `INFRA-01` in `docs/gaps.md`.

### Git and machine facts

Established flow is **edit on the Mac → push → pull on EC2**. Nothing is edited
directly on the server.

- Mac working copy: `/Users/farhankauser/Desktop/Usman Agents/infinitee_app(1)`
- Remote: `git@github.com:farhankauser-web/Daily-Sales-Dashboard.git`, branch `main`
- **Auth is SSH** (`~/.ssh/id_ed25519`). There is **no `gh` CLI** and **no
  credential helper** — an HTTPS remote will prompt for a username and can never
  succeed, since GitHub dropped password auth. If a clone prompts, the fix is
  `git remote set-url origin git@github.com:...`, not a token.
- EC2: `ubuntu@13.62.83.159`, project at `/home/ubuntu/Daily-Sales-Dashboard`,
  venv at `venv/`. Activate the venv only when running Python; a plain
  `git pull` does not need it.
- Django settings module is **`infinitee.settings`** (project package is
  `infinitee/`, not `pulse/` — the site is *called* Pulse but the package is not).
  Standalone scripts must set `DJANGO_SETTINGS_MODULE=infinitee.settings` and run
  from the repo root.

**Cowork device-bridge limits.** The bridge can read and write files under the
mounted folders but **cannot delete** them, and `git` run through it leaves a
`.git/index.lock` behind that it cannot clean up. After any bridge-side git
command the user must run `rm -f .git/index.lock` in a real Mac terminal before
`git add`/`commit` will work. Prefer: agent edits files, user runs all git.

## Conventions

- Verify with real data before claiming a fix works. Run the query, show the
  numbers. Several bugs this codebase has had were "obviously fine" on reading.
- **This machine is the development environment, not production.** Code is
  written and tested here, then deployed; the **production server runs every
  scheduled job continuously and this laptop runs none of them**. It is not
  always on. Cron, background workers and automated imports execute only after
  deploy.

  So the following are **expected here and are never, by themselves, evidence of
  a defect**: stale timestamps, missing scheduled executions, empty or partly
  populated tables, absent background processing, jobs that look like they never
  ran. Only the code can prove a defect in any of them.

- **Rank the evidence, and name its source.** Code → business rules →
  production data → local development data. Use the local database to understand
  **structure and to verify that a path works**, not to infer what production
  does. Any conclusion not drawn from code, architecture or documentation is
  provisional until checked against production, and must say so.

- **Separate the five layers** when analysing anything: code implementation ·
  local development state · deployment configuration · scheduled execution ·
  production behaviour. Most apparent defects are one of the middle three.

- **Production verification belongs to implementation, not documentation.**
  While documenting: identify where it is needed, write the exact query, and
  **keep going** — never block a document on production access. When a feature
  is built or a gap is worked: run the query first, update the classification if
  the evidence moves, and close or re-prioritise the gap on what it shows. Each
  section's `gaps.md` carries a **Production verification queue** that is the
  handoff between the two.
- **Absence of data is not a defect.** Before recommending a fix, say why the
  state exists: missing implementation, bug, configuration, missing operational
  process, or legacy data. A code change cannot close a process gap.
- Destructive or irreversible work goes through a dry run first — most
  management commands report by default and only write with `--apply`.
- Test writes against the local DB inside a rolled-back transaction.
- Money and units: state which, and in which currency, in the same sentence.

## Docs

`docs/README.md` is the index. One leaf per sidebar section; the big two
(Inventory, Marketing) are split further. Each leaf carries **How it works
today**, **How it should work**, and a **Gaps** table.

`docs/gaps.md` aggregates every open gap — that is the backlog.

**Touching a file means updating its doc's `verified against` line in the same
commit.** A doc that lies is worse than no doc, because it gets believed.

## God Mode Swarm (agent operating system)

A 25-agent "God Mode Swarm" is installed *around* this application to add a
structured, risk-based delivery pipeline. It does not change app behaviour.

- **Agents (executable/delegatable):** `.claude/agents/godmode_*.md` — 25 Claude
  Code subagents, invoked via the **Task tool**. `godmode_orchestrator` is the
  entry point/router.
- **Skills (detailed personas):** `.claude/skills/godmode_*/SKILL.md` — the deep
  operating instructions behind each agent. (The existing `search-term-dashboard`
  skill is untouched.)
- **Global rules:** `.claude/god_mode/AGENTS.md` (project-init, file-storage, QA
  testing, pipeline, risk-based orchestration) and `god_mode_delegation.md`.
- **Vault / SSOT / Shadow Context:** `_vault/` — `00_Master_Source_Of_Truth.md`
  (living SSOT), `00_Shadow_Context/` (context that survives lost chats), `Tasks/`,
  `Assets/`, `Reports/`, `Master_Development_Log.md`.

**How orchestration works.** The main session acts as `godmode_orchestrator` and
delegates to specialists via the Task tool (subagents don't spawn subagents). It is
**risk-based** — a typo uses one specialist; a schema change uses PM → Architect →
DBA → dev → QA; a production deploy requires the **mandatory** QA, Cybersecurity and
UAT gates before DevOps. Chain: PM (scope) → Architect (design/SSOT) → specialists →
QA → Security → UAT → DevOps → docs/archivist.

**Mandatory rules.** No deployment without `godmode_cybersecurity_agent` clearance;
no schema/API change without `godmode_lead_architect`; agents expose conclusions,
decisions, assumptions, evidence, plans, risks and verification — never private
chain-of-thought.

**Precedence.** These God Mode rules are generic scaffolding. **This file's
invariants, conventions, and safety constraints above are authoritative** and win
on any conflict. QA uses this project's Django test suite (`python manage.py check`
/ `test`), not the source system's QAHub, unless QAHub is explicitly configured.
