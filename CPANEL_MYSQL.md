# Run Flask + MySQL on GoDaddy Deluxe cPanel (Python application manager)

> Do NOT create the database, Python app, DNS changes, or production data
> moves until the owner approves each step. Railway stays live throughout.

## What changed in code (branch `cpanel-mysql-port`, PostgreSQL intact)

- `requirements.txt`: added `PyMySQL` (pure Python, no compiler needed on
  shared hosting). `psycopg2-binary` stays for Railway production.
- `config.py`: bare `mysql://` URLs normalize to `mysql+pymysql://`,
  mirroring the existing `postgres://` fix.
- `passenger_wsgi.py` (new): re-exports the existing WSGI app for
  Passenger. Startup file = `passenger_wsgi.py`, entry point =
  `application`. No app logic changed.
- Untouched on purpose: models, migrations (backend-agnostic Alembic
  chain), datetime handling (`normalize_dt` already naive/aware-safe),
  uploads (plain filesystem paths, persist on home-dir disk), seeding
  (`seed_production_admin.py` is ORM-only), translations, admin tools.

## cPanel setup (owner or admin, in order)

1. **MySQL Databases**: create database + user, note host (usually
   `localhost`), name, user, password.
2. **Setup Python App**: Python 3.9+ (use the newest offered), app root =
   repo folder, startup file `passenger_wsgi.py`, entry point
   `application`; `pip install -r requirements.txt` in the app venv.
3. **Environment variables** in the Python app (never in files):
   `SECRET_KEY=<long random>`, `FLASK_ENV=production`, `FORCE_HTTPS=true`,
   `SITE_BASE_URL=https://aks.a28.mytemp.website` for testing (switch to
   `https://ableshomehealthcare.com` only at approved cutover),
   `DATABASE_URL=mysql+pymysql://USER:PASS@localhost:3306/DBNAME`,
   `ADMIN_EMAIL` + `MAIL_*` pointed at **GoDaddy's SMTP relay**
   (shared hosting restricts outside SMTP; see GoDaddy's "Send form mail
   using an SMTP relay server" doc — password goes in cPanel, not here).
   If the temporary URL serves plain HTTP only, also set
   `SESSION_COOKIE_SECURE=false` + `FORCE_HTTPS=false` for testing, then
   remove both overrides (defaults are secure) before cutover.
4. **Migrate + seed** (cPanel terminal or SSH):
   `flask --app app db upgrade`, then `seed_production_admin.py` values as
   env vars; change the password immediately.
5. Verify `/`, `/care`, one form POST, login, `/healthz`, uploads, then
   cut over DNS only on explicit approval (rollback = restore old DNS).

## Known cosmetic follow-ups (not blockers)

- Health/status pages label backends "PostgreSQL/SQLite/other" — MySQL
  reports as "other". Cosmetic only.
- `postgres_backup.py` is PostgreSQL-only; use cPanel/MySQL backups here.
