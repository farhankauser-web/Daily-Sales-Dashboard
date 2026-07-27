# Database configuration & SQLite → Postgres cutover

The app reads its database from the **`DATABASE_URL`** environment variable
(parsed in `infinitee/settings.py`):

| Environment | `DATABASE_URL`                                                   | Engine             |
|-------------|-----------------------------------------------------------------|--------------------|
| Local dev   | *(unset)*                                                       | SQLite `db.sqlite3`|
| EC2 / prod  | `postgres://user:pass@host:5432/infinitee?sslmode=require`      | PostgreSQL         |

Nothing else in the code changes between the two — the Django ORM, all
migrations, and every `JSONField` are database-agnostic.

Optional: `DB_CONN_MAX_AGE` (seconds a connection is kept open for reuse;
default `60`). Query params on the DSN (e.g. `?sslmode=require`) are passed
straight through as `OPTIONS`.

---

## Why Postgres in production

The pipeline runs many overlapping cron writers (every-minute AMS ingest,
every-5-min Walmart import, hourly snapshots, per-region loops). SQLite locks
the **whole database file** on every write, which is the source of the
`database is locked` retries. Postgres does row-level locking and handles
concurrent writers cleanly.

Keep SQLite locally — it's zero-config and fast for dev and the test suite.

---

## One-time cutover on EC2

Assumes Postgres 14+ is reachable. Run from the project root inside the venv.

### 1. Provision the database
```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE infinitee;
CREATE USER infinitee_user WITH PASSWORD 'CHANGE-ME-strong-password';
ALTER ROLE infinitee_user SET client_encoding TO 'utf8';
ALTER ROLE infinitee_user SET default_transaction_isolation TO 'read committed';
GRANT ALL PRIVILEGES ON DATABASE infinitee TO infinitee_user;
SQL
```

### 2. Install the driver
```bash
pip install -r requirements.txt      # includes psycopg2-binary
```

### 3. (Optional) export existing SQLite data
Only if you want to carry local/EC2 SQLite data forward. Skip to start clean.
```bash
# with DATABASE_URL still UNSET (reads SQLite):
python manage.py dumpdata --natural-foreign --natural-primary \
  -e contenttypes -e auth.permission -e admin.logentry -e sessions.session \
  --indent 2 > /tmp/dump.json
```

### 4. Point the app at Postgres
Add to the EC2 `.env` (never commit it):
```
DATABASE_URL=postgres://infinitee_user:CHANGE-ME-strong-password@127.0.0.1:5432/infinitee
```

### 5. Build the schema
```bash
python manage.py migrate
```

### 6. (Optional) load the exported data
```bash
python manage.py loaddata /tmp/dump.json
```
If load order errors appear, re-run — natural keys usually resolve on a second
pass. Then reset Postgres sequences:
```bash
python manage.py sqlsequencereset $(python manage.py showmigrations --list \
  | awk '/^ [a-z]/{print $1}' | sort -u) | python manage.py dbshell
```

### 7. Recreate the admin user (if you started clean)
```bash
python manage.py createsuperuser
```

### 8. Verify
```bash
python manage.py check
python manage.py showmigrations | grep -c '\[X\]'
```

### Rollback
Unset `DATABASE_URL` (or comment it in `.env`) and the app is back on the
SQLite file instantly — nothing was deleted.

---

## Notes
- The `job_lock` guards and SQLite lock-retry code in the pipeline are harmless
  on Postgres; they can be simplified later but don't need to change for the
  cutover.
- Back up Postgres with `pg_dump` on a schedule once you're live.
