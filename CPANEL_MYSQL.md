# Run Flask + MySQL on GoDaddy Deluxe cPanel (Python application manager)

> Do NOT create the database, Python app, DNS changes, or production data
> moves until the owner approves each step. Do not disrupt the currently-live
> site while preparing the cPanel app.

## What changed in code (branch `repair/security-audit-2026-10`, PostgreSQL intact)

- `requirements.txt`: added `PyMySQL` (pure Python, no compiler needed on
  shared hosting). `psycopg2-binary` remains for the optional PostgreSQL path.
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
   every pinned dependency supports 3.11.)
2. Open `https://aks.a28.mytemp.website` → HTTPS works? **[YES/NO]**.
   (Decides the cookie overrides in step 4.)
3. **MySQL Databases** → host value: **[DB HOST, usually `localhost`]**;
   username format: **[PREFIX FORMAT, e.g. `user_dbname`]**.
4. GoDaddy's "Send form mail using an SMTP relay server" doc → relay
   hostname: **[RELAY HOST]**.

## Files that must exist before creating the app

Clone the repo (branch `repair/security-audit-2026-10`, or `main` once merged) to exactly this path
using cPanel Terminal or SSH:

`git clone -b repair/security-audit-2026-10 https://github.com/Weebmaster13/ables-home-healthcare /home/soev6l6iqyxn/ables-home-healthcare`

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
   `pip install -r requirements-cpanel.txt` (cPanel-only minimal set) in the
   app venv. This set omits `gunicorn` and `psycopg2-binary`, which cPanel
   does not need.
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
6. Safe smoke test (BEFORE migrations/admin — nothing is created yet):
   open `/` and `/care` (expect 200), then `/healthz` (expect 200 with a
   minimal `{"status": "ok"}`). The public endpoint is liveness-only;
   detailed diagnostics live behind `/admin/healthz`. Check the Passenger
   log for "Listening at". Do NOT submit forms or log in yet.
   Before that, run the read-only preflight from the app root
   (`python preflight_cpanel.py` with the same env exported): it checks
   config, DB reachability, migration status, the upload folder, and the
   Passenger entrypoint without migrating, seeding, creating folders/files, or
   sending anything. Add `--write-test` only if you want it to also prove the
   upload folder accepts a write (it creates and removes one probe file).
7. Separately approved steps ONLY: `flask --app app db upgrade`, admin
   seeding, then full verification (form POST, login, upload, email).
   Cut over DNS only on explicit approval (rollback = restore old DNS).

## Boot-failure triage (Passenger error page on every path)

Symptom: every URL — including `/healthz` and static files — returns
Phusion's "Web application could not be started". That page means the app
crashed during **import**, before serving anything.

 narrowed diagnosis (verify against the server state before acting):
- The first upload (commit `734dea6` era) has NO fail-fast guard, so a
  missing `DATABASE_URL` there boots SQLite and serves pages — it canNOT
  produce this page. An import-level failure is therefore near-certain:
  either dependencies were never installed (`Run pip install` result was
  never confirmed), or the startup file / entry point / app root is
  mismatched.
- After uploading the fixed package, a missing `DATABASE_URL` with
  `FLASK_ENV=production` fails fast with `RuntimeError: DATABASE_URL is
  required...` in the Passenger log — that message means "set the env
  var", not "the code is broken".

Check in this order (read-only first):
1. cPanel Python app: does it show packages installed for
   `requirements.txt`? If unknown, re-run pip install from the app UI.
2. File Manager: open `/home/soev6l6iqyxn/logs/passenger.log`, read the
   last ~30 lines. `ModuleNotFoundError: No module named 'flask'` (or any
   app import) = step 1 incomplete. `RuntimeError: DATABASE_URL` = set the
   env var and restart. Anything else = paste the exact lines for review.
3. Only then: re-upload the current package, restart the app, re-check.

## Optional data move into the GoDaddy database (separate approval required)

The GoDaddy database starts empty; nothing is copied automatically. If rows
from an existing deployment must move over, use the reviewed copy script —
never restore a raw dump blindly, never manual SQL edits:

1. Back up the source database first and confirm where that backup is stored.
2. Run Alembic on the empty GoDaddy database first
   (`flask --app app db upgrade` with the cPanel env exported).
3. Dry run (reads both sides, writes nothing):
   `python migrate_data.py --from "$SOURCE_DATABASE_URL" --to "$CPANEL_DATABASE_URL"`
4. Only on explicit approval, run the same command with `--execute`,
   then compare the printed per-table counts on both sides.
5. Copy uploaded files separately over SFTP — the script prints every
   referenced resume/document filename because files are NOT in the
   database. Admin accounts are re-seeded fresh; do not copy password
   hashes by hand.

The script refuses non-empty targets and identical source/target, copies
explicit primary keys so relationships survive, and never drops, truncates,
deletes, or alters schema on either side.

## Known cosmetic follow-ups (not blockers)

- Health/status pages label backends "PostgreSQL/SQLite/MySQL/other"; MySQL is
  now reported as "mysql" (fixed on this branch), not "other".
- `postgres_backup.py` is PostgreSQL-only; use `backup_db.py` (MySQL/MariaDB)
  or cPanel/MySQL backups here.
