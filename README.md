# Ables Home Healthcare Website

Flask website and portal for Ables Home Healthcare with public pages, referrals,
contact inquiries, client appointment requests, caregiver schedules, and admin tools.

The public site is positioned as Maryland Residential Service Agency-style,
non-medical home care. Do not add skilled nursing, therapy, wound care, or other
clinical claims unless the business confirms licensing and service scope.

## Current Website Features

- Public company website with service, family guide, provider partner, FAQ, resource, testimonial, and local SEO pages
- Free consultation intake with service address, start date, schedule, safety notes, emergency contact, language preference, and consent
- Referral form and downloadable referral form
- Client portal care requests with expanded intake details
- Admin follow-up statuses from new request through care plan draft, caregiver assignment, and closeout
- Admin global search across users, inquiries, referrals, applications, care plans, and appointments
- Admin work queue for open inquiries, referrals, care plans, appointments, and caregiver applications
- Admin care plan builder for service type, schedule, tasks, risks, contacts, caregiver preferences, and notes
- Caregiver application system with availability, certifications, references, background-check consent, and resume/certification upload
- Caregiver dashboard and visit notes
- Sitemap, robots file, canonical tags, Open Graph metadata, and local SEO pages
- Production readiness checker, SMTP test script, security headers, and launch handoff documentation
- Account lockouts, stronger password rules, secure session settings, HTTPS enforcement option, and admin security monitor

## Local Setup

```powershell
cd C:\Users\nzomo\Code\AbleHomeHealth\able-home-health-care\able-home-health-care
python -m pip install -r requirements.txt
python migrate_db.py
python init_db.py
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

The Codex demo server usually runs on:

```text
http://127.0.0.1:5050
```

## Official Launch Path

Use Render or Railway with PostgreSQL. Do not launch with the local SQLite demo
database for real client, caregiver, or document data.

Prepared deployment files:

- `Procfile` starts the app with `gunicorn wsgi:app`
- `render.yaml` is legacy (Render is no longer the production target); see `DEPLOYMENT.md`
- `runtime.txt` and `.python-version` pin Python 3.12.10
- `.env.production.example` lists production environment variables
- `RAILWAY_DEPLOY.md` covers Railway-specific setup
- `DEPLOYMENT.md` is the current production deployment guide (Oracle VPS / Docker)

Render start command:

```text
gunicorn wsgi:app
```

Render pre-deploy command:

```text
flask --app app db upgrade
```

After deployment, seed the first production admin:

```powershell
$env:ADMIN_SEED_EMAIL="owner@example.com"
$env:ADMIN_SEED_NAME="Office Admin"
$env:ADMIN_SEED_PASSWORD="replace-with-strong-password"
python seed_production_admin.py
```

## Environment Variables

Copy `.env.example` into the hosting provider's environment settings and set:

```text
SECRET_KEY
FLASK_ENV=production
SITE_BASE_URL
DATABASE_URL
ADMIN_EMAIL
MAIL_SERVER
MAIL_PORT
MAIL_USERNAME
MAIL_PASSWORD
MAIL_USE_TLS
MAIL_DEFAULT_SENDER
ENABLE_SMS_REMINDERS
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_FROM_NUMBER
SESSION_HOURS
WTF_CSRF_TIME_LIMIT
FORCE_HTTPS
ACCOUNT_LOCKOUT_ATTEMPTS
ACCOUNT_LOCKOUT_MINUTES
BUSINESS_DETAILS_CONFIRMED
LEGAL_SCOPE_CONFIRMED
APPROVED_PHOTOS_READY
```

Use `.env.production.example` as the production starting point. Do not commit or
share real secrets. Set the real values in the hosting provider dashboard.

Run the production checker before launch:

```powershell
python deploy_check.py --production
python test_email.py
```

## Database Migrations

The app now includes Flask-Migrate/Alembic. For production schema changes, prefer:

```powershell
flask --app app db migrate -m "describe the schema change"
flask --app app db upgrade
```

The older `migrate_db.py` helper is kept for the local SQLite demo database.

## Automated Tests

Run the security and workflow smoke tests with:

```powershell
python -m pytest
```

## Admin Security Tools

Admins can open `/admin/security` and generate one-time recovery codes. After
codes exist for an admin account, they can be required at login by setting
`ADMIN_RECOVERY_CODES_REQUIRED=true`. Each code is stored hashed and can be used
once. The current local/demo setting is `ADMIN_RECOVERY_CODES_REQUIRED=false`,
so admins can log in with email and password only while testing.

Admins can open `/admin/audit` to filter audit events by action, user, entity,
and date range, then export the filtered CSV.

Email and SMS delivery attempts are logged in `/admin/reports`, including sent,
skipped, and failed attempts.

Additional admin operations pages:

- `/admin/system-status` shows health, SMTP, PostgreSQL, scanner, backup, and failure status.
- `/admin/maintenance` shows deploy signals, migration version, backup actions, and log file size.
- `/admin/logs` shows recent application log lines safely.
- `/admin/retention` cleans old notification logs, closed messages, audit logs, or old backups.
- `/admin/notifications` filters email/SMS logs by channel, status, and date.
- `/admin/caregiver-conflicts` shows double-booked caregivers, unassigned upcoming visits, and completed visits missing notes.

To seed a production admin after setting environment variables:

```powershell
$env:ADMIN_SEED_EMAIL="owner@example.com"
$env:ADMIN_SEED_PASSWORD="Use-A-Long-Temporary-Password-2026!"
python seed_production_admin.py
```

See `rotate_secret_key_notes.md` before rotating `SECRET_KEY`.

To check the production env file specifically:

```powershell
$env:ENV_FILE=".env.production"
python production_check.py
Remove-Item Env:ENV_FILE
```

Uploaded caregiver application files are stored in `UPLOAD_FOLDER`. Keep uploads
private and outside static/public folders in production.
Portal documents and caregiver resumes are saved with randomized private
filenames. The original filename is kept only for display and download labels.
Uploads are checked by the built-in safety scan before saving. To add a real
external scanner, set `VIRUS_SCAN_COMMAND` to the scanner command supplied by
the host or security vendor.
Uploads are also checked by extension and expected MIME type, and upload forms
display the configured maximum upload size.

## Production Database

SQLite is acceptable for a local demo. For a real company, use PostgreSQL:

```text
DATABASE_URL=postgresql://REPLACE_USER:REPLACE_PASSWORD@REPLACE_HOST:5432/REPLACE_DATABASE
```

Use a migration tool such as Flask-Migrate/Alembic before production schema changes.
For Render, Railway, Heroku-style hosts, or managed PostgreSQL providers, copy
the provider's exact internal PostgreSQL URL into `DATABASE_URL`.

## Email Sending

Contact and referral forms save to the database. Email notifications are sent only
when SMTP variables are configured.

Common options:

- Google Workspace SMTP
- Microsoft 365 SMTP
- SendGrid
- Mailgun
- Postmark

Minimum SMTP setup:

```text
MAIL_SERVER=REPLACE_SMTP_HOST
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=REPLACE_SMTP_USERNAME
MAIL_PASSWORD=REPLACE_SMTP_PASSWORD_OR_APP_PASSWORD
MAIL_DEFAULT_SENDER=admin@ableshomehealthcare.com
ADMIN_EMAIL=admin@ableshomehealthcare.com
```

The production checker treats placeholder SMTP values as not ready. Use an app
password or transactional email API SMTP password from the provider; do not use a
personal email password.

Optional SMS appointment reminders use Twilio-compatible credentials:

```text
ENABLE_SMS_REMINDERS=true
TWILIO_ACCOUNT_SID=replace-with-account-sid
TWILIO_AUTH_TOKEN=replace-with-auth-token
TWILIO_FROM_NUMBER=+13013262225
```

After configuring SMTP, submit `/contact`, `/free-consultation`, and `/referrals`
test forms and confirm the admin inbox receives each message.

You can also run:

```powershell
python test_email.py
```

## SEO And Public Discovery

The site now generates:

```text
/robots.txt
/sitemap.xml
```

Set `SITE_BASE_URL` to the final HTTPS domain before production. The sitemap,
canonical tags, robots file, and social preview URLs use that value.

After launch:

- Submit `/sitemap.xml` in Google Search Console
- Add the final domain to Bing Webmaster Tools
- Create or update the Google Business Profile
- Make sure service areas, phone, address, and business name match everywhere
- Add real photos and verified reviews when approved by the business

## Backups

Manual backup:

```powershell
python backup_db.py
```

Admins can use `/admin/backups` or `/admin/maintenance` to create a local full
backup, delete a single backup, and clean older local backups while keeping the latest 10.

For PostgreSQL production databases, enable automated backups in the host dashboard.
Manual PostgreSQL backup, when `pg_dump` is installed and `DATABASE_URL` points to PostgreSQL:

```powershell
python postgres_backup.py
```

Production should schedule encrypted backups at least daily and keep off-server copies.
For regulated or sensitive data, encrypt backups using the hosting provider's
managed backup encryption or a vetted encryption tool before storing off-server.

### Off-site backup and recovery

A local backup in `backups/` protects against a corrupted table, not against
losing the production host. Copy each finished backup to a destination that
survives losing that host, and verify the copy before you rely on it.

1. Choose a destination. An `sftp://` host is off-site (a second host, a NAS, or
   a storage account that accepts SFTP). A directory on the same hosting account
   is *not* off-site — it is lost with the host — so the readiness check treats
   it as not ready.
2. Create a dedicated SSH key pair for backups. Put the private key where only
   the backup job can read it, and set `OFFSITE_BACKUP_KEY` to its path. Add the
   public key to the destination's `authorized_keys`. Passwords in an `sftp://`
   URL are refused rather than used, so a credential never appears in a command
   line or a log.
3. Pin the destination's host key in a known-hosts file and set
   `OFFSITE_SFTP_KNOWN_HOSTS` to it, so the transfer is encrypted *and* the
   destination is authenticated.
4. Configure `OFFSITE_BACKUP_URL`, for example
   `sftp://backups@backup-host/home/backups/ables`.
5. Run the backup and the transfer together:

   ```bash
   python backup_db.py --offsite
   ```

   The command copies the database artifact, its authenticated fingerprint, and
   a non-secret manifest, then downloads the remote copy and compares its
   SHA-256 with the local file. It exits non-zero unless the remote copy is
   verified, so a partial or corrupted transfer is never reported as a
   successful backup. `--offsite-verify=size` is a weaker size-only check.

6. Confirm off-site protection on `/admin/launch-checklist`, which reports ready
   only after a verified upload — a configured destination alone is not enough.

To restore from off-site, list the destination and restore a named set using
only the off-site copy. The recorded fingerprint still authenticates the
content, and the restore refuses the live `DATABASE_URL`:

```bash
python backup_db.py --offsite-list
python backup_db.py --restore-from-offsite ablehome-postgres-YYYYMMDD-HHMMSS.dump \
    --restore-to "$SCRATCH_DATABASE_URL"
```

`--offsite-list` is read-only and proves the destination is reachable with the
configured credentials. `--restore-from-offsite` downloads that set, checks its
fingerprint, and restores it into an isolated scratch database, so a full
recovery can be demonstrated without the local backup file. (To fetch a copy by
hand instead, `python -c "import offsite_backup;
print(offsite_backup.download_backup('NAME'))"` followed by the normal
`--restore-to` drill still works.)

### Preserving the fingerprint signing key

Backup fingerprints are HMAC-signed with `BACKUP_FINGERPRINT_KEY` (falling back
to a domain-separated derivation of `SECRET_KEY`). The key proves a fingerprint
was not altered, so a backup can only be content-verified while that key is
still available.

- Set `BACKUP_FINGERPRINT_KEY` to a long random value and keep it stable.
- Store it in a password manager or secret store, separate from the backups, and
  record its non-secret ID so you can confirm you still hold the right key:

  ```bash
  python backup_db.py --print-key-id
  ```

  The command prints a short ID, never the key. The same ID after a restore
  means the key matches and the fingerprints will authenticate.
- Do not rotate `BACKUP_FINGERPRINT_KEY` (or `SECRET_KEY` with no dedicated key)
  without re-fingerprinting backups; rotating it makes older backups
  unverifiable. See `rotate_secret_key_notes.md`.

## Monitoring

Basic local health check:

```powershell
python monitor_health.py http://127.0.0.1:5050/
```

Production should use external monitoring such as UptimeRobot, Better Stack,
Render health checks, or cloud provider alerts.

## Deployment

Recommended beginner-friendly hosts:

- Render
- Railway
- PythonAnywhere
- DigitalOcean App Platform

Use:

```text
Procfile: web: gunicorn wsgi:app
```

Production checklist:

- Set `SECRET_KEY`
- Use PostgreSQL
- Configure SMTP
- Enable HTTPS/SSL
- Connect real domain
- Change admin password
- Remove test data
- Run mobile QA
- Verify all forms
- Schedule backups
- Add uptime monitoring
- Replace placeholder logo
- Replace placeholder/stock imagery with licensed assets
- Confirm testimonial permissions
- Confirm all business details with the agency

The website also includes `/launch-readiness`, which summarizes remaining
production, legal, asset, email, database, and business-confirmation tasks.

See `LAUNCH_HANDOFF.md` for the domain, email, database, legal, photo, and
Google Business Profile tasks that require account access.

## Domain And SSL

These cannot be completed from code alone. You must control the domain registrar
or DNS provider. Create DNS records pointing the domain/subdomain to the hosting
provider, then enable HTTPS/SSL in the host dashboard.

## Legal And HIPAA Review

The privacy and terms pages are starter copy. Before collecting real client,
caregiver, referral, or health-related information, have the site reviewed by a
qualified healthcare compliance professional or attorney.

Review topics:

- HIPAA applicability
- Maryland home care requirements
- Consent language
- Business associate agreements
- Data retention
- Secure communications
- Staff access procedures

## Licensed Images And Branding

Replace all generated/development imagery before sale or launch. Use:

- Real company photos with signed permission
- Licensed stock images
- Purchased healthcare image assets
- A final company logo

The current logo and generated care photos are development assets, not final
brand identity or legally reviewed marketing photography.

## Mobile QA Checklist

Check these widths before launch:

- 390px phone
- 768px tablet
- 1024px small laptop
- 1440px desktop

Confirm:

- Sidebar opens and closes without covering required content
- Hero text remains readable
- Buttons wrap cleanly
- Forms fit without horizontal scrolling
- Tables scroll inside their cards
- Footer language selector and compliance copy remain readable

## Portal Workflow Roadmap

The portal now has appointment status, caregiver notes, care profile fields,
expanded intake, caregiver applications, admin care plans, and task prompts. A
production buyer may still expect:

- User activity timeline on admin user profiles
- Caregiver onboarding checklist
- Urgent-language flagging for care requests in the admin work queue
- Care plan version history
- Family update feed
- Visit task completion stored in the database
- Medication reminder schedule
- Emergency contact alert workflow


