from datetime import datetime
from pathlib import Path
import os
import subprocess


BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "backups"


def main():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("DATABASE_URL must point to PostgreSQL before running postgres_backup.py")

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"ables-postgres-{timestamp}.dump"
    subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(backup_path), database_url],
        check=True,
    )
    print(f"PostgreSQL backup created: {backup_path}")


if __name__ == "__main__":
    main()


