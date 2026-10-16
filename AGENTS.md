# AGENTS.md — Ables Home Healthcare

Repository-specific knowledge for agents working in this codebase.

## What this is

Flask app (Flask + Flask-Login + Flask-Migrate + Flask-WTF) serving the public
Ables Home Healthcare marketing site plus a client/caregiver/admin portal.
Entry points: `wsgi.py` (production, adds proxy-header handling) and
`app.py::app`. Tests live in `tests/` and run with `python -m pytest -q`.

## Run tests

```bash
python -m pytest -q          # 71 tests, must stay green
```

Tests use a temp SQLite DB (see `tests/conftest.py`) and disable CSRF, so many
workflows are exercised without CSRF enforcement. Add regression tests for any
security fix in `tests/test_core_features.py`.

## Deployment reality (important)

- The live site https://ableshomehealthcare.com is served by **GoDaddy cPanel
  shared hosting: Apache + Phusion Passenger + MySQL/MariaDB**, behind a WAF
  (bot challenge). It is NOT Railway/Render/VPS. Confirmed 2026-10-16 via an
  external fetcher (sandbox egress to the GoDaddy IP is blocked): live
  `/healthz` returns the full operational JSON and `database_backend: "other"`
  — i.e. MySQL on the OLDER, pre-fix code. Local `/healthz` is a minimal
  `{"status": "ok"}`; any fix must actually be deployed to take effect.
- cPanel entry point is `passenger_wsgi.py` (startup file) with entry point
  `application` (re-exports `wsgi.application`, which applies `ProxyFix`).
  Install the minimal `requirements-cpanel.txt` (no gunicorn/psycopg2 needed).
  Step-by-step runbook: `CPANEL_MYSQL.md`; authoritative guide: `DEPLOYMENT.md`.
- `preflight_cpanel.py` is a read-only pre-deploy check (config, DB reachability,
  migration status, upload folder, Passenger entrypoint). Run it before any
  migration/seed; by default it never writes, migrates, seeds, or sends mail and
  it does not create the upload folder or log directory. Pass `--write-test` to
  opt in to a single create-and-remove probe file when preparing a deploy.
- Superseded drafts to ignore unless the owner decides otherwise:
  `GODADDY_VPS_DEPLOY.md` (Docker + Nginx + PostgreSQL), `RAILWAY_DEPLOY.md`,
  `DEPLOY_RENDER_FREE.md`. Do not configure more than one platform at a time.
- Uploads must live on durable private storage. `/tmp` paths (Render free
  tier) are ephemeral and unsuitable for client documents. On cPanel use
  `/home/<user>/ables-home-healthcare/instance/uploads` (persists on home disk).
- Production fail-fast: with `FLASK_ENV=production` and no `DATABASE_URL`,
  `config.database_url()` raises instead of silently booting on SQLite.

## Conventions and invariants

- Public `form` handling lives in `routes.py` (`contact`, `referrals`,
  `free_consultation`, `schedule_consultation`, `quick_message`,
  `employment_application`). Validate required fields and email server-side
  using `validate_required()` / `is_valid_email()` before committing.
- Never redirect to `request.referrer` directly; use `safe_redirect()` to
  avoid open redirects.
- Upload scanning: `security.scan_upload_stream()` reads the WHOLE file and
  streams it to the external scanner (if configured). Do not reintroduce a
  fixed-size sample read. Without a scanner, production **fails closed**:
  uploads are rejected unless `UPLOAD_SCAN_FAIL_OPEN=true` is set (a documented
  owner-approved risk). Development still accepts unscanned uploads.
- Production startup guards (fail fast, do not weaken): with
  `FLASK_ENV=production`, `config.database_url()` raises when `DATABASE_URL` is
  missing and `config.secret_key()` raises on a missing/short/development
  `SECRET_KEY` (`config.DEVELOPMENT_SECRET_KEYS`). `preflight_cpanel.py` mirrors
  both as blocking FAILs; it duplicates the dev-key set on purpose so it can
  report the misconfiguration instead of crashing on it.
- Database backends: PostgreSQL and MySQL/MariaDB are both supported.
  `config.database_url()` normalizes bare `mysql://` → `mysql+pymysql://`
  (PyMySQL) and `postgres://` → `postgresql://` (psycopg2). `backup_db.py`
  dispatches on the URL: `pg_dump` for PostgreSQL, `mysqldump`/`mariadb-dump`
  for MySQL/MariaDB, SQLite copy only for real SQLite. A server database must
  NEVER silently fall back to a SQLite file copy — the dump tool is required
  and a missing tool or failed dump raises (no misleading success).
- Admin user creation/role changes must validate against `VALID_ROLES`.
- Public `/healthz` must stay minimal (liveness only). Detailed diagnostics
  belong behind `/admin/healthz`.
- Development seeding scripts (`init_db.py`, `reset_all.py`) must never
  hard-code credentials; they read `DEV_ADMIN_*` env vars. `reset_all.py`
  requires `RESET_ALL_CONFIRM=yes` and refuses in production.
- `load_user` rejects inactive accounts, so disabling a user invalidates
  existing sessions on the next request.
