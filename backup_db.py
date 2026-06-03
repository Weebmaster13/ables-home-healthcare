from datetime import datetime
from pathlib import Path
import shutil
import zipfile


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "ablehome.db"
BACKUP_DIR = BASE_DIR / "backups"
UPLOAD_DIR = BASE_DIR / "instance" / "uploads"


def create_backup(include_uploads=True):
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if include_uploads:
        backup_path = BACKUP_DIR / f"ablehome-full-{timestamp}.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(DB_PATH, arcname="ablehome.db")
            if UPLOAD_DIR.exists():
                for path in UPLOAD_DIR.rglob("*"):
                    if path.is_file():
                        archive.write(path, arcname=str(Path("uploads") / path.relative_to(UPLOAD_DIR)))
        return backup_path

    backup_path = BACKUP_DIR / f"ablehome-{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def cleanup_old_backups(keep=10):
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = sorted(
        [path for path in BACKUP_DIR.iterdir() if path.is_file() and path.name.startswith(("ablehome-", "ablehome-full-"))],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for backup in backups[max(1, keep):]:
        backup.unlink()
        removed.append(backup.name)
    return removed


def main():
    backup_path = create_backup(include_uploads=True)
    print(f"Backup created: {backup_path}")


if __name__ == "__main__":
    main()


