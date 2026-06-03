# Railway Deploy Notes

Use this if the site is deployed on Railway instead of Render.

## Services

Create one Railway project with:

- A web service from this repository
- A PostgreSQL service

Railway will provide `DATABASE_URL` from the PostgreSQL service. The Flask app
already reads `DATABASE_URL`.

## Build And Start

Railway should install from:

```text
requirements.txt
```

Use this start command if Railway does not detect it automatically:

```text
gunicorn wsgi:app
```

Run migrations after deploy:

```text
flask --app app db upgrade
```

## Required Variables

Set these in the web service variables:

```text
SECRET_KEY=<long random secret>
FLASK_ENV=production
FORCE_HTTPS=true
SITE_BASE_URL=https://REPLACE_WITH_FINAL_DOMAIN
DATABASE_URL=<provided by Railway PostgreSQL>
UPLOAD_FOLDER=/app/instance/uploads
MAX_CONTENT_LENGTH=5242880
SESSION_HOURS=8
WTF_CSRF_TIME_LIMIT=3600
ACCOUNT_LOCKOUT_ATTEMPTS=5
ACCOUNT_LOCKOUT_MINUTES=15
ADMIN_RECOVERY_CODES_REQUIRED=false
ADMIN_EMAIL=admin@ableshomehealthcare.com
MAIL_SERVER=<real SMTP host>
MAIL_PORT=587
MAIL_USERNAME=<real SMTP username>
MAIL_PASSWORD=<real SMTP app password>
MAIL_USE_TLS=true
MAIL_DEFAULT_SENDER=admin@ableshomehealthcare.com
ENABLE_SMS_REMINDERS=false
BUSINESS_DETAILS_CONFIRMED=false
LEGAL_SCOPE_CONFIRMED=false
LEGAL_PRIVACY_REVIEW_CONFIRMED=false
APPROVED_PHOTOS_READY=false
```

## After Deploy

Run:

```text
python deploy_check.py --production
python seed_production_admin.py
python test_email.py
```

Do not collect real client health or document data until PostgreSQL, SMTP,
HTTPS, backups, and legal/privacy review are complete.
