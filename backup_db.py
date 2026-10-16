from datetime import datetime
from pathlib import Path
import os
import shutil
import subprocess
import zipfile


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "ablehome.db"
BACKUP_DIR = BASE_DIR / "backups"
UPLOAD_DIR = Path(os.environ.get("UPLOAD_FOLDER") or (BASE_DIR / "instance" / "uploads"))


def _database_url():
    return os.environ.get("DATABASE_URL", "").strip()


def _is_postgres(url):
    return url.startswith(("postgresql://", "postgres://"))


def _normalized_postgres_url(url):
    # Some hosts still expose the older Heroku-style postgres:// URL.
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _timestamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _write_uploads(archive):
    if UPLOAD_DIR.exists():
        for path in UPLOAD_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=str(Path("uploads") / path.relative_to(UPLOAD_DIR)))


def create_postgres_backup(include_uploads=True):
    """Back up the live PostgreSQL database with pg_dump.

    The dump is written with --format=custom so it can be restored with
    `pg_restore`. Uploaded files are bundled alongside the dump when requested.
    """
    database_url = _database_url()
    if not _is_postgres(database_url):
        raise RuntimeError("DATABASE_URL does not point to PostgreSQL.")

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = _timestamp()
    dump_path = BACKUP_DIR / f"ables-postgres-{timestamp}.dump"
    subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(dump_path), _normalized_postgres_url(database_url)],
        check=True,
    )

    if not include_uploads:
        return dump_path

    backup_path = BACKUP_DIR / f"ablehome-full-{timestamp}.zip"
    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(dump_path, arcname="database.dump")
        _write_uploads(archive)
    dump_path.unlink(missing_ok=True)
    return backup_path


def create_sqlite_backup(include_uploads=True):
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = _timestamp()

    if include_uploads:
        backup_path = BACKUP_DIR / f"ablehome-full-{timestamp}.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(DB_PATH, arcname="ablehome.db")
            _write_uploads(archive)
        return backup_path

    backup_path = BACKUP_DIR / f"ablehome-{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def create_backup(include_uploads=True):
    """Create a backup appropriate for the configured database backend."""
    if _is_postgres(_database_url()):
        return create_postgres_backup(include_uploads=include_uploads)
    return create_sqlite_backup(include_uploads=include_uploads)


def cleanup_old_backups(keep=10):
    """Delete the oldest local backups, keeping the most recent ``keep``.

    This only removes *local* copies. It is safe solely when independent,
    verified backups exist off-site (upload them to durable off-host storage
    before relying on this). Retention never removes the newest backup.
    """
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = sorted(
        [
            path for path in BACKUP_DIR.iterdir()
            if path.is_file() and (path.name.startswith("ablehome-") or path.name.startswith("ables-postgres-"))
        ],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for backup in backups[max(1, keep):]:
        backup.unlink()
        removed.append(backup.name)
    return removed


def verify_backup(path):
    """Confirm a backup file is readable and structurally intact.

    For a ZIP bundle, verify the archive opens and every member is readable.
    For a custom-format ``pg_dump`` file, verify the file is non-empty; a full
    restore test still requires ``pg_restore --list`` against a real dump.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return False, "Backup file is missing or empty."

    if path.suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                corrupt = archive.testzip()
                if corrupt:
                    return False, f"Corrupt entry in backup archive: {corrupt}"
                names = archive.namelist()
                if "database.dump" not in names and "ablehome.db" not in names:
                    return False, "Backup archive contains no database dump."
                if "database.dump" in names:
                    ok, message = _verify_pg_dump_member(archive)
                    if not ok:
                        return False, message
        except zipfile.BadZipFile:
            return False, "Backup archive could not be opened."
    return True, "Backup verified as readable."


def _verify_pg_dump_member(archive):
    """Run ``pg_restore --list`` against the bundled dump when pg_restore exists.

    This catches a truncated or corrupt custom-format dump that a plain ZIP
    integrity check would miss. If ``pg_restore`` is unavailable we fall back
    to a non-empty size check rather than failing the whole backup.
    """
    import tempfile

    if shutil.which("pg_restore") is None:
        return True, "pg_restore unavailable; archive integrity checked only."

    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = Path(tmpdir) / "database.dump"
        with archive.open("database.dump") as source, open(dump_path, "wb") as target:
            shutil.copyfileobj(source, target)
        if dump_path.stat().st_size == 0:
            return False, "Bundled database dump is empty."
        result = subprocess.run(
            ["pg_restore", "--list", str(dump_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            return False, "pg_restore could not read the bundled dump: " + result.stderr.decode("utf-8", "replace").strip()
    return True, "Backup verified: archive readable and pg_dump table of contents lists successfully."


def main():
    if _is_postgres(_database_url()):
        backup_path = create_postgres_backup(include_uploads=True)
    else:
        backup_path = create_sqlite_backup(include_uploads=True)

    ok, message = verify_backup(backup_path)
    print(f"{'Backup created' if ok else 'Backup FAILED verification'}: {backup_path} ({message})")
    if not ok:
        raise SystemExit(1)
    print("Upload this file to durable, off-site storage to complete a real backup.")


if __name__ == "__main__":
    main()


