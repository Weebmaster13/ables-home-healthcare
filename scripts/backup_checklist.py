#!/usr/bin/env python3
"""Generate a timestamped production-backup checklist + manifest template.

Read-only: inspects only this checkout's code/config to derive WHAT must
be backed up. It never contacts production, never reads secret values,
and never labels anything verified — every item starts "unverified".

Component list is derived from models.py (tables), config.py (upload and
log locations), and the deployment docs (app files, env var NAMES only):

    python scripts/backup_checklist.py checklist.json
    python scripts/backup_checklist.py checklist.json --record-counts "sqlite:///path/to/COPY.db"

--record-counts accepts ONLY sqlite:/// file URLs pointing at a
disposable COPY of a database export (refuses mysql/postgres URLs and
refuses paths inside this repository). It fills per-table counts and a
completeness verdict: complete / incomplete / unverified.

A manifest whose files verify is still NOT a verified backup until the
isolated restore in scripts/restore_verify.py succeeds against it.
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Mirrors models.py (db.Model subclasses) + Alembic bookkeeping.
TABLES = [
    "user", "appointment", "inquiry", "referral", "care_plan",
    "caregiver_application", "audit_log", "admin_recovery_code",
    "notification_log", "portal_message", "client_document", "admin_note",
    "caregiver_availability", "caregiver_onboarding_item", "alembic_version",
]

APP_FILES = ["config.py", "routes.py", "security.py", "requirements-cpanel.txt",
             "passenger_wsgi.py", "wsgi.py", "migrations"]

ENV_NAMES = ["SECRET_KEY", "FLASK_ENV", "SITE_BASE_URL", "DATABASE_URL",
             "UPLOAD_FOLDER", "ADMIN_EMAIL", "MAIL_SERVER", "MAIL_PORT",
             "MAIL_USE_TLS", "MAIL_USERNAME", "MAIL_PASSWORD",
             "MAIL_DEFAULT_SENDER", "FORCE_HTTPS", "SESSION_COOKIE_SECURE",
             "ACCOUNT_LOCKOUT_ATTEMPTS", "ACCOUNT_LOCKOUT_MINUTES",
             "ADMIN_RECOVERY_CODES_REQUIRED"]


def generate():
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "unverified",
        "database": {
            "method": "cPanel database export of the MySQL database "
                      "(phpMyAdmin Export or Backup Wizard; export-only, "
                      "no DROP/ALTER). The in-app backup button covers "
                      "SQLite dev databases only.",
            "tables": {t: {"count": None, "select": f"SELECT COUNT(*) FROM {t};"}
                       for t in TABLES},
        },
        "uploads": {
            "upload_folder_env": "UPLOAD_FOLDER",
            "subdirs": ["documents/", "applications/"],
            "note": "Compress + download the whole upload folder; files are "
                    "NOT inside the database (only filenames are).",
        },
        "logs": ["~/logs/passenger.log tail (evidence, not data)"],
        "app_files": APP_FILES,
        "env_names_present_in_cpanel": ENV_NAMES,
        "env_values_policy": "Names only. Values stay in cPanel; never paste "
                             "into chat, tickets, or manifests.",
        "checks": {
            "off_server_copies": "unverified",
            "manifest_hashes_match": "unverified",
            "isolated_restore": "unverified",
        },
    }


def _sqlite_path(url):
    if not url.startswith("sqlite:///"):
        return None, "refusing non-sqlite URL (pass a disposable COPY path)"
    raw = url[len("sqlite:///"):]
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    repo = BASE_DIR.resolve()
    if path == repo or repo in path.parents:
        return None, "refusing path inside this repository"
    if not path.is_file():
        return None, f"file not found: {path}"
    return path, None


def record_counts(checklist, url):
    path, error = _sqlite_path(url)
    if error:
        return f"FAIL {error}"
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"FAIL cannot open read-only: {type(exc).__name__}"
    with connection:
        existing = {r[0] for r in
                    connection.execute("SELECT name FROM sqlite_master "
                                       "WHERE type='table'")}
        for table in checklist["database"]["tables"]:
            if table in existing:
                n = connection.execute(
                    f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                checklist["database"]["tables"][table]["count"] = n
    missing = [t for t, v in checklist["database"]["tables"].items()
               if v["count"] is None]
    checklist["status"] = ("incomplete"
                           if missing else "counts-recorded-unverified")
    if missing:
        return f"WARN {len(missing)} table(s) without counts: {missing}"
    return "OK all table counts recorded (restore verification still pending)"


def main(argv=None):
    args = list(argv or sys.argv[1:])
    if not args or "--record-counts" in args and len(args) < 3:
        print("usage: backup_checklist.py OUT.json "
              "[--record-counts sqlite:///path/to/COPY.db]")
        return 2
    out = Path(args[0])
    if "--record-counts" in args:
        url = args[args.index("--record-counts") + 1]
        try:
            checklist = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL cannot read checklist: {type(exc).__name__}")
            return 1
        message = record_counts(checklist, url)
        print(f"[{message.split()[0]}] {message}")
        if message.startswith("FAIL"):
            return 1
        out.write_text(json.dumps(checklist, indent=2), encoding="utf-8")
        return 0
    out.write_text(json.dumps(generate(), indent=2), encoding="utf-8")
    print(f"[PASS] checklist template -> {out} "
          f"({len(TABLES)} tables, status=unverified)")
    print("Next (owner, cPanel): export DB + download upload folder + "
          "app files, keep off-server, then verify in isolation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
