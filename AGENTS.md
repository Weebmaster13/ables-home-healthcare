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

- The live site https://ableshomehealthcare.com is served by **Apache +
  Phusion Passenger** behind a WAF (bot challenge). It is NOT Railway/Render.
  The deployed code is OLDER than this repo — live `/healthz` still leaks
  operational JSON, while local `/healthz` is a minimal `{"status": "ok"}`.
  Any fix here must actually be deployed to take effect on the live site.
- The repo also carries Render/Railway/Docker/GoDaddy-VPS deploy configs.
  `GODADDY_VPS_DEPLOY.md` describes the Docker + Nginx + Postgres path.
- Uploads must live on durable private storage. `/tmp` paths (Render free
  tier) are ephemeral and unsuitable for client documents.

## Conventions and invariants

- Public `form` handling lives in `routes.py` (`contact`, `referrals`,
  `free_consultation`, `schedule_consultation`, `quick_message`,
  `employment_application`). Validate required fields and email server-side
  using `validate_required()` / `is_valid_email()` before committing.
- Never redirect to `request.referrer` directly; use `safe_redirect()` to
  avoid open redirects.
- Upload scanning: `security.scan_upload_stream()` reads the WHOLE file and
  streams it to the external scanner (if configured). Do not reintroduce a
  fixed-size sample read.
- Admin user creation/role changes must validate against `VALID_ROLES`.
- Public `/healthz` must stay minimal (liveness only). Detailed diagnostics
  belong behind `/admin/healthz`.
- Development seeding scripts (`init_db.py`, `reset_all.py`) must never
  hard-code credentials; they read `DEV_ADMIN_*` env vars. `reset_all.py`
  requires `RESET_ALL_CONFIRM=yes` and refuses in production.
- `load_user` rejects inactive accounts, so disabling a user invalidates
  existing sessions on the next request.
