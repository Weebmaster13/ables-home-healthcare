# Official Launch Checklist

Use this checklist before the Ables Home Healthcare website collects real client,
caregiver, referral, appointment, or document data.

## 1. Domain

- Buy or confirm the official domain.
- Set `SITE_BASE_URL=https://REPLACE_WITH_FINAL_DOMAIN`.
- Point DNS to the hosting provider.
- Enable HTTPS and force HTTPS.

## 2. Hosting

Recommended: Render or Railway.

Render is prepared with:

- `render.yaml`
- `runtime.txt`
- `Procfile`
- `gunicorn wsgi:app`
- `flask --app app db upgrade`
- `/healthz` health check

## 3. PostgreSQL

- Create managed PostgreSQL on the host.
- Set `DATABASE_URL`.
- Run `flask --app app db upgrade`.
- Enable automated PostgreSQL backups.

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
ADMIN_RECOVERY_CODES_REQUIRED=false
```

After launch, consider setting `ADMIN_RECOVERY_CODES_REQUIRED=true`.

## 6. Uploads And Backups

- Use the host disk only for a small first launch.
- Long term, move uploads to cloud storage such as S3, Backblaze B2, DigitalOcean Spaces, or Cloudinary.
- Keep upload backups separate from database backups.
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
