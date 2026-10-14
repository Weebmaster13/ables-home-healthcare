# Ables Home Healthcare — Incident Response Runbook

Keep Railway live until any incident is resolved; it is the rollback target.
Never paste passwords, tokens, or client data into chat, tickets, or logs.

## Suspected account compromise (admin/client/caregiver)

1. **Contain:** cPanel Python app → STOP APP (halts sessions/logins).
   In `/admin/users`, set the account inactive (do not delete — preserves
   audit trail). I (OpenCode) can: inspect logs/routes; I cannot click
   cPanel — owner or approved admin performs these two steps.
2. **Evidence:** download `~/logs/passenger.log` tail + export
   `/admin/audit` CSV filtered to the account; note timestamps.
3. **Rotate:** change `SECRET_KEY` in cPanel env (invalidates all sessions),
   then the affected account password via Password change page; regenerate
   admin recovery codes (`/admin/security`).
4. **Recover:** restart app, verify `/healthz` ok, confirm login works with
   the new secrets. GoDaddy support needed only if the panel itself is
   unreachable or the account email is compromised (password reset flows).

## Malicious file upload

1. **Contain:** `/admin/documents` → archive the file; block the uploader
   account as above. Uploads live outside the web root and are served only
   through owner-or-admin download routes, so a payload is not directly
   web-executable — do NOT move it into `public_html` or `static/`.
2. **Evidence:** keep the stored file + audit row; note uploader, time, IP
   from logs if present.
3. **Recover:** delete via `/admin/documents` delete after evidence is kept.

## Exposed credentials (SECRET_KEY, DB/mail password, recovery code)

1. **Contain:** rotate the exposed value immediately (cPanel env for app
   secrets; MySQL Databases for DB password + update `DATABASE_URL`).
2. Rotating `SECRET_KEY` logs everyone out — announce a brief login
   interruption to staff first if possible.
3. **Verify:** restart, `/healthz` ok, test login.

## Database or client-data exposure

1. **Contain:** STOP APP; do not delete rows (deletion destroys evidence).
2. **Evidence:** audit CSV export, log tail, list of exposed tables.
3. Determine scope (which tables/fields) before notifying anyone; involve
   the business owner before any client notification.
4. **Recover:** restore only into a *separate* test location; never
   overwrite production to "test" a restore.

## Defacement or unexpected file changes

1. **Evidence:** File Manager timestamps + `git status` against branch
   `cpanel-mysql-port` (any unapproved diff is suspect).
2. **Recover:** re-upload verified files from the branch; restart; verify.

## Denial-of-service / traffic flood

1. Shared hosting has no origin-level volumetric protection; CloudLinux
   throttles CPU/EP instead — expect slowness, not a bill (fixed plan).
2. In-app throttles cover login/register/public forms; do not add IP
   blocks without evidence (risk of blocking real families).
3. If sustained: enable GoDaddy's security product (paid — needs owner
   approval) or front with a CDN/WAF (needs DNS change + approval).

## What OpenCode can/can't do

- Can: inspect code/logs/routes, prepare fixes + tests, verify public
  endpoints, draft messages.
- Cannot: click cPanel, rotate server-side secrets, touch DNS/mail,
  contact clients or authorities. Those need the owner or GoDaddy support.
