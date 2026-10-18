# Launch Handoff

This file covers the remaining launch tasks that require business-owner or account access.

## Domain

Set the final production domain in hosting:

```text
SITE_BASE_URL=https://REPLACE_WITH_FINAL_DOMAIN
```

Then point DNS to the hosting provider and enable HTTPS.

## Email

Choose the office email provider, then set SMTP variables:

```text
MAIL_SERVER=REPLACE_SMTP_HOST
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=REPLACE_SMTP_USERNAME
MAIL_PASSWORD=REPLACE_SMTP_PASSWORD_OR_APP_PASSWORD
MAIL_DEFAULT_SENDER=admin@ableshomehealthcare.com
ADMIN_EMAIL=admin@ableshomehealthcare.com
```

Optional SMS reminders:

```text
ENABLE_SMS_REMINDERS=true
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+13013262225
```

After setup:

```powershell
python test_email.py
```

Then submit test forms at:

- `/contact`
- `/free-consultation`
- `/referrals`
- `/employment-application`

## Production Database

Production runs on GoDaddy cPanel with **MySQL/MariaDB**. Create the database
and user in cPanel → MySQL Databases, then set:

```text
DATABASE_URL=mysql+pymysql://REPLACE_USER:REPLACE_PASSWORD@localhost:3306/REPLACE_DATABASE
```

(PostgreSQL is also supported if the owner chooses a different host:
`DATABASE_URL=postgresql://REPLACE_USER:REPLACE_PASSWORD@REPLACE_HOST:5432/REPLACE_DATABASE`.)

Confirm the live engine on the host before trusting any backup, then run the
read-only preflight and migrations:

```bash
python preflight_cpanel.py            # config/DB/migrations/upload folder/entrypoint, no writes
python preflight_cpanel.py --write-test  # opt-in: also prove the upload folder accepts writes
flask --app app db upgrade            # run once, as an approved step
python deploy_check.py --production
```

For backups: `python backup_db.py` dispatches on `DATABASE_URL` (`mysqldump`/
`mariadb-dump` for MySQL, `pg_dump` for PostgreSQL) and never silently copies a
local SQLite file for a server database. Keep a verified copy off-host, and
verify a restore on an isolated scratch database (see `DEPLOYMENT.md`).

## Business And Legal Review

Confirm these before launch:

- Legal business name
- Office address
- Phone and email
- Administrator name
- Service areas
- Payment options
- Office hours
- Whether services are non-medical only
- Any license or Residential Service Agency wording
- Privacy and terms language
- Testimonial permissions

After owner/legal review, set:

```text
BUSINESS_DETAILS_CONFIRMED=true
LEGAL_SCOPE_CONFIRMED=true
LEGAL_PRIVACY_REVIEW_CONFIRMED=true
```

## Photos And Branding

Replace development images with:

- Real company photos with signed permission
- Licensed stock photos
- Final logo and brand colors

After replacement, set:

```text
APPROVED_PHOTOS_READY=true
```

## Search And Google Business Profile

After the site is live:

- Submit `/sitemap.xml` in Google Search Console
- Add the domain to Bing Webmaster Tools
- Create or update Google Business Profile
- Match name, address, phone, service areas, and hours exactly
- Add approved business photos
- Add appointment or consultation URL
- Ask approved customers for reviews through the official profile


