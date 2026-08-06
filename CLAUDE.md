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

## Conventions

- Verify with real data before claiming a fix works. Run the query, show the
  numbers. Several bugs this codebase has had were "obviously fine" on reading.
- **Rank the evidence, and name its source.** Code → business rules →
  production data → the local snapshot. `db.sqlite3` is a **development
  snapshot, not the business truth**: whole feature paths have never run against
  it, so an empty table means "never used locally", not "broken". Any conclusion
  not drawn from code, architecture or documentation is provisional until
  checked against production, and must say so.
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
