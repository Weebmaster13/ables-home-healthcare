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
- `config.py`: production without `DATABASE_URL` now fails fast instead of
  silently booting on local SQLite; secure-cookie flags accept env
  overrides (defaults unchanged and secure).
- `SECRET_KEY` for cPanel lives ONLY in the local ignored file
  `.secrets.cpanel` (see `CPANEL_SECRET_KEY=` there) — never committed,
  never pasted anywhere except the cPanel Environment Variables screen.
- Untouched on purpose: models, migrations (backend-agnostic Alembic
  chain), datetime handling (`normalize_dt` already naive/aware-safe),
  uploads (plain filesystem paths, persist on home-dir disk), seeding
  (`seed_production_admin.py` is ORM-only), translations, admin tools.

## Step 0 — read-only checks (create nothing yet)

The temporary domain currently shows GoDaddy's default page — expected, no
app exists there yet. It tells us nothing about HTTPS support, so verify
that separately below. In cPanel, only look and write down:
1. **Setup Python App** → versions offered top out at **3.11.16 — use
   exactly that**. (Verified: the code parses under the 3.11 grammar and
   every pinned dependency supports 3.11; the repo's 3.12 pin is for
   Render/Railway only.)
2. Open `https://aks.a28.mytemp.website` → HTTPS works? **[YES/NO]**.
   (Decides the cookie overrides in step 4.)
3. **MySQL Databases** → host value: **[DB HOST, usually `localhost`]**;
   username format: **[PREFIX FORMAT, e.g. `user_dbname`]**.
4. GoDaddy's "Send form mail using an SMTP relay server" doc → relay
   hostname: **[RELAY HOST]**.
5. Open the Railway site → still live? **[YES — do not touch it]**.

## Files that must exist before creating the app

Clone the repo (`cpanel-mysql-port` branch content) to exactly this path
using cPanel Terminal or SSH:

`git clone -b cpanel-mysql-port https://github.com/Weebmaster13/ables-home-healthcare /home/soev6l6iqyxn/ables-home-healthcare`

Use the temporary domain at its **root only**
(`https://aks.a28.mytemp.website/`) — the app builds every URL from the
domain root and does NOT support subpaths like `/ables-test`.

That one folder provides everything Passenger needs (`passenger_wsgi.py`,
`wsgi.py`, app code, `migrations/`, `templates/`, `static/`,
`requirements.txt`). Do NOT upload a local `instance/` folder (database,
uploads, logs) — the server creates its own. No other uploads needed.

## Create Application screen — exact values

| Field | Enter |
|---|---|
| Python version | `3.11.16` |
| Application root | `/home/soev6l6iqyxn/ables-home-healthcare` |
| Application URL | `https://aks.a28.mytemp.website/` (domain `aks.a28.mytemp.website`, path `/` = root) |
| Application startup file | `passenger_wsgi.py` |
| Application entry point | `application` |
| Passenger log file | Leave the pre-filled default unchanged |

## cPanel setup (owner or admin, in order — each step needs approval first)

1. **MySQL Databases**: create database **[DB NAME]**, user **[DB USER]**,
   password **[DB PASSWORD — generate, store privately]**.
2. **Setup Python App**: Python **3.11.16** (verified compatible — see tests), app root = repo
   folder, startup file `passenger_wsgi.py`, entry point `application`;
   `pip install -r requirements.txt` in the app venv (`gunicorn` and
   `psycopg2-binary` install but stay unused here — harmless).
3. **Environment variables you can set immediately** (Python app →
   Environment Variables screen; never in files). Generate the secret with
   `python -c "import secrets; print(secrets.token_hex(32))"`:
   - `SECRET_KEY` = **[GENERATED]**
   - `FLASK_ENV` = `production`
   - `SITE_BASE_URL` = `https://aks.a28.mytemp.website` (switch to
     `https://ableshomehealthcare.com` only at approved cutover)
   - `UPLOAD_FOLDER` =
     `/home/soev6l6iqyxn/ables-home-healthcare/instance/uploads`
   - `ADMIN_EMAIL` = `admin@ableshomehealthcare.com`
4. **Environment variables ONLY after step 1 (need the database first):**
   - `DATABASE_URL` =
     `mysql+pymysql://[DB USER]:[DB PASSWORD]@[DB HOST]:3306/[DB NAME]`
   - `MAIL_SERVER` = `[RELAY HOST]`, `MAIL_PORT` = `587`,
     `MAIL_USE_TLS` = `true`, `MAIL_USERNAME`/`MAIL_PASSWORD` = **[RELAY
     CREDENTIALS]** (password in cPanel only)
   - `FORCE_HTTPS` = `true`, except: if step 0.2 was NO (plain HTTP),
     set `SESSION_COOKIE_SECURE=false` + `FORCE_HTTPS=false` for testing,
     then **remove both** (defaults are secure) before cutover.
5. **Migrate + seed**  — app variables do NOT reach the terminal, so export
   them first in the same terminal session (same bracketed values as step
   3, plus `ADMIN_SEED_EMAIL/NAME/PASSWORD`), then run
   `flask --app app db upgrade`, then `python seed_production_admin.py`;
   change the password immediately.
5. Safe smoke test (BEFORE migrations/admin — nothing is created yet):
   open `/` and `/care` (expect 200), then `/healthz` (expect 200 with
   `"database_schema": "error"` until step 6 runs — that error is normal
   pre-migration, not a failure). Check the Passenger log for
   "Listening at". Do NOT submit forms or log in yet.
6. Separately approved steps ONLY: `flask db upgrade`, admin seeding,
   then full verification (form POST, login, upload, email). Cut over DNS
   only on explicit approval (rollback = restore old DNS).

## Known cosmetic follow-ups (not blockers)

- Health/status pages label backends "PostgreSQL/SQLite/other" — MySQL
  reports as "other". Cosmetic only.
- `postgres_backup.py` is PostgreSQL-only; use cPanel/MySQL backups here.
