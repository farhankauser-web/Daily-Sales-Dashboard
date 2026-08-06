# Deployment

files: `deploy/{crontab.txt,infinitee-gunicorn.service,nginx-pulse.conf,deploy.sh}`
       `deploy/{DATABASE.md,PRODUCTION_HARDENING.md}` · `infinitee/settings*.py`
verified against: `9038729` · 2026-08-06

How Pulse runs in production, and the difference between there and a laptop.

## Purpose

Every section's documentation ends at "and this runs on a schedule in
production". This is where that sentence is cashed: what runs, where, how it is
released, and which of the five layers a problem is actually in.

It also carries the **highest open gap in the project** — `INFRA-001` — because
scheduled execution is where the most business value is currently being lost.

## The two environments

This distinction is the single most useful thing in this document, and it is a
standing project assumption.

| | Development | Production |
|---|---|---|
| Where | a laptop, not always on | EC2, continuously |
| Database | SQLite, a working copy | PostgreSQL |
| Scheduled jobs | **none, by design** | **all of them, continuously** |
| Server | `runserver` | gunicorn behind nginx |
| Debug | on | off, with strict transport security |

**Stale timestamps, empty tables, missing scheduled runs and jobs that look like
they never ran are expected locally and are never on their own evidence of a
defect.** Only the code can prove one. See
[methodology.md](methodology.md).

## The stack

```
browser ──443──→ TLS ──→ nginx :80 ──proxy──→ gunicorn 127.0.0.1:8000
                            │                      │
                       /static/ from disk    Django · PostgreSQL
```

- **gunicorn** runs as a systemd unit, two workers and two threads, bound to
  localhost only. Two workers is a memory decision on a small instance, not a
  throughput one.
- **nginx** terminates the public side, proxies to gunicorn, serves static files
  from disk, and allows uploads up to a size that matters — several sections
  depend on spreadsheet uploads.
- **Configuration is environment, not code.** The database URL, allowed hosts
  and the security flags all come from the environment, which is what lets the
  same codebase run in both environments unchanged.

## Release

Pull, migrate, restart. The documented sequence is in `CLAUDE.md`; the
essentials are that it is a **git pull on the server**, not a build, and that
**nothing is deployed that is not committed**.

A release that changes only documentation needs neither a migration nor a
restart — worth knowing, because restarting gunicorn drops live connections for
no benefit.

## Scheduled work

Thirty-three scheduled jobs drive almost everything the business sees. They fall
into four groups:

| Group | Cadence | What stops if they do not run |
|---|---|---|
| Hourly metric snapshots | hourly, and a daily finalise | today's figures, the hourly heatmap |
| Amazon and Ads ingestion | every minute to daily | campaign, search-term and settlement data |
| Inventory syncs | twice daily | stock positions and container receipts |
| Walmart pipeline | every 5–15 minutes | **fulfilment of real customer orders** |

**Order matters between some of them**, and that ordering is documented where it
is load-bearing rather than here — the stock refresh runs ten minutes before the
receipt syncs, for instance, so units are never in neither column. See
[inventory/receiving.md](inventory/receiving.md).

## Business rules

1. **Production runs every scheduled job; development runs none.** This is a
   deliberate separation, not a configuration gap.
2. **Secrets live in the environment**, never in the repository. A credential is
   rotated by an administrator, not by a release.
3. **The database is the only stateful thing.** Everything else — the code, the
   static files, the service definitions — is reproducible from the repository.
4. **Security settings are dormant until switched on**, so the hardening changes
   could be merged and pulled long before the cutover, without risk.
5. **Transport security is enabled only once TLS demonstrably works.** Enabling a
   redirect before the certificate is live locks everyone out, including whoever
   would fix it.

## Known gaps

| Gap | | Classification |
|---|---|---|
| `INFRA-001` | the crontab is a macOS template that cannot be installed on EC2; the server's is hand-maintained and has drifted | configuration |
| `INFRA-004` | VAPT follow-ups: dependency bumps and four templates still inlining JSON | missing implementation |
| `INFRA-005` | no SMTP, so password reset does not work | configuration |
| `INFRA-002` | kernel upgrade pending, needs a reboot | configuration |
| `INFRA-003` | transport security max-age still short of its target | configuration |

`INFRA-001` is the **highest-priority gap in the project**. Every section's
documentation assumes its jobs run; where the server's crontab has drifted from
the template, some of them do not, and the symptom appears as stale data in a
section that is working perfectly.

## Observations — not gaps

- **`deploy/crontab.txt` is a macOS file.** Its paths point at a laptop and its
  header explains that macOS cron does not run while the machine sleeps. It is a
  **template and a record of intent**, not the production schedule. Treating it
  as the latter is the mistake `INFRA-001` exists to prevent.
- **The hardening and database cutover guides in `deploy/` are procedures, not
  descriptions.** They record how the cutover was done and remain the reference
  for doing it again.

## Related documents

- [methodology.md](methodology.md) — the five layers, and why local state proves nothing
- [inventory/receiving.md](inventory/receiving.md) — where job ordering is load-bearing
- [walmart/mcf-pipeline.md](walmart/mcf-pipeline.md) — the schedule that fulfils real orders
