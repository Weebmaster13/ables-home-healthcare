# Deployment Guide (authoritative)

This is the single source of truth for deploying Ables Home Healthcare.
The other `*_DEPLOY.md` / `DEPLOY_RENDER_FREE.md` files are kept for history
only and are **superseded by this document**.

## Where production actually runs

The live site https://ableshomehealthcare.com runs on a **GoDaddy VPS**
(Ubuntu, Docker + Nginx + PostgreSQL). Earlier drafts described Render and
Railway; those are no longer the production target. Do not configure the app
against more than one platform at a time.

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
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
SITE_BASE_URL=https://ableshomehealthcare.com
UPLOAD_FOLDER=/var/lib/ables/uploads      # persistent, NOT /tmp
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

This writes a `pg_dump` (custom format, uploads bundled) to `backups/` and
verifies the archive. **Copy every backup off-host** to durable storage;
the local `backups/` directory is not a real backup on its own.

Restore test (run periodically, on a scratch database):

```bash
pg_restore --clean --if-exists --dbname "$DATABASE_URL" backups/ables-postgres-<timestamp>.dump
```

Retention in `backup_db.py` (`cleanup_old_backups`) only deletes **local**
copies and never the newest one — use it only after off-host copies exist.

## Launch checklist

```bash
python deploy_check.py --production   # must pass with no TODO items
python test_email.py                  # then submit every public form
tunnel/submit each of: /contact, /free-consultation, /referrals,
/employment-application and confirm each arrives in an owner inbox
```

Then verify HTTPS, DNS, migrations applied, uploads writable, and that
`/healthz` returns only `{"status": "ok"}`.
