# Deployment Guide (authoritative)

This is the single source of truth for deploying Ables Home Healthcare.
The other `*_DEPLOY.md` / `DEPLOY_RENDER_FREE.md` files are kept for history
only and are **superseded by this document**.

## Where production actually runs

**Confirmed from repository + live evidence (read-only):** the live site
https://ableshomehealthcare.com is served by **GoDaddy cPanel shared hosting
with Apache + Phusion Passenger**, backed by **MySQL/MariaDB**, using the
`passenger_wsgi.py` entry point (`application`) and the minimal
`requirements-cpanel.txt`. The full runbook is **[CPANEL_MYSQL.md](CPANEL_MYSQL.md)**.

Evidence: the domain resolves to a GoDaddy IP; the live `/healthz` reports
`database_backend: "other"` (neither PostgreSQL nor SQLite — i.e. MySQL on the
pre-fix code); and the `cpanel-mysql-port` branch documents the exact cPanel
Python-app setup. A separate, **superseded** draft ([GODADDY_VPS_DEPLOY.md](GODADDY_VPS_DEPLOY.md))
proposed a self-managed VPS with Docker + PostgreSQL; that is **not** what is
running and should not be followed without an owner decision.

Do not configure the app against more than one platform at a time.

> **Before trusting any backup or cutover, confirm the backend on the real
> host** (read-only; never paste credentials):
>
> ```bash
> echo "$DATABASE_URL" | sed -E 's#(://[^:]+:)[^@]+@#\1***@#'   # scheme only
> flask --app app db current                                   # connects to the DB; prints the applied revision (exit 0 = reachable)
> flask --app app db check                                     # are the models in sync with the migration head?
> python preflight_cpanel.py                                    # config/DB/migrations/upload folder/entrypoint (read-only)
> python preflight_cpanel.py --write-test                       # opt-in: also prove the upload folder accepts a write
> ```
>
> `db current` connects and prints the applied revision, so it fails loudly on an
> unreachable database. `db check` answers a different question — it reports
> whether the models have drifted from the migration head and exits non-zero with
> "Target database is not up to date" when migrations have not been applied yet,
> which is expected on a fresh database. Do not read a `db check` failure as a
> connectivity problem.
>
> The app supports MySQL/MariaDB **and** PostgreSQL: bare `mysql://` URLs are
> normalized to `mysql+pymysql://`, `PyMySQL` is installed, and backups dispatch
> to `mysqldump`/`mariadb-dump` or `pg_dump` accordingly. Use whichever the host
> actually reports.

## One deliberate migration strategy

Migrations run **once, as an explicit deploy step**, never automatically on
app startup:

- `AUTO_MIGRATE_ON_STARTUP` defaults to **false**. Leave it false.
- `LOG_TO_FILE` defaults to **true** (stream logging is always on). Set it to
  `false` on ephemeral hosts, or when running the read-only preflight, so no
  local `instance/logs/app.log` is created.
- Run migrations from the deploy pipeline or a shell:

```bash
flask --app app db upgrade
```

This avoids multiple instances racing the same migration at boot.

## Production startup guards (fail fast)

With `FLASK_ENV=production`, the app refuses to boot rather than run insecurely:

- **No `DATABASE_URL`** → `RuntimeError` (never silently falls back to local SQLite).
- **Missing, short, or development `SECRET_KEY`** → `RuntimeError` (sessions
  must not be signed with a known key).

Set both correctly before deploying; `python preflight_cpanel.py` reports the
same conditions as blocking issues.

## Required environment variables

```text
FLASK_ENV=production
SECRET_KEY=<long random value>
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME   # or mysql+pymysql://USER:PASSWORD@HOST:3306/DBNAME
SITE_BASE_URL=https://ableshomehealthcare.com
UPLOAD_FOLDER=/home/<cpanel-user>/ables-home-healthcare/instance/uploads   # persistent disk, NEVER /tmp
FORCE_HTTPS=true
SESSION_HOURS=8
MAX_CONTENT_LENGTH=5242880
ACCOUNT_LOCKOUT_ATTEMPTS=5
ACCOUNT_LOCKOUT_MINUTES=15
ADMIN_RECOVERY_CODES_REQUIRED=true
PASSWORD_BREACH_CHECK_ENABLED=true       # Have I Been Pwned check; fails open on network errors
VIRUS_SCAN_COMMAND=clamdscan --stream     # required so uploads are really scanned
MAIL_SERVER=<smtp host>
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=<smtp user>
MAIL_PASSWORD=<smtp password>
MAIL_DEFAULT_SENDER=admin@ableshomehealthcare.com
ADMIN_EMAIL=admin@ableshomehealthcare.com
```

Set the business/legal confirmations only after owner and legal review:

```text
BUSINESS_DETAILS_CONFIRMED=true
LEGAL_SCOPE_CONFIRMED=true
LEGAL_PRIVACY_REVIEW_CONFIRMED=true
APPROVED_PHOTOS_READY=true
```

## Persistent, private storage

Client documents must survive restarts and must not be publicly served.
Mount a durable volume at `UPLOAD_FOLDER` (for example
`/var/lib/ables/uploads`) or use object storage. **`/tmp` is wiped on restart
and redeploy — never use it in production.**

## Upload antivirus

`security.scan_upload_stream()` reads the **whole file** and streams it to
`VIRUS_SCAN_COMMAND` on stdin. Install ClamAV on the host and set:

```text
VIRUS_SCAN_COMMAND=clamdscan --stream
```

With no scanner configured, uploads only get the EICAR signature check, which
is not a real antivirus scan. Because of that, **production fails closed**:
when `VIRUS_SCAN_COMMAND` is empty, uploads are rejected instead of being
stored unscanned. To accept unscanned uploads anyway (a documented risk the
owner must approve), set:

```text
UPLOAD_SCAN_FAIL_OPEN=true
```

In development, uploads are still accepted without a scanner.

## Backups and restore

Create a backup and verify it is readable:

```bash
python backup_db.py
```

The tool dispatches on `DATABASE_URL`:

- **PostgreSQL** → `pg_dump` (custom format), bundled as
  `ablehome-full-<timestamp>.zip` containing `database.dump`.
- **MySQL/MariaDB** → `mysqldump`/`mariadb-dump`, bundled as
  `ablehome-mysql-<timestamp>.zip` containing `database.sql`.
- **SQLite** (local dev only) → copies the SQLite file.

A configured server database is **never** silently backed up as local SQLite:
if the matching dump tool is missing or the dump fails, the command exits
non-zero (the admin screen shows an error instead of a misleading success).
Install the matching tool on the host (`pg_dump` or `mysqldump`).

**Copy every backup off-host** to durable storage; the local `backups/`
directory is not a real backup on its own.

Restore test (run periodically, on a scratch database). The client tools do
**not** understand the SQLAlchemy driver suffix, so use `backup_db.client_url()`
— a `mysql+pymysql://` URL passed straight to `mysql` fails to parse:

```bash
# PostgreSQL (pg_restore does understand the postgresql:// scheme)
pg_restore --clean --if-exists --dbname "$DATABASE_URL" <extracted>/database.dump

# MySQL/MariaDB: let the app rewrite the URL for the native client
CLIENT_URL=$(python -c "import backup_db; print(backup_db.client_url('$DATABASE_URL'))")
mysql "$CLIENT_URL" < <extracted>/database.sql
```

Or run the whole drill (backup + verified restore into a scratch database) in
one guarded command — it refuses to target the live `DATABASE_URL`:

```bash
python backup_db.py --restore-to "$SCRATCH_DATABASE_URL"
```

Retention in `backup_db.py` (`cleanup_old_backups`) only deletes **local**
copies and never the newest one — use it only after off-host copies exist.

### cPanel/MySQL production backup and verified restore

On cPanel the database usually lives on the same host (`localhost`). Do this
**before** any code change, and never restore over the live database.

**Authentication — pick one method and use it for every command below.** The
MySQL client reads `MYSQL_PWD` from the environment, so the password never
appears in the process list or in your shell history:

```bash
# Option A (recommended): an owner-only defaults file, chmod 600
cat > "$HOME/.my.cnf" <<'EOF'
[client]
host=localhost
user=CPANEL_DB_USER
password=CPANEL_DB_PASSWORD
EOF
chmod 600 "$HOME/.my.cnf"

# Option B: export the password for the current shell only
export MYSQL_PWD='CPANEL_DB_PASSWORD'      # do NOT put this in a script or a file
```

```bash
# 1. Dump the live database to a file OUTSIDE the web root (read-only on prod).
#    With ~/.my.cnf in place no password flag is needed.
mysqldump --single-transaction --quick \
  --result-file="$HOME/backups/ables-$(date +%F).sql" "$DB_NAME"

# 2. Bundle the uploaded documents too (they are NOT in the database)
tar -czf "$HOME/backups/ables-uploads-$(date +%F).tar.gz" \
  /home/<cpanel-user>/ables-home-healthcare/instance/uploads

# 3. Verify the dump is real (non-empty, has CREATE TABLE for core tables)
grep -c "CREATE TABLE" "$HOME/backups/ables-$(date +%F).sql"
grep -E "CREATE TABLE .*(inquiry|caregiver_application|notification_log)" "$HOME/backups/ables-$(date +%F).sql"

# 4. Store both files OFF the hosting account (owner-controlled, access-restricted).
```

**Restore drill (isolated — never on the live database):**

Create the scratch database and grant the user **through cPanel's MySQL
Databases screen first** (Add Database → Add User To Database). cPanel
provisions the `cpaneluser_` prefix and grants the privileges; a client-side
`CREATE DATABASE` usually fails because the account user is not granted the
`CREATE` privilege. Record the exact name cPanel assigns, e.g.
`cpaneluser_ables_restore`.

```bash
# SCRATCH_DB is the name cPanel created and granted, e.g. cpaneluser_ables_restore.
# Uses the same authentication method chosen above (no password on the command line).
SCRATCH_DB=cpaneluser_ables_restore

# Restore into the EXISTING scratch database (no CREATE DATABASE here).
mysql "$SCRATCH_DB" < "$HOME/backups/ables-YYYY-MM-DD.sql"

# Compare representative row counts against production
mysql "$SCRATCH_DB" -e \
  "SELECT (SELECT COUNT(*) FROM inquiry) inquiries, \
          (SELECT COUNT(*) FROM caregiver_application) applications, \
          (SELECT COUNT(*) FROM notification_log) notifications, \
          (SELECT version_num FROM alembic_version) migration;"

# Point a THROWAWAY copy of the app at the scratch database to confirm it boots
# and serves pages, then drop the scratch database when done.
```

The guarded helper does the backup-and-restore half of this in one command and
refuses to target the live database:

```bash
# DATABASE_URL must be the LIVE database (it is the backup source);
# --restore-to must be the cPanel-created scratch database.
python backup_db.py --restore-to "mysql+pymysql://USER:PASS@localhost/$SCRATCH_DB"
```

A backup is only "verified" once a restore has been performed and the row
counts / migration head / uploads match. If cPanel permissions or missing
client tools block the restore drill, record that limitation explicitly rather
than calling the backup verified.

## Launch checklist

```bash
python deploy_check.py --production   # must pass with no TODO items
python test_email.py                  # then submit every public form
tunnel/submit each of: /contact, /free-consultation, /referrals,
/employment-application and confirm each arrives in an owner inbox
```

Then verify HTTPS, DNS, migrations applied, uploads writable, and that
`/healthz` returns only `{"status": "ok"}`.
