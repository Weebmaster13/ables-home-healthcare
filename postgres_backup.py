"""Standalone PostgreSQL backup helper.

Kept as a thin, dependency-free entry point for hosts that only need a dump.
It reuses ``backup_db.client_url`` so the URL handed to ``pg_dump`` is one the
native client can parse (``postgres://`` -> ``postgresql://``), and it fails
loudly instead of leaving a truncated dump behind when ``pg_dump`` is missing
or errors out.
"""
from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess

from backup_db import client_url


BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "backups"


def main():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("DATABASE_URL must point to PostgreSQL before running postgres_backup.py")

    if shutil.which("pg_dump") is None:
        raise SystemExit("pg_dump is required to back up PostgreSQL but was not found on PATH.")

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"ables-postgres-{timestamp}.dump"
    try:
        result = subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(backup_path), client_url(database_url)],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        backup_path.unlink(missing_ok=True)
        raise SystemExit(f"pg_dump could not be run: {error}")

    if result.returncode != 0 or not backup_path.exists() or backup_path.stat().st_size == 0:
        # A zero-byte or absent file is not a backup; remove it so nobody
        # mistakes it for one.
        backup_path.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or b"pg_dump failed").decode("utf-8", "replace").strip()
        raise SystemExit(f"pg_dump failed (exit {result.returncode}): {detail[:300]}")

    print(f"PostgreSQL backup created: {backup_path}")


if __name__ == "__main__":
    main()



