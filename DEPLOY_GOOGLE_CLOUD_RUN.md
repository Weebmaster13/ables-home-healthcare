# Deploy Ables Home Healthcare to Google Cloud Run (free tier)

> Do NOT touch GoDaddy DNS, delete the Railway service, or spend money until
> the Cloud Run deployment is tested and the owner approves the cutover.
> Railway stays live as the production site throughout.

## Why this shape (verified against Google's current docs)

- **Cloud Run** genuine free tier (request-based billing): 2M requests +
  180k vCPU-seconds + 360k GiB-seconds per month, aggregated per billing
  account. A site this size stays at $0 — but the Firebase pricing table
  lists Cloud Run as **"Not applicable" on the no-card Spark plan**: using
  Cloud Run (and Cloud Build, Artifact Registry, Secrets Manager) requires
  the **Blaze pay-as-you-go plan, i.e. a billing account with a card on
  file**, even when metered usage stays inside the $0 free quotas.
- **Database stays on Railway.** Cloud SQL (Google's managed Postgres) has
  **no free tier**, so migrating the DB would start billing immediately.
  Cloud Run connects to the existing Railway Postgres over its public
  `DATABASE_URL` — zero migration, zero new cost, all admin/appointment/
  application data preserved.
- **Uploads stay ephemeral** (Cloud Run filesystem, like Render free):
  resumes/documents survive while an instance lives, then recycle. No code
  change; flagged, not fixed — a Cloud Storage bucket is the paid-grade fix.
- **Email works better here than Render free:** GCP blocks only port 25, so
  the app's default SMTP port 587 is allowed.
- Recommended region: `us-east4` (N. Virginia, near Maryland; Tier-1 pricing,
  supports domain mapping).

## One-time project setup (owner does this in Google Cloud Console)

1. Create a project (e.g. `ables-home-health`) at console.cloud.google.com.
2. Attach a billing account (required even for $0 free-tier use) and enable
   the **Cloud Run**, **Artifact Registry**, and **Cloud Build** APIs.
3. Tell the developer the **Project ID** — everything below needs it.

## Deploy (developer or owner with `gcloud` authed to the project)

```bash
gcloud config set project PROJECT_ID
gcloud config set run/region us-east4

# Build + deploy straight from the existing GitHub repo (no local Docker needed)
gcloud run deploy ables-home-health \
  --source https://github.com/Weebmaster13/ables-home-healthcare \
  --region us-east4 --allow-unauthenticated \
  --memory 512Mi --cpu 1 --max-instances 3 \
  --set-env-vars FLASK_ENV=production,FORCE_HTTPS=true,SITE_BASE_URL=https://ables-home-healthcare-PLACEHOLDER.run.app,UPLOAD_FOLDER=/tmp/ables-uploads,MAX_CONTENT_LENGTH=5242880,ADMIN_EMAIL=admin@ableshomehealthcare.com,MAIL_DEFAULT_SENDER=admin@ableshomehealthcare.com,MAIL_SERVER=<smtp-host>,MAIL_PORT=587,MAIL_USE_TLS=true,ENABLE_SMS_REMINDERS=false
```

Then set secrets (never commit these):

```bash
printf 'REPLACE_LONG_RANDOM' | gcloud secrets create flask-secret --data-file=-
printf 'REPLACE_RAILWAY_PUBLIC_DATABASE_URL' | gcloud secrets create database-url --data-file=-
printf 'REPLACE_SMTP_USER' | gcloud secrets create mail-user --data-file=-
printf 'REPLACE_SMTP_PASS' | gcloud secrets create mail-pass --data-file=-
gcloud run services update ables-home-health --region us-east4 \
  --update-secrets SECRET_KEY=flask-secret:latest,DATABASE_URL=database-url:latest,MAIL_USERNAME=mail-user:latest,MAIL_PASSWORD=mail-pass:latest
```

Run migrations once via a Job (do NOT bake `db upgrade` into container boot):

```bash
gcloud run jobs create db-migrate --image us-docker.pkg.dev/cloudbuild/cloudbuildworker:latest \
  --region us-east4 --command flask --args --app,app,db,upgrade
```

Verify: open the service `*.run.app` URL, check `/`, `/care`, one form page,
and `/healthz`. Seed the admin with `seed_production_admin.py` values as env,
then change the password.

## Custom domain (ONLY after approval + tested deploy)

Google's recommended free path is **Firebase Hosting rewrite → Cloud Run**;
direct **Cloud Run domain mapping is Preview and "not recommended for
production."** Either way: verify `ableshomehealthcare.com` ownership in
Search Console first, then Google shows the exact DNS records to add in
GoDaddy (A/AAAA or CNAME depending on the path chosen at deploy time — do not
pre-create records). Managed TLS is automatic. Keep Railway until the domain
serves the new site, so rollback = restoring old DNS values.
