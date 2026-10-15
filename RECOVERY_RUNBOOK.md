# Able Home Healthcare — Recovery Runbook

Companion to `SECURITY_RUNBOOK.md` (incidents) and `DEPLOY_CHECKLIST_CPANEL.md`
(deploys). This covers getting the service back from a **verified backup**.
A tested procedure is not an executed recovery: do not claim recovery
until the acceptance criteria below pass against real data.

## What a complete backup is (all four, off-server, dated together)

1. **Database export** — cPanel MySQL export (phpMyAdmin Export or Backup
   Wizard, export-only). Record per-table counts:
   `SELECT COUNT(*) FROM <table>;` for the 15 tables listed by
   `python scripts/backup_checklist.py checklist.json`.
2. **Upload folder** — the whole `UPLOAD_FOLDER` (`documents/`,
   `applications/`). Files are NOT in the database, only filenames are.
3. **App files** — `config.py`, `routes.py`, `security.py`,
   `requirements-cpanel.txt`, `passenger_wsgi.py`, `wsgi.py`, `migrations/`.
4. **Environment names** — the checklist's env-name list (presence only;
   values stay in cPanel and are never pasted anywhere).

The in-app backup button covers SQLite dev databases only; on MySQL
production it explains itself and refuses instead of failing silently.

## Restoration order (isolated location first, production only on approval)

1. Stage copies in an **empty disposable directory** (never the live app
   path, never the live database name).
2. `python scripts/backup_inventory.py manifest.json <staged-copy>`
3. `python scripts/restore_verify.py manifest.json --dir <staged-copy>
   --checklist checklist.json --db "sqlite:///path/to/COPY.db"
   --confirm-disposable` — exit 0 required (files + `integrity_check` +
   count match). INCOMPLETE or FAIL stops the process.
4. Boot the app against the copy with test-only env; open `/healthz`,
   one record per key table, one file per upload subdir.
5. Only on explicit owner approval per step: repeat against production
   (re-upload exact app files, import the database, Restart), then run
   the Section-4 checks live.

## Rollback

- App files: re-upload the pre-change downloads + Restart (no DB change).
- Database: re-import the previous export (downtime; announce first —
  imports replace data created after the export).
- If anything is unclear, STOP APP and escalate instead of improvising.

## Retention and storage (shared-hosting appropriate)

- Keep the 3 most recent dated backup sets + 1 monthly set, off-server
  (owner workstation or business cloud drive — never only on the server).
- `backups/` on the server is a transfer area, not an archive; the admin
  cleanup keeps at most 50 and defaults to 10.
- Re-verify a backup quarterly: inventory + restore-verify on a fresh
  disposable copy. `restore_verify.py` refuses live paths by construction.

## Recovery acceptance criteria (all required)

- [ ] Manifest verifies 100% (hashes + sizes) on the restored copy.
- [ ] Database `integrity_check` ok and every table count matches.
- [ ] `/healthz` ok, login page renders, admin login works (owner).
- [ ] One representative record per key table opens; one file per upload
      subdir downloads with the original filename.
- [ ] No traceback in `passenger.log` for 10 minutes after Restart.

## Escalation

- App won't boot after restore: STOP APP, capture the log tail, restore
  the previous known-good files, report exact lines (redact secrets).
- Suspected data exposure during recovery: follow `SECURITY_RUNBOOK.md`
  database-exposure section before notifying anyone except the owner.
- GoDaddy support only for panel/SSH outages or lost account access.
