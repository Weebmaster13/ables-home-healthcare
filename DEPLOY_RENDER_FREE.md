> **Superseded:** see [DEPLOYMENT.md](DEPLOYMENT.md) for the authoritative deployment guide.

# Deploy Ables Home Healthcare (free) + connect ableshomehealthcare.com

> Do NOT touch GoDaddy DNS until the Render deployment is tested and the
> owner has explicitly approved the cutover. The old site stays live until then.

## Why Render (free tier, verified against Render's current docs)

- Runs this exact Flask app: `gunicorn wsgi:app`, `requirements.txt`, Python 3.12.
- Free web service supports custom domains + automatic TLS certificates.
- `render.yaml` in this repo already describes the full stack (web + Postgres);
  it is set to **free** plans so a Blueprint deploy costs $0.

## Known free-tier limitations (from Render's docs — read before launch)

- The web service **sleeps after 15 min idle** (~1 min to wake) and has an
  **ephemeral filesystem**: uploaded resumes/documents are lost on restart.
  Relational data in Postgres is safe, but **Free Postgres expires 30 days
  after creation, has no backups, and holds 1 GB**.
- Free web services **cannot send SMTP email** (ports 25/465/587 blocked), so
  office email notifications will NOT send until the app uses an HTTPS email
  API or the service is upgraded.
- While spun down, `/robots.txt` answers "disallow all" (SEO impact).
- Render itself says: do not use Free instances for production applications.
  Recommendation: prove the site on free, then upgrade web + database to paid
  plans before collecting real client data (also complete SMTP, backups, and
  the legal/privacy review in README.md).

## Deploy steps (needs a Render account — see "your one action" below)

1. Push this repo to GitHub (only with owner approval).
2. In the Render Dashboard: **New → Blueprint**, select the repo.
   Render reads `render.yaml` and creates the web service + Postgres.
3. After deploy, open `https://<service>.onrender.com` and check `/`, `/care`,
   and one form page. First load can take ~1 min (cold start is normal).
4. In Render: run `flask --app app db upgrade` if the pre-deploy command did
   not run, then seed the admin:
   `ADMIN_SEED_EMAIL / ADMIN_SEED_NAME / ADMIN_SEED_PASSWORD` +
   `python seed_production_admin.py`, then change the password and remove test data.
5. Before cutover, set `SITE_BASE_URL=https://ableshomehealthcare.com` in the
   Render environment (it controls sitemap, canonical tags, and share URLs).

## Connect the GoDaddy domain (ONLY after approval + tested deploy)

1. Render Dashboard → service → **Settings → Custom Domains → + Add Custom
   Domain** → enter `ableshomehealthcare.com` (Render auto-adds `www` and
   redirects one to the other).
2. In GoDaddy → domain → **DNS → Manage DNS**:
   - Remove any `AAAA` records for the domain.
   - Root: **A** record, host `@`, value **`216.24.57.1`**
     (Render's load-balancer IP; GoDaddy roots cannot use CNAME).
   - `www`: **CNAME** record, host `www`, value **`<service>.onrender.com`**
     (your exact Render subdomain from the dashboard).
3. Back in Render, click **Verify**. DNS can take minutes–hours. Render then
   issues the TLS certificate automatically; `http://` redirects to `https://`.

## HTTPS + replacing the old site

- HTTPS is automatic: Render provisions and renews the certificate once the
  domain verifies. No certificate files to manage.
- The old GoDaddy site is replaced the moment DNS points at Render — there is
  no file to delete on GoDaddy's side for the cutover itself. Keep the old
  site's content/export until the new domain is verified working, then cancel
  or remove the old hosting per GoDaddy's screens.
- If anything fails, reverting = restoring the old DNS values in GoDaddy.
