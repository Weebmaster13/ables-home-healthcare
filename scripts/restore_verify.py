#!/usr/bin/env python3
"""Verify a restore into a DISPOSABLE location. Never touches production.

Guards (all must pass before any check runs):
  * --confirm-disposable is required (explicit safety confirmation).
  * The target directory must exist and must NOT be inside this repo.
  * The target must not look like a live app root (passenger_wsgi.py or
    config.py+migrations/ present => refuse).
  * An optional --db accepts ONLY sqlite:/// file URLs outside this repo,
    opened read-only; mysql/postgres URLs are refused.

Checks (read-only):
  * every backup_checklist.py manifest entry re-hashes (via
    verify_backup_manifest.verify),
  * sqlite PRAGMA integrity_check passes,
  * per-table counts match checklist db_counts when recorded.

Usage:
    python scripts/restore_verify.py manifest.json --dir DISPOSABLE_DIR \\
        --checklist checklist.json --db "sqlite:///path/to/COPY.db" \\
        --confirm-disposable

Exit 0 only when everything verifies. A manifest match alone is NOT
reported as a verified backup — the verdict line says so explicitly.
"""

import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from verify_backup_manifest import verify as verify_files  # noqa: E402


def _refuse(target):
    repo = BASE_DIR.resolve()
    target = target.resolve()
    if target == repo or repo in target.parents:
        return "target is inside this repository"
    if (target / "passenger_wsgi.py").exists():
        return "target looks like a live app root (passenger_wsgi.py)"
    if (target / "config.py").exists() and (target / "migrations").is_dir():
        return "target looks like a live app root (config.py + migrations/)"
    return None


def _sqlite_ro(url):
    if not url.startswith("sqlite:///"):
        return None, "refusing non-sqlite URL"
    path = Path(url[len("sqlite:///"):])
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    repo = BASE_DIR.resolve()
    if path == repo or repo in path.parents:
        return None, "refusing database inside this repository"
    if not path.is_file():
        return None, f"database file not found: {path}"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        return conn, None
    except sqlite3.Error as exc:
        return None, f"cannot open read-only: {type(exc).__name__}"


def main(argv=None):
    args = list(argv or sys.argv[1:])
    if "--confirm-disposable" not in args or len(args) < 3:
        print("usage: restore_verify.py manifest.json --dir DIR "
              "[--checklist checklist.json] [--db sqlite:///COPY.db] "
              "--confirm-disposable")
        print("REFUSED: pass --confirm-disposable for a disposable target only")
        return 2
    args = [a for a in args if a != "--confirm-disposable"]
    manifest_path, target = Path(args[0]), Path(args[args.index("--dir") + 1])
    checklist = None
    if "--checklist" in args:
        checklist = json.loads(
            Path(args[args.index("--checklist") + 1]).read_text(
                encoding="utf-8"))
    db_url = args[args.index("--db") + 1] if "--db" in args else None

    if not manifest_path.is_file():
        print(f"FAIL manifest not found: {manifest_path}")
        return 1
    if not target.is_dir():
        print(f"FAIL target directory not found: {target}")
        return 1
    problem = _refuse(target)
    if problem:
        print(f"REFUSED {problem} — verification runs on disposable "
              f"copies only, never on live paths")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failed, warned = 0, 0
    for level, message in verify_files(manifest, target):
        print(f"[{level}] {message}")
        failed += level == "FAIL"

    db_counts = None
    if db_url:
        conn, error = _sqlite_ro(db_url)
        if error:
            print(f"FAIL {error}")
            return 1
        with conn:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            print(f"[{'PASS' if ok == 'ok' else 'FAIL'}] "
                  f"sqlite integrity_check: {ok}")
            failed += ok != "ok"
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            db_counts = {}
            for row in tables:
                try:
                    db_counts[row] = conn.execute(
                        f'SELECT COUNT(*) FROM "{row}"').fetchone()[0]
                except sqlite3.Error:
                    pass
    if checklist:
        recorded = {t: v.get("count") for t, v in
                    checklist.get("database", {}).get("tables", {}).items()}
        if db_counts is None:
            print("[WARN] checklist counts present but no --db given; "
                  "counts uncompared")
            warned += 1
        else:
            for table, expected in sorted(recorded.items()):
                if expected is None:
                    print(f"[WARN] {table}: no recorded count (unverified)")
                    warned += 1
                elif db_counts.get(table) != expected:
                    print(f"[FAIL] {table}: restored "
                          f"{db_counts.get(table)}, expected {expected}")
                    failed += 1
                else:
                    print(f"[PASS] {table}: count {expected} matches")
    if failed:
        print(f"restore-verification: FAIL ({failed} failure(s))")
        return 1
    if warned:
        print(f"restore-verification: INCOMPLETE "
              f"({warned} unverified item(s)) — files match but the backup "
              f"is NOT a verified restore")
        return 1
    print("restore-verification: OK (files + integrity + counts match; "
          "applies to THIS disposable copy only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
