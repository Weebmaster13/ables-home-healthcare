# cPanel deployment checklist — redirect fix + hardening batch

Scope: promote the reviewed local work to the live GoDaddy app. Railway,
DNS, and mail records are not touched by any step below.

## 0. Preconditions (verify, do not skip)

- [ ] Branch `cpanel-mysql-port` pushed and CI/tests green locally (66+).
- [ ] Temp-site `/healthz` currently all-ok (proves DB + uploads healthy).
- [ ] `instance/` backup from the server already downloaded (see step 1).

## 1. Back up live versions first (File Manager)

- [ ] Download: `config.py`, `routes.py`, `security.py`,
      `requirements-cpanel.txt` from `/home/soev6l6iqyxn/ables-app`.
- [ ] Compress + download `instance/` (uploads + logs).
- [ ] Keep both backups off-server. Rollback = re-upload these exact
      files + Restart (no database change involved).

## 2. Exact files to upload (overwrite in `ables-app`)

- [ ] `config.py` — pool_pre_ping, cookie-flag overrides, DB guard.
- [ ] `routes.py` — safe-redirect helper on 13 post-action redirects,
      health-check rollback isolation, public-form throttling.
- [ ] `security.py` — `safe_redirect_url` + public-form throttle bucket.
- [ ] `requirements-cpanel.txt` — minimal set (Werkzeug 3.1.9,
      python-dotenv 1.2.2); only if server pins differ.
- [ ] Do NOT upload: `.env*`, `.secrets.cpanel`, `*.db`, `*.log`,
      `__pycache__/`, tests (optional, harmless if included).

## 3. Restart safely

- [ ] Python app → Restart. Watch `~/logs/passenger.log` for tracebacks
      for 2 minutes. Any traceback = stop, restore step-1 backups, report.

## 4. External-Referer tests (safe: GETs only, no records created)

- [ ] `curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" -e https://evil.example/ https://ableshomehealthcare.com/auth/login` → expect a page, never a redirect to evil.example.
- [ ] Quick-message POST with evil Referer is covered by automated tests;
      live POSTs create real records, so perform one only if an admin will
      delete the resulting test row immediately after.

## 5. Post-deployment checks

- [ ] Homepage 200 + brand/phone, no legacy number.
- [ ] `/care`, `/contact`, `/auth/login` (CSRF token present), CSS/JS/logo.
- [ ] `/healthz` three consecutive reads, all-ok (catches stale-pool 503s).
- [ ] `/admin/users` unauthenticated → login page, never admin content.
- [ ] `http://` → `https://` redirect intact.
- [ ] SMTP still unconfigured by design (forms save + log "skipped").

## 6. WAF/CDN status (evaluated, not deployed)

- Reverse-proxy WAF/CDN (including Cloudflare free) requires a DNS or
  nameserver change — blocked by current rules, so **no WAF is active**.
- GoDaddy's security products are paid — not purchased per rules.
- cPanel ModSecurity status is unverified from here — confirm in cPanel
  Security section; standard form POSTs are low false-positive risk.
- Origin posture stands alone: throttles, CSRF, auth gates, headers,
  fixed-plan hosting (no usage-billing shock), 1 GB account memory.
