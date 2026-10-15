#!/usr/bin/env python3
"""Local production-readiness aggregator (read-only, offline-safe).

Runs every check that can be answered from this checkout without server
access, secrets, or writes:

  files        required deploy paths exist
  pins         requirements-cpanel.txt parses with == pins
  migrations   exactly one Alembic head
  entrypoints  wsgi app + passenger re-export import cleanly
  secret-hygiene .secrets.cpanel is git-ignored and untracked
  launch config production_check.CHECKS evaluated as WARN (a local
               checkout without prod env is *expected* to show TODOs)

Levels: PASS / WARN / FAIL. Exit 0 iff no FAIL. Live-server items
(packages installed, /healthz, admin login, real backup) are reported
as BLOCKED, never guessed.
"""

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def check_files():
    required = ["passenger_wsgi.py", "wsgi.py", "requirements-cpanel.txt",
                "requirements.txt", "CPANEL_MYSQL.md", "migrations",
                "templates", "static"]
    return [("PASS" if (BASE_DIR / n).exists() else "FAIL",
             f"path {'present' if (BASE_DIR / n).exists() else 'MISSING'}: {n}")
            for n in required]


def check_pins():
    lines = (BASE_DIR / "requirements-cpanel.txt").read_text().splitlines()
    pins = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    bad = [l for l in pins if "==" not in l]
    if bad:
        return [("FAIL", f"unpinned entries: {bad[:3]}")]
    return [("PASS", f"{len(pins)} pinned packages in requirements-cpanel.txt")]


def check_migrations():
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory
    cfg = AlembicConfig(str(BASE_DIR / "migrations" / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) == 1:
        return [("PASS", f"single migration head: {heads[0]}")]
    return [("FAIL", f"expected 1 migration head, found {heads}")]


def check_entrypoints():
    try:
        import wsgi  # noqa: F401  (builds the app; no server started)
        import passenger_wsgi  # noqa: F401
        assert passenger_wsgi.application is not None
        return [("PASS", "wsgi app + passenger_wsgi.application importable")]
    except Exception as exc:
        return [("FAIL", f"entrypoint import failed: {type(exc).__name__}")]


def check_secret_hygiene():
    out = []
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".secrets.cpanel"],
        cwd=BASE_DIR).returncode == 0
    out.append(("PASS" if ignored else "FAIL",
                ".secrets.cpanel is git-ignored" if ignored
                else ".secrets.cpanel NOT ignored"))
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=BASE_DIR,
        capture_output=True, text=True).stdout
    leaked = [l for l in tracked.splitlines()
              if ".secrets.cpanel" in l or l.endswith(".db")]
    out.append(("FAIL" if leaked else "PASS",
                f"tracked secret/db files: {leaked}" if leaked
                else "no secrets or .db files tracked"))
    return out


def check_launch_config():
    from production_check import CHECKS
    from app import app
    out = []
    with app.app_context():
        for name, predicate, _ in CHECKS:
            out.append(("WARN" if not predicate(app.config) else "PASS",
                        f"launch setting: {name}"))
    return out


def main():
    sections = [
        ("files", check_files),
        ("pins", check_pins),
        ("migrations", check_migrations),
        ("entrypoints", check_entrypoints),
        ("secret-hygiene", check_secret_hygiene),
        ("launch-config", check_launch_config),
    ]
    failed = 0
    for title, fn in sections:
        print(f"== {title} ==")
        try:
            results = fn()
        except Exception as exc:  # never crash the aggregator
            results = [("FAIL", f"{title} errored: {type(exc).__name__}")]
        for level, message in results:
            print(f"[{level}] {message}")
            failed += level == "FAIL"
    print("BLOCKED (need server/owner): installed packages, /healthz, "
          "admin login, real backup + isolated restore")
    print(f"readiness-local: {'FAIL' if failed else 'OK'} ({failed} failure(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
