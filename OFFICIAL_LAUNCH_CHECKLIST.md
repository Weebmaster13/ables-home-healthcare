# Official Launch Checklist

Use this checklist before the Ables Home Healthcare website collects real client,
caregiver, referral, appointment, or document data.

## 1. Domain

- Buy or confirm the official domain.
- Set `SITE_BASE_URL=https://REPLACE_WITH_FINAL_DOMAIN`.
- Point DNS to the hosting provider.
- Enable HTTPS and force HTTPS.

## 2. Hosting

Production runs on **GoDaddy cPanel (Apache + Phusion Passenger) with
MySQL/MariaDB**. See `DEPLOYMENT.md` (authoritative) and `CPANEL_MYSQL.md`
(step-by-step cPanel runbook). Render/Railway/VPS files are kept for history
only; do not configure more than one platform at a time.

Prepared deployment files:

- `passenger_wsgi.py` (cPanel startup file; entry point `application`)
- `requirements-cpanel.txt` (minimal cPanel runtime set)
- `preflight_cpanel.py` (read-only config/DB/entrypoint check)
- `flask --app app db upgrade` (run once as an explicit deploy step)
- `/healthz` minimal liveness check

## 3. Database (MySQL/MariaDB)

- Create the database and user in cPanel → **MySQL Databases**.
- Set `DATABASE_URL=mysql+pymysql://USER:PASSWORD@HOST:3306/DBNAME`.
- Run `flask --app app db upgrade`.
- Enable scheduled backups (cPanel backup wizard) **and** keep an off-host copy.
- Confirm the engine on the host before relying on backups (see `DEPLOYMENT.md`).

## 4. Email And Forms

Create real email hosting for `admin@ableshomehealthcare.com` using Google
Workspace, Microsoft 365, Zoho Mail, or another provider.

Set:

```text
MAIL_SERVER
MAIL_PORT=587
MAIL_USERNAME
MAIL_PASSWORD
MAIL_USE_TLS=true
MAIL_DEFAULT_SENDER=admin@ableshomehealthcare.com
ADMIN_EMAIL=admin@ableshomehealthcare.com
```

Run:

```text
python test_email.py
```

Then test:

- `/contact`
- `/free-consultation`
- `/referrals`
- `/employment-application`

## 5. Security Settings

Set:

```text
SECRET_KEY=<long random secret>
FLASK_ENV=production
FORCE_HTTPS=true
ACCOUNT_LOCKOUT_ATTEMPTS=5
ACCOUNT_LOCKOUT_MINUTES=15
ADMIN_RECOVERY_CODES_REQUIRED=true
PASSWORD_BREACH_CHECK_ENABLED=true
VIRUS_SCAN_COMMAND=clamdscan --stream
```

`ADMIN_RECOVERY_CODES_REQUIRED` defaults on in production. Keep it true.
`VIRUS_SCAN_COMMAND` must be set so uploads are scanned by a real engine
(the built-in EICAR check is not a substitute).

## 6. Uploads And Backups

- Set `UPLOAD_FOLDER` to persistent home-dir storage (for example `/home/<cpanel-user>/ables-home-healthcare/instance/uploads`), never `/tmp`.
- Confirm uploads survive an app restart and a code update, and that documents cannot be fetched by guessing a URL (authorization is enforced on every download).
- Back up the database with `python backup_db.py` (uses `mysqldump`/`mariadb-dump` when `DATABASE_URL` is MySQL, `pg_dump` for PostgreSQL) and keep a verified copy off-host.
- Keep upload backups separate from database backups, and verify a restore on an isolated scratch database before relying on any backup (see `DEPLOYMENT.md`).
- Review uploaded document privacy and retention rules.

## 7. Legal And Business Review

Do not collect real health/client documents until these are reviewed:

- Privacy policy
- Terms
- Consent language
- Document upload policy
- HIPAA/privacy obligations
- Service scope and licensing language
- Payment options
- Service areas

After review, set:

```text
BUSINESS_DETAILS_CONFIRMED=true
LEGAL_SCOPE_CONFIRMED=true
LEGAL_PRIVACY_REVIEW_CONFIRMED=true
APPROVED_PHOTOS_READY=true
```

## 8. Production Admin

Set these once, then run `python seed_production_admin.py`:

```text
ADMIN_SEED_EMAIL=owner@example.com
ADMIN_SEED_NAME=Office Admin
ADMIN_SEED_PASSWORD=<strong password>
```

The script creates the admin but refuses to silently change the role of an
existing account. If you deliberately need to promote an existing account,
re-run with `ADMIN_SEED_ALLOW_PROMOTE=true` after confirming the email belongs
to the intended person. The password is never printed to stdout.

Log in and change the password after first access.

## 9. Final Checks

Run:

```text
python deploy_check.py --production
python test_email.py
python -m pytest -q
```

Then submit test forms and confirm emails arrive at:

```text
admin@ableshomehealthcare.com
```
