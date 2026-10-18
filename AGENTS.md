# AGENTS.md — Ables Home Healthcare

Repository-specific knowledge for agents working in this codebase.

## What this is

Flask app (Flask + Flask-Login + Flask-Migrate + Flask-WTF) serving the public
Ables Home Healthcare marketing site plus a client/caregiver/admin portal.
Entry points: `wsgi.py` (production, adds proxy-header handling) and
`app.py::app`. Tests live in `tests/` and run with `python -m pytest -q`.

## Run tests

```bash
python -m pytest -q          # must stay green (currently 163 passed, 3 skipped)
```

The live-database tests skip unless their URL is set. CI runs them in two jobs
against real services (see `.github/workflows/tests.yml`): `pytest-mysql` sets
`MYSQL_TEST_URL` (MariaDB 11) and `pytest-postgres` sets `POSTGRES_TEST_URL`
(postgres:16). Keep the PostgreSQL service on the same major as the runner's
`pg_dump` client — `pg_dump` refuses to dump a newer server, and the CI job
fails loudly if the drill skips instead of running. Locally the drill skips with
a clear reason on a client/server major mismatch (e.g. a v17 client vs a v16
server) rather than failing with a confusing `pg_restore` error.

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
- Restore drills: `pg_restore` exits 1 when it ignored errors, and prints
  `errors ignored on restore: N` whenever it ignored *any* — including fatal
  ones like `relation "x" already exists`. That summary is not success.
  `backup_db._pg_restore_outcome` accepts exit 1 only when every ignored error
  is the understood version-skew GUC (`unrecognized configuration parameter`
  from a `SET`), and `_verify_postgres_restore` then confirms the dump's tables
  exist in the target. Anything else raises. Do not widen this to a substring
  match on the summary line; that would call a partial restore healthy.
- Staging verification runs against the real stack, not the test client:
  `tools/staging_recovery.py` prints one fresh admin recovery code, and
  `tools/staging_verify.py` drives gunicorn + MariaDB + MailHog + ClamAV over
  HTTPS. `--password` is required (no committed default). See
  `docs/STAGING_RELEASE_EVIDENCE.md` for the owner-controlled checks, exact
  configuration requirements, rollback, and remaining blockers.
- Restore drills verify more than the exit code. `backup_db` reads the dump's
  schema from its own table of contents (`pg_restore --list` for PostgreSQL,
  `CREATE TABLE`/`INSERT INTO` for MySQL), then confirms the schema objects
  exist and that the restored *content* matches the backup. When the backup was
  taken, a per-table fingerprint (row count plus a SHA-256 over every value) is
  recorded beside the dump and travels with it, so a restore is verified against
  what the backup contains, not just its row counts — and it stays authoritative
  even when the source database no longer exists. The fingerprint is canonical
  (type-tagged values, `None` distinct from `''`, rows read in an explicit order
  so physical row order does not matter), HMAC-authenticated with
  `BACKUP_FINGERPRINT_KEY` (or a domain-separated `SECRET_KEY` derivation), and
  required to cover every table the dump carried data for: a tampered, unsigned,
  or incomplete fingerprint fails the restore rather than falling back to the
  weaker check. Keep `BACKUP_FINGERPRINT_KEY` stable and backed up — rotating it
  (or `SECRET_KEY` with no dedicated key) makes older backups unverifiable.
  `python backup_db.py --print-key-id` prints a short, non-secret ID of the
  signing key so an operator can confirm after a restore that they still hold
  the right key (the key itself is never printed).
  Without a fingerprint the
  check falls back to comparing row counts against the live source, and when the
  source is gone, to requiring that tables the dump carried data for are
  non-empty. A restore that created the tables but lost or altered rows is
  reported as a failure. `pg_restore` exit 1 is accepted only for the understood
  version-skew GUC; the ignored-errors summary alone is never treated as
  success. Live drills: `POSTGRES_TEST_URL=...` and `MYSQL_TEST_URL=...` (the
  MySQL one needs `mysqldump`/`mysql` on PATH).
- Off-site backup is built in: `python backup_db.py --offsite` copies the
  artifact, its authenticated fingerprint, and a non-secret manifest to
  `OFFSITE_BACKUP_URL` and verifies the remote copy (SHA-256 by default), exiting
  non-zero unless verification passes. `sftp://` (OpenSSH, key-based,
  host-key-pinned via `OFFSITE_BACKUP_KEY`/`OFFSITE_SFTP_KNOWN_HOSTS`) counts as
  off-site; a `file://` path or directory does not, and `/admin/launch-checklist`
  reports off-site protection ready only after a verified upload. A password in
  an `sftp://` URL is refused, error messages are redacted, transfers stage under
  `.partial` and are renamed only after the size matches, and remote retention
  never removes the newest set. `offsite_backup.py` holds the transport; see
  `README.md` "Off-site backup and recovery" for the owner setup steps. Two
  read-only/restore helpers drive the real destination without a hand-written
  script: `backup_db.py --offsite-list` lists the complete sets (proving
  reachability and credentials) and `backup_db.py --restore-from-offsite NAME
  --restore-to <scratch URL>` restores a set using only the off-site copy,
  authenticating its fingerprint first and refusing the live `DATABASE_URL`.
- Admin routes are guarded in the body (`require_role("admin")` /
  `require_permission(...)`) after `@login_required`, not by a decorator.
  `tests/test_admin_authorization_sweep.py` enumerates every `/admin/` route
  from the URL map and checks anonymous, non-admin, and admin access, so a new
  route is covered automatically. Keep that sweep green; it was mutation-checked
  to confirm it fails when a guard is missing.
