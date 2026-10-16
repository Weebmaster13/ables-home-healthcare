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
> flask --app app db check                                      # driver + schema reachable
> python preflight_cpanel.py                                    # config/DB/migrations/uploads/entrypoint
> ```
>
> The app supports MySQL/MariaDB **and** PostgreSQL: bare `mysql://` URLs are
> normalized to `mysql+pymysql://`, `PyMySQL` is installed, and backups dispatch
> to `mysqldump`/`mariadb-dump` or `pg_dump` accordingly. Use whichever the host
> actually reports.

## One deliberate migration strategy

Migrations run **once, as an explicit deploy step**, never automatically on
app startup:

- `AUTO_MIGRATE_ON_STARTUP` defaults to **false**. Leave it false.
- Run migrations from the deploy pipeline or a shell:

```bash
flask --app app db upgrade
```

This avoids multiple instances racing the same migration at boot.

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
is not a real antivirus scan.

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

Restore test (run periodically, on a scratch database):

```bash
# PostgreSQL
pg_restore --clean --if-exists --dbname "$DATABASE_URL" <extracted>/database.dump

# MySQL/MariaDB
mysql "$DATABASE_URL" < <extracted>/database.sql
```

Retention in `backup_db.py` (`cleanup_old_backups`) only deletes **local**
copies and never the newest one — use it only after off-host copies exist.

### cPanel/MySQL production backup and verified restore

On cPanel the database usually lives on the same host (`localhost`). Do this
**before** any code change, and never restore over the live database.

```bash
# 1. Dump the live database to a file OUTSIDE the web root (read-only on prod)
mysqldump --single-transaction --quick \
  --host "$DB_HOST" --user "$DB_USER" --result-file="$HOME/backups/ables-$(date +%F).sql" "$DB_NAME"
# (password: export MYSQL_PWD=... first, or use a ~/.my.cnf with chmod 600)

# 2. Bundle the uploaded documents too (they are NOT in the database)
tar -czf "$HOME/backups/ables-uploads-$(date +%F).tar.gz" \
  /home/<cpanel-user>/ables-home-healthcare/instance/uploads

# 3. Verify the dump is real (non-empty, has CREATE TABLE for core tables)
grep -c "CREATE TABLE" "$HOME/backups/ables-$(date +%F).sql"
grep -E "CREATE TABLE .*(inquiry|caregiver_application|notification_log)" "$HOME/backups/ables-$(date +%F).sql"

# 4. Store both files OFF the hosting account (owner-controlled, access-restricted).
```

**Restore drill (isolated — never on the live database):**

```bash
# Create a scratch database in cPanel's MySQL Databases screen, then:
mysql --host "$DB_HOST" --user "$DB_USER" -e "CREATE DATABASE ables_restore"
mysql --host "$DB_HOST" --user "$DB_USER" ables_restore < "$HOME/backups/ables-YYYY-MM-DD.sql"

# Compare representative row counts against production
mysql --host "$DB_HOST" --user "$DB_USER" ables_restore -e \
  "SELECT (SELECT COUNT(*) FROM inquiry) inquiries, \
          (SELECT COUNT(*) FROM caregiver_application) applications, \
          (SELECT COUNT(*) FROM notification_log) notifications, \
          (SELECT version_num FROM alembic_version) migration;"

# Point a THROWAWAY copy of the app at ables_restore to confirm it boots and
# serves pages, then drop the scratch database when done.
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
