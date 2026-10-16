#!/usr/bin/env python3
"""Read-only preflight check for the cPanel deployment.

Verifies configuration, database connectivity, migration status, upload
writability, and the Passenger entrypoint WITHOUT migrating, seeding,
deleting, sending mail, or touching production data.

Exit 0 = safe to proceed to the separately-approved migration step.
Exit 1 = fix the reported FAIL items first.

Secrets are never printed: only presence, lengths, and masked URLs appear.
"""

import os
import re
import sys
from pathlib import Path

# Kept in sync with config.DEVELOPMENT_SECRET_KEYS. Duplicated deliberately:
# importing config here would run Config's body and raise on the very
# misconfiguration this script exists to report.
DEVELOPMENT_SECRET_KEYS = {
    "dev-secret-change-later",
    "dev-change-this-before-production",
}

BASE_DIR = Path(__file__).resolve().parent


def mask_url(url):
    """Show scheme/user/host/db only; strip any password."""
    if not url:
        return "(missing)"
    shown = re.sub(r"(://[^/:@]+:)[^@]+(@)", r"\1***\2", url)
    return shown


def main():
    results = []  # (level, message); level in PASS/WARN/FAIL

    # 1. Interpreter
    impl = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 9):
        results.append(("PASS", f"Python {impl} meets the 3.9+ floor (cPanel target 3.11.16)."))
    else:
        results.append(("FAIL", f"Python {impl} is below the 3.9 floor."))

    # 2. Required files
    for name in ["passenger_wsgi.py", "wsgi.py", "requirements.txt",
                 "CPANEL_MYSQL.md", "migrations", "templates", "static"]:
        if (BASE_DIR / name).exists():
            results.append(("PASS", f"Required path present: {name}"))
        else:
            results.append(("FAIL", f"Required path MISSING: {name}"))

    # 3. Secrets and environment (presence/length only)
    secret = os.environ.get("SECRET_KEY", "")
    if len(secret) < 32 or secret in DEVELOPMENT_SECRET_KEYS:
        results.append(("FAIL", "SECRET_KEY missing, shorter than 32 chars, or a development value."))
    else:
        results.append(("PASS", f"SECRET_KEY present ({len(secret)} chars, value hidden)."))

    flask_env = os.environ.get("FLASK_ENV", "")
    db_url = os.environ.get("DATABASE_URL", "")
    if flask_env.lower() == "production" and not db_url:
        results.append(("FAIL", "FLASK_ENV=production without DATABASE_URL (app would refuse to boot)."))
    else:
        results.append(("PASS", f"DATABASE_URL configured: {mask_url(db_url)}"))

    # 4. Boot the app (no migrations run unless AUTO_MIGRATE_ON_STARTUP=true)
    try:
        from app import app
        from extensions import db
        from sqlalchemy import text
        results.append(("PASS", "Flask app imports cleanly."))
    except Exception as exc:
        results.append(("FAIL", f"Flask app failed to import: {type(exc).__name__}"))
        return report(results)

    # 5. Database connectivity (read-only SELECT 1)
    try:
        with app.app_context():
            db.session.execute(text("SELECT 1"))
        results.append(("PASS", "Database connection answered SELECT 1."))
    except Exception as exc:
        results.append(("FAIL", f"Database unreachable: {type(exc).__name__} (check DATABASE_URL host/user/password)."))
        return report(results)

    # 6. Migration status (read version only, never upgrade)
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory
        with app.app_context():
            current = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        cfg = AlembicConfig(str(BASE_DIR / "migrations" / "alembic.ini"))
        cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        if current in heads:
            results.append(("PASS", f"Migrations current at {current}."))
        else:
            results.append(("WARN", f"Migrations behind (db={current or 'none'}, head={heads[0] if heads else 'none'}) — run the approved migrate step."))
    except Exception:
        results.append(("WARN", "Migration version unreadable (fresh database?) — run the approved migrate step."))

    # 7. Upload folder writable (probe file created + removed, like /healthz)
    try:
        folder = Path(app.config["UPLOAD_FOLDER"])
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".preflight"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        results.append(("PASS", f"Upload folder writable: {folder}"))
    except Exception as exc:
        results.append(("FAIL", f"Upload folder not writable: {type(exc).__name__}"))

    # 8. Passenger entrypoint
    try:
        import passenger_wsgi
        assert passenger_wsgi.application is not None
        results.append(("PASS", "passenger_wsgi.application importable."))
    except Exception as exc:
        results.append(("FAIL", f"Passenger entrypoint broken: {type(exc).__name__}"))

    # 9. Mail settings (presence only, never values, never sending)
    relay = os.environ.get("MAIL_SERVER", "")
    if relay:
        results.append(("PASS", f"MAIL_SERVER set to {relay} (password presence: {'yes' if os.environ.get('MAIL_PASSWORD') else 'no'})."))
    else:
        results.append(("WARN", "MAIL_* unset: forms will save but emails log 'skipped' until relay is configured."))

    return report(results)


def report(results):
    failed = sum(1 for level, _ in results if level == "FAIL")
    for level, message in results:
        print(f"[{level}] {message}")
    print(f"preflight: {'FAIL' if failed else 'OK'} ({failed} blocking issue(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
