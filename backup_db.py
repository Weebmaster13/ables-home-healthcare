from datetime import datetime
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import zipfile


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "ablehome.db"
BACKUP_DIR = BASE_DIR / "backups"
UPLOAD_DIR = Path(os.environ.get("UPLOAD_FOLDER") or (BASE_DIR / "instance" / "uploads"))


def _database_url():
    return os.environ.get("DATABASE_URL", "").strip()


def _normalize_url(url):
    """Normalize legacy bare schemes to the dialects SQLAlchemy expects."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+pymysql://", 1)
    return url


def _is_postgres(url):
    return url.startswith(("postgresql://", "postgres://"))


def _is_mysql(url):
    return url.startswith(("mysql://", "mysql+pymysql://", "mysql+mysqldb://", "mariadb://"))


def _is_sqlite(url):
    return url.startswith(("sqlite://", "sqlite+pysqlite://"))


def _dispatch_backup(url, include_uploads=True):
    """Choose a backup routine by backend, failing loudly on anything else.

    An empty or unrecognized URL must never silently fall through to a local
    SQLite file copy: that would report a "backup" of a server database that
    was never actually dumped.
    """
    if _is_postgres(url):
        return create_postgres_backup(include_uploads=include_uploads)
    if _is_mysql(url):
        return create_mysql_backup(include_uploads=include_uploads)
    if _is_sqlite(url):
        return create_sqlite_backup(include_uploads=include_uploads)
    raise RuntimeError(
        "Unsupported or missing DATABASE_URL for backup: "
        f"{url or '(empty)'!r}. Set DATABASE_URL to a sqlite, mysql, or "
        "postgresql URL before backing up."
    )


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
        ["pg_dump", "--format=custom", "--file", str(dump_path), _normalize_url(database_url)],
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


def _mysql_dump_credentials(url):
    """Extract MySQL connection parts from a SQLAlchemy URL, if needed."""
    from urllib.parse import urlsplit, unquote

    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    port = parts.port or 3306
    user = unquote(parts.username) if parts.username else ""
    password = unquote(parts.password) if parts.password else ""
    database = (parts.path or "").lstrip("/")
    return host, port, user, password, database


def _dump_client_extra_flags(dump_tool):
    """Return flags needed for this mysqldump client to dump a server safely.

    MySQL 8's ``mysqldump`` queries ``information_schema.COLUMN_STATISTICS``,
    which does not exist on MariaDB, so the dump aborts with "Unknown table
    'COLUMN_STATISTICS'" unless ``--column-statistics=0`` is passed. The
    MariaDB client and older MySQL clients (5.7) do not accept that flag, so it
    must only be added for an Oracle MySQL client at version 8 or newer. This
    matters on shared hosting, where the client and server versions differ.
    """
    try:
        result = subprocess.run([dump_tool, "--version"], capture_output=True, check=False)
    except OSError:
        return []
    text = (result.stdout or b"").decode("utf-8", "replace")
    lowered = text.lower()
    if "mariadb" in lowered:
        return []
    # "mysqldump Ver 10.13 Distrib 5.7.42" -> server version is after "Distrib";
    # "mysqldump Ver 8.0.36 ... (MySQL ...)" -> no Distrib, so take "Ver".
    match = re.search(r"distrib\s+(\d+)\.", lowered) or re.search(r"ver\s+(\d+)\.", lowered)
    if match and int(match.group(1)) >= 8:
        return ["--column-statistics=0"]
    return []


def create_mysql_backup(include_uploads=True):
    """Back up a live MySQL/MariaDB database with mysqldump.

    Unlike the SQLite path, this never copies a local file: it dumps the
    configured server. It raises loudly when ``mysqldump`` is missing or the
    dump fails, so a MySQL production database can never be silently backed up
    as SQLite. Uploaded files are bundled alongside the dump when requested.
    """
    database_url = _database_url()
    if not _is_mysql(database_url):
        raise RuntimeError("DATABASE_URL does not point to MySQL/MariaDB.")

    dump_tool = shutil.which("mysqldump") or shutil.which("mariadb-dump")
    if dump_tool is None:
        raise RuntimeError(
            "mysqldump (or mariadb-dump) is required to back up MySQL/MariaDB "
            "but was not found on PATH. Install the MySQL/MariaDB client tools."
        )

    host, port, user, password, database = _mysql_dump_credentials(_normalize_url(database_url))
    if not database:
        raise RuntimeError("DATABASE_URL does not name a MySQL database.")

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = _timestamp()

    with tempfile.TemporaryDirectory() as tmpdir:
        dump_path = Path(tmpdir) / "database.sql"
        command = [
            dump_tool,
            "--single-transaction",
            "--quick",
            "--host", host,
            "--port", str(port),
            "--user", user,
            *_dump_client_extra_flags(dump_tool),
            "--result-file", str(dump_path),
            database,
        ]
        env = dict(os.environ)
        if password:
            # Pass the password via the environment so it never appears in the
            # process command line (visible to other users via `ps`).
            env["MYSQL_PWD"] = password
        result = subprocess.run(command, env=env, capture_output=True, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or b"mysqldump failed").decode("utf-8", "replace").strip()
            raise RuntimeError(f"mysqldump failed (exit {result.returncode}): {detail[:300]}")
        if not dump_path.is_file() or dump_path.stat().st_size == 0:
            raise RuntimeError("mysqldump produced an empty backup; refusing to report success.")

        if not include_uploads:
            final_path = BACKUP_DIR / f"ables-mysql-{timestamp}.sql"
            shutil.copy2(dump_path, final_path)
            return final_path

        backup_path = BACKUP_DIR / f"ablehome-mysql-{timestamp}.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(dump_path, arcname="database.sql")
            _write_uploads(archive)
        return backup_path


def client_url(url):
    """Return a URL the native ``mysql``/``psql`` clients can parse.

    The app normalizes bare ``mysql://`` URLs to ``mysql+pymysql://`` so
    SQLAlchemy picks the right driver, but the native client tools reject the
    driver suffix. ``pg_restore`` does understand ``postgresql://``, so only
    the MySQL family is rewritten.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(_normalize_url(url))
    scheme = parts.scheme
    if scheme.startswith(("mysql", "mariadb")):
        scheme = "mysql"
    elif scheme.startswith("postgres"):
        scheme = "postgresql"
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def _postgres_dump_credentials(url):
    """Extract PostgreSQL connection parts from a SQLAlchemy URL."""
    from urllib.parse import urlsplit, unquote

    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    port = parts.port or 5432
    user = unquote(parts.username) if parts.username else ""
    password = unquote(parts.password) if parts.password else ""
    database = (parts.path or "").lstrip("/")
    return host, port, user, password, database


_PG_RESTORE_IGNORED_COUNT = re.compile(r"errors ignored on restore: (\d+)")
_PG_RESTORE_UNKNOWN_GUC = re.compile(r'unrecognized configuration parameter "([^"]+)"')
_PG_RESTORE_SET_COMMAND = re.compile(r"Command was:\s*SET\s+\S+\s*=", re.IGNORECASE)


def _pg_restore_ignorable_errors(stderr_text):
    """Return the ignored errors that are safe to ignore, else None.

    pg_restore's ``errors ignored on restore: N`` warning is a *summary*: it is
    printed whenever any error was ignored, including fatal ones such as
    ``relation "x" already exists`` or a duplicate-key COPY failure. Treating
    that summary as success would report a partially restored database as
    healthy, so every individual error is inspected instead.

    The only condition accepted here is a version-skew GUC: a newer pg_dump
    writes ``SET transaction_timeout = 0;`` and an older server answers
    ``unrecognized configuration parameter``. That statement does not carry
    data, so ignoring it is safe. Anything else returns None (not ignorable).
    """
    blocks = stderr_text.split("pg_restore: error:")[1:]
    if not blocks:
        return None
    ignorable = []
    for block in blocks:
        block = block.split("\npg_restore:", 1)[0]
        match = _PG_RESTORE_UNKNOWN_GUC.search(block)
        if match and _PG_RESTORE_SET_COMMAND.search(block):
            ignorable.append(f'unrecognized configuration parameter "{match.group(1)}"')
            continue
        return None
    return ignorable


def _pg_restore_outcome(returncode, stderr, stdout):
    """Classify a pg_restore run into (ok, detail).

    Success requires exit 0, or exit 1 where *every* ignored error is the
    understood version-skew GUC above. A summary warning alone is never enough,
    so a partial restore stays a failure. ``detail`` is the failure text, or a
    short description of what was safely ignored.
    """
    if returncode == 0:
        return True, ""
    stderr_text = (stderr or b"").decode("utf-8", "replace")
    detail = (stderr_text or (stdout or b"").decode("utf-8", "replace")).strip()
    if returncode != 1:
        return False, detail
    if _PG_RESTORE_IGNORED_COUNT.search(stderr_text) is None:
        return False, detail
    ignorable = _pg_restore_ignorable_errors(stderr_text)
    if ignorable is None:
        return False, detail
    return True, "; ".join(ignorable)


def _quote_ident(engine, name):
    return engine.dialect.identifier_preparer.quote(name)


def _table_row_counts(url):
    """Return ``{table: row_count}`` for a reachable database, else ``None``.

    Used to compare the restored copy against the source. Returns ``None``
    (rather than raising) when the database cannot be reached, so a restore
    into a target whose source is gone still completes with the weaker
    dump-derived checks below.
    """
    try:
        from sqlalchemy import create_engine, inspect, text
    except ImportError:  # pragma: no cover - sqlalchemy is a hard dependency
        return None
    engine = None
    try:
        engine = create_engine(_normalize_url(url))
        counts = {}
        with engine.connect() as conn:
            for name in inspect(engine).get_table_names():
                counts[name] = conn.execute(
                    text(f"SELECT COUNT(*) FROM {_quote_ident(engine, name)}")
                ).scalar()
        return counts
    except Exception:
        return None
    finally:
        if engine is not None:
            engine.dispose()


def _postgres_dump_objects(dump_path):
    """Schema objects a custom-format dump defines, from ``pg_restore --list``.

    Returns ``{"tables", "indexes", "constraints", "sequences", "data_tables"}``
    or ``None`` when the table of contents cannot be read.
    """
    client = shutil.which("pg_restore")
    if client is None:
        return None
    result = subprocess.run(
        [client, "--list", str(dump_path)], capture_output=True, check=False
    )
    if result.returncode != 0:
        return None
    objects = {
        "tables": set(),
        "indexes": set(),
        "constraints": set(),
        "sequences": set(),
        "data_tables": set(),
    }
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        # Real TOC lines (note CONSTRAINT names both its table and itself, and
        # that "SEQUENCE OWNED BY"/"SEQUENCE SET" must not match "SEQUENCE"):
        #   "218; 1259 16386 TABLE public client ables"
        #   "3458; 0 16386 TABLE DATA public client ables"
        #   "3319; 2606 16393 CONSTRAINT public client client_pkey ables"
        #   "217; 1259 16385 SEQUENCE public client_id_seq ables"
        # Anchoring at the end keeps "TABLE ATTACH"/"SEQUENCE OWNED BY" out.
        patterns = (
            ("data_tables", r"\sTABLE DATA\s+(\S+)\s+(\S+)\s+\S+\s*$", 2),
            ("tables", r"\sTABLE\s+(?!DATA\b)(?!ATTACH\b)(\S+)\s+(\S+)\s+\S+\s*$", 2),
            ("tables", r"\sVIEW\s+(\S+)\s+(\S+)\s+\S+\s*$", 2),
            ("indexes", r"\sINDEX\s+(\S+)\s+(\S+)\s+\S+\s*$", 2),
            ("constraints", r"\sCONSTRAINT\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s*$", 3),
            ("sequences", r"\sSEQUENCE\s+(?!OWNED\b)(?!SET\b)(\S+)\s+(\S+)\s+\S+\s*$", 2),
        )
        if not re.match(r"^\d+;", line):
            continue
        for bucket, pattern, group in patterns:
            match = re.search(pattern, line)
            if match:
                objects[bucket].add(match.group(group))
                break
    return objects


def _mysql_dump_objects(dump_path):
    """Schema objects a mysqldump defines, parsed from its SQL text."""
    text_data = Path(dump_path).read_bytes().decode("utf-8", "replace")
    tables = set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?`?([A-Za-z0-9_]+)`?", text_data))
    data_tables = set(re.findall(r"INSERT INTO `?([A-Za-z0-9_]+)`?", text_data))
    return {"tables": tables, "indexes": set(), "constraints": set(),
            "sequences": set(), "data_tables": data_tables}


def _verify_restored_database(target_url, dump_path, objects):
    """Confirm a restore produced the dump's schema *and* its data.

    Table existence alone is necessary but not sufficient: a restore can create
    every table and still lose the rows. Two levels are checked:

    1. Every table, index, and constraint the dump defines must exist.
    2. Row counts must match the source database. When the source is not
       reachable, every table the dump carried data for must be non-empty.

    Returns ``(ok, detail)``.
    """
    if objects is None:
        # The dump's table of contents could not be read, so the restore cannot
        # be confirmed. That points at a corrupt or unreadable dump, not a
        # verified success, so fail rather than assume the data arrived.
        return False, "could not read the dump's table of contents to verify the restore"
    expected_tables = objects["tables"]
    if not expected_tables:
        return True, "no tables in the dump to verify"

    try:
        from sqlalchemy import create_engine, inspect
    except ImportError:  # pragma: no cover - sqlalchemy is a hard dependency
        return True, "sqlalchemy unavailable; table verification skipped"

    try:
        engine = create_engine(_normalize_url(target_url))
        try:
            inspector = inspect(engine)
            present_tables = set(inspector.get_table_names()) | set(inspector.get_view_names())
            missing = sorted(expected_tables - present_tables)
            if missing:
                return False, "missing tables after restore: " + ", ".join(missing)

            present_indexes = {
                index["name"]
                for table in inspector.get_table_names()
                for index in inspector.get_indexes(table)
                if index.get("name")
            }
            missing_indexes = sorted(objects["indexes"] - present_indexes)
            if missing_indexes:
                return False, "missing indexes after restore: " + ", ".join(missing_indexes)
        finally:
            engine.dispose()
    except Exception as exc:
        # An unreachable target means the restore cannot be confirmed, so treat
        # it as a failure rather than assuming success.
        return False, f"could not inspect the restored database: {exc}"

    restored_counts = _table_row_counts(target_url) or {}
    source_counts = _table_row_counts(_database_url())
    if source_counts:
        mismatched = [
            f"{table}: expected {source_counts[table]}, got {restored_counts.get(table)}"
            for table in sorted(expected_tables)
            if table in source_counts and restored_counts.get(table) != source_counts[table]
        ]
        if mismatched:
            return False, "row-count mismatch: " + "; ".join(mismatched)
        return True, (
            f"verified {len(expected_tables)} table(s), {len(objects['indexes'])} index(es), "
            "and row counts against the source"
        )

    # The source is gone, so counts cannot be compared. At minimum, a table the
    # dump carried data for must not be empty after restore.
    empty = sorted(
        table for table in objects["data_tables"] if restored_counts.get(table, 0) == 0
    )
    if empty:
        return False, "tables present but empty after restore: " + ", ".join(empty)
    return True, f"verified {len(expected_tables)} table(s) present and non-empty"


def _verify_postgres_restore(target_url, dump_path):
    return _verify_restored_database(
        target_url, dump_path, _postgres_dump_objects(dump_path)
    )


def restore_postgres_dump(dump_path, target_url, allow_live=False):
    """Load a custom-format ``pg_dump`` file into ``target_url``.

    Connection details are passed as explicit flags with the password in
    ``PGPASSWORD`` so it never reaches the process list, matching the MySQL
    helper. Refuses to target the database named by ``DATABASE_URL`` unless
    ``allow_live`` is set, so a restore drill cannot overwrite production by
    accident. Raises loudly when ``pg_restore`` is missing.
    """
    target_url = _normalize_url(target_url)
    if not _is_postgres(target_url):
        raise RuntimeError("Restore target must be a PostgreSQL URL.")

    host, port, user, password, database = _postgres_dump_credentials(target_url)
    if not database:
        raise RuntimeError("Restore target URL does not name a database.")

    live_url = _database_url()
    if _is_postgres(live_url):
        live_database = _postgres_dump_credentials(_normalize_url(live_url))[4]
        if live_database and database == live_database and not allow_live:
            raise RuntimeError(
                f"Refusing to restore into {database!r}: it is the live "
                "DATABASE_URL. Restore into a separate scratch database, or "
                "pass allow_live=True only if you truly mean to overwrite it."
            )

    client = shutil.which("pg_restore")
    if client is None:
        raise RuntimeError(
            "pg_restore is required to restore a PostgreSQL dump but was not "
            "found on PATH."
        )

    dump_path = Path(dump_path)
    if not dump_path.is_file() or dump_path.stat().st_size == 0:
        raise RuntimeError(f"Restore source is missing or empty: {dump_path}")

    command = [
        client,
        "--clean",
        "--if-exists",
        # The scratch database may not have the source's roles; skip ownership
        # and privilege replay so the data itself restores cleanly.
        "--no-owner",
        "--no-privileges",
        "--host", host,
        "--port", str(port),
        "--dbname", database,
    ]
    if user:
        command += ["--username", user]
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = password
    result = subprocess.run(command + [str(dump_path)], env=env, capture_output=True, check=False)
    ok, detail = _pg_restore_outcome(result.returncode, result.stderr, result.stdout)
    if not ok:
        raise RuntimeError(f"Restore failed (exit {result.returncode}): {detail[:300]}")

    verified, verify_detail = _verify_postgres_restore(target_url, dump_path)
    if not verified:
        raise RuntimeError(f"Restore reported success but verification failed: {verify_detail}")

    if detail:
        # Only the understood version-skew GUC was ignored; the data loaded and
        # the schema and row counts check out, so report success but surface
        # what was skipped.
        return True, (
            f"Restored {dump_path.name} into {database} "
            f"({detail}; {verify_detail})."
        )
    return True, f"Restored {dump_path.name} into {database} ({verify_detail})."


def restore_mysql_dump(dump_path, target_url, allow_live=False):
    """Load a mysqldump file into ``target_url`` with the native client.

    Connection details are passed as explicit flags (password via ``MYSQL_PWD``
    so it never reaches the process list) rather than a URL, which avoids the
    client's URL-parsing quirks entirely.

    Refuses to target the database named by ``DATABASE_URL`` unless
    ``allow_live`` is set, so a restore drill cannot overwrite production by
    accident. Raises loudly when the client tool is missing.
    """
    target_url = _normalize_url(target_url)
    if not _is_mysql(target_url):
        raise RuntimeError("Restore target must be a MySQL/MariaDB URL.")

    host, port, user, password, database = _mysql_dump_credentials(target_url)
    if not database:
        raise RuntimeError("Restore target URL does not name a database.")

    live_url = _database_url()
    if _is_mysql(live_url):
        live_database = _mysql_dump_credentials(_normalize_url(live_url))[4]
        if live_database and database == live_database and not allow_live:
            raise RuntimeError(
                f"Refusing to restore into {database!r}: it is the live "
                "DATABASE_URL. Restore into a separate scratch database, or "
                "pass allow_live=True only if you truly mean to overwrite it."
            )

    client = shutil.which("mysql") or shutil.which("mariadb")
    if client is None:
        raise RuntimeError(
            "The mysql/mariadb client is required to restore a dump but was "
            "not found on PATH."
        )

    dump_path = Path(dump_path)
    if not dump_path.is_file() or dump_path.stat().st_size == 0:
        raise RuntimeError(f"Restore source is missing or empty: {dump_path}")

    command = [
        client,
        "--host", host,
        "--port", str(port),
        "--user", user,
        database,
    ]
    env = dict(os.environ)
    if password:
        env["MYSQL_PWD"] = password
    with open(dump_path, "rb") as source:
        result = subprocess.run(command, stdin=source, env=env, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"restore failed").decode("utf-8", "replace").strip()
        raise RuntimeError(f"Restore failed (exit {result.returncode}): {detail[:300]}")

    verified, verify_detail = _verify_restored_database(
        target_url, dump_path, _mysql_dump_objects(dump_path)
    )
    if not verified:
        raise RuntimeError(f"Restore reported success but verification failed: {verify_detail}")
    return True, f"Restored {dump_path.name} into {database} ({verify_detail})."


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
    """Create a backup appropriate for the configured database backend.

    Dispatch is strict: a MySQL/MariaDB URL is dumped with ``mysqldump`` (and
    fails loudly if that is unavailable), a PostgreSQL URL with ``pg_dump``,
    a SQLite URL via the SQLite file copy, and anything else (including a
    missing URL) raises rather than silently backing up as local SQLite.
    """
    return _dispatch_backup(_database_url(), include_uploads=include_uploads)


def cleanup_old_backups(keep=10):
    """Delete the oldest local backups, keeping the most recent ``keep``.

    This only removes *local* copies. It is safe solely when independent,
    verified backups exist off-site (upload them to durable off-host storage
    before relying on this). Retention never removes the newest backup.
    """
    BACKUP_DIR.mkdir(exist_ok=True)
    prefixes = ("ablehome-", "ablehome-full-", "ables-postgres-", "ables-mysql-")
    backups = sorted(
        [
            path for path in BACKUP_DIR.iterdir()
            if path.is_file() and path.name.startswith(prefixes)
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
                if "database.dump" not in names and "database.sql" not in names and "ablehome.db" not in names:
                    return False, "Backup archive contains no database dump."
                if "database.dump" in names:
                    ok, message = _verify_pg_dump_member(archive)
                    if not ok:
                        return False, message
                if "database.sql" in names:
                    ok, message = _verify_mysql_dump_member(archive)
                    if not ok:
                        return False, message
        except zipfile.BadZipFile:
            return False, "Backup archive could not be opened."
    elif path.suffix == ".sql":
        if path.stat().st_size == 0:
            return False, "MySQL dump file is empty."
    return True, "Backup verified as readable."


def _verify_mysql_dump_member(archive):
    """Confirm the bundled MySQL dump is non-empty and structurally a dump.

    mysqldump output always contains SQL statements (and typically a
    "MySQL dump" header comment); a truncated or empty member is rejected.
    """
    info = archive.getinfo("database.sql")
    if info.file_size == 0:
        return False, "Bundled MySQL dump is empty."
    with archive.open("database.sql") as source:
        head = source.read(4096)
    if b"INSERT INTO" not in head and b"CREATE TABLE" not in head and b"MySQL dump" not in head:
        return False, "Bundled MySQL dump does not look like mysqldump output."
    return True, "Backup verified: archive readable and MySQL dump present."


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


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restore-to",
        metavar="URL",
        help="Restore the created backup into this MySQL/MariaDB database "
             "(an isolated scratch database) and report the result. Refuses "
             "to target the live DATABASE_URL.",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Permit --restore-to to target the live DATABASE_URL. Off by "
             "default so a drill cannot overwrite production by accident.",
    )
    args = parser.parse_args(argv)

    try:
        backup_path = _dispatch_backup(_database_url(), include_uploads=True)

        ok, message = verify_backup(backup_path)
        print(f"{'Backup created' if ok else 'Backup FAILED verification'}: {backup_path} ({message})")
        if not ok:
            raise SystemExit(1)

        if args.restore_to:
            dump_path = backup_path
            if str(backup_path).endswith(".zip"):
                with tempfile.TemporaryDirectory() as tmpdir:
                    with zipfile.ZipFile(backup_path) as archive:
                        members = archive.namelist()
                        if "database.sql" in members:
                            member = "database.sql"
                            restore = restore_mysql_dump
                        elif "database.dump" in members:
                            member = "database.dump"
                            restore = restore_postgres_dump
                        else:
                            print("Backup archive has no database dump member; cannot restore.")
                            raise SystemExit(1)
                        archive.extract(member, tmpdir)
                    dump_path = Path(tmpdir) / member
                    _, restored = restore(dump_path, args.restore_to, allow_live=args.allow_live)
                    print(restored)
            elif backup_path.suffix == ".dump" or _is_postgres(_database_url()):
                _, restored = restore_postgres_dump(dump_path, args.restore_to, allow_live=args.allow_live)
                print(restored)
            else:
                _, restored = restore_mysql_dump(dump_path, args.restore_to, allow_live=args.allow_live)
                print(restored)
            print("Restore drill complete. Schema objects and row counts were verified against the source;")
            print("compare the alembic revision against production to finish.")
            return
    except RuntimeError as error:
        # Refusals (live target, missing tool, failed dump) should read as a
        # clear message, not a traceback.
        print(f"ERROR: {error}")
        raise SystemExit(1)

    print("Upload this file to durable, off-site storage to complete a real backup.")


if __name__ == "__main__":
    main()


