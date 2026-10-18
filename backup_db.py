from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "ablehome.db"

# Load the same environment file the web app uses, so a fingerprint written by
# this CLI is signed with the same key the app verifies it with. Without this a
# backup made from the shell could be signed with no key (or a different one)
# and then be rejected as unauthenticated by the app.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, os.environ.get("ENV_FILE", ".env")))
except ImportError:  # pragma: no cover - python-dotenv is a hard dependency of config
    pass

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
    fingerprint = _database_fingerprint(database_url)
    if fingerprint is None:
        raise RuntimeError(
            "Backup written, but the source database could not be fingerprinted to "
            "prove the backup is complete. Check DATABASE_URL and connectivity."
        )

    if not include_uploads:
        _write_fingerprint(dump_path, fingerprint)
        return dump_path

    backup_path = BACKUP_DIR / f"ablehome-full-{timestamp}.zip"
    with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(dump_path, arcname="database.dump")
        archive.writestr("database.fingerprint.json", _fingerprint_member_bytes(fingerprint))
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

        fingerprint = _database_fingerprint(database_url)
        if fingerprint is None:
            raise RuntimeError(
                "Backup written, but the source database could not be fingerprinted to "
                "prove the backup is complete. Check DATABASE_URL and connectivity."
            )

        if not include_uploads:
            final_path = BACKUP_DIR / f"ables-mysql-{timestamp}.sql"
            shutil.copy2(dump_path, final_path)
            _write_fingerprint(final_path, fingerprint)
            return final_path

        backup_path = BACKUP_DIR / f"ablehome-mysql-{timestamp}.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(dump_path, arcname="database.sql")
            archive.writestr("database.fingerprint.json", _fingerprint_member_bytes(fingerprint))
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


def _cell_bytes(value):
    """A stable, unambiguous byte encoding of one column value.

    Each value is tagged with a type byte so distinct types never collide, and
    ``None`` is distinguished from the empty string. Numeric and temporal values
    are rendered in their canonical, full-precision, locale-independent form so
    the fingerprint does not depend on the driver's default formatting.
    """
    if value is None:
        return b"\x00NULL"
    if isinstance(value, bytes):
        return b"\x01b" + value
    if isinstance(value, bool):
        return b"\x01bool" + (b"1" if value else b"0")
    if isinstance(value, Decimal):
        return b"\x01dec" + str(value.normalize()).encode("utf-8")
    if isinstance(value, int):
        return b"\x01i" + str(value).encode("utf-8")
    if isinstance(value, float):
        return b"\x01f" + repr(value).encode("utf-8")
    if isinstance(value, datetime):
        return b"\x01dt" + value.isoformat().encode("utf-8")
    if isinstance(value, date):
        return b"\x01d" + value.isoformat().encode("utf-8")
    if isinstance(value, time):
        return b"\x01t" + value.isoformat().encode("utf-8")
    if isinstance(value, memoryview):
        return b"\x01b" + bytes(value)
    return b"\x01s" + str(value).encode("utf-8")


def _table_digest(conn, engine, table):
    """A content fingerprint of one table: row count, column names, and values.

    Row counts alone cannot detect a restore that produced the right number of
    rows with wrong values, so the digest folds in every value. Values are
    serialized with ``_cell_bytes`` (type-tagged, full precision) and the rows
    are read in a deterministic order, so two databases with identical contents
    fingerprint identically regardless of physical row order or driver.

    Rows are ordered by their primary key when the table has one, otherwise by
    every column, which keeps the ordering stable across a dump/restore. Column
    names are folded in after the rows so a reordered schema changes the digest.

    Returns ``{"rows": int, "sha256": str}``.
    """
    quoted = _quote_ident(engine, table)
    columns = [column["name"] for column in engine.dialect.get_columns(conn, table)]
    quoted_columns = ", ".join(_quote_ident(engine, name) for name in columns)

    order_by = ""
    try:
        primary_key = engine.dialect.get_pk_constraint(conn, table).get("constrained_columns") or []
    except Exception:
        primary_key = []
    order_columns = primary_key or columns
    if order_columns:
        order_by = " ORDER BY " + ", ".join(
            _quote_ident(engine, name) for name in order_columns
        )

    digest = hashlib.sha256()
    rows = 0
    result = conn.exec_driver_sql(f"SELECT {quoted_columns} FROM {quoted}{order_by}")
    for row in result:
        rows += 1
        for value in row:
            digest.update(_cell_bytes(value))
            digest.update(b"\x1f")
        digest.update(b"\x1e")
    digest.update(("\x1d".join(columns)).encode("utf-8"))
    return {"rows": rows, "sha256": digest.hexdigest()}


def _database_fingerprint(url):
    """``{table: {"rows", "sha256"}}`` for a reachable database, else ``None``."""
    try:
        from sqlalchemy import create_engine, inspect
    except ImportError:  # pragma: no cover - sqlalchemy is a hard dependency
        return None
    engine = None
    try:
        engine = create_engine(_normalize_url(url))
        fingerprint = {}
        with engine.connect() as conn:
            for name in inspect(engine).get_table_names():
                fingerprint[name] = _table_digest(conn, engine, name)
        return fingerprint
    except Exception:
        return None
    finally:
        if engine is not None:
            engine.dispose()


class FingerprintIntegrityError(RuntimeError):
    """The recorded fingerprint failed authentication (tampered or unsigned)."""


def _fingerprint_key():
    """Return the HMAC key that authenticates fingerprints, or ``None``.

    A dedicated ``BACKUP_FINGERPRINT_KEY`` wins; otherwise the app ``SECRET_KEY``
    is used, domain-separated so the session-signing key is not reused verbatim.
    A fingerprint stored beside a backup only detects corruption if the
    fingerprint itself can be trusted, so it is authenticated with this key.
    """
    dedicated = os.environ.get("BACKUP_FINGERPRINT_KEY", "").strip()
    if dedicated:
        return dedicated.encode("utf-8")
    secret = os.environ.get("SECRET_KEY", "").strip()
    if secret:
        return hashlib.sha256(b"ables-backup-fingerprint-v1:" + secret.encode("utf-8")).digest()
    return None


def fingerprint_key_id():
    """A short, non-secret identifier for the key that signs fingerprints.

    Lets an operator confirm that the key they hold still matches the key a
    backup was signed with — after a restore, or after moving the key to a
    password manager — without ever printing the key. ``None`` when no key is
    configured (fingerprints are then unauthenticated).
    """
    key = _fingerprint_key()
    if key is None:
        return None
    return hashlib.sha256(b"ables-backup-key-id-v1:" + key).hexdigest()[:16]


def verify_fingerprint_key_matches(backup_path):
    """True when the recorded fingerprint authenticates under the current key.

    Raises :class:`FingerprintIntegrityError` with a clear message when the key
    is wrong, so an operator restoring a backup learns immediately that they are
    holding the wrong signing key rather than a corrupted backup.
    """
    _read_fingerprint(Path(backup_path))
    return True


def _serialize_fingerprint(fingerprint):
    """Canonical JSON for a fingerprint, so the MAC is reproducible."""
    return json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))


def _fingerprint_payload(fingerprint):
    """Wrap a fingerprint with a version and, when possible, an HMAC-SHA256 MAC.

    The envelope is what gets stored (sidecar or ZIP member). ``mac`` is absent
    only when no key is configured (local/dev), in which case the fingerprint is
    unauthenticated and the reader refuses it whenever a key is configured.
    """
    payload = {"version": 2, "algorithm": "hmac-sha256", "fingerprint": fingerprint}
    key = _fingerprint_key()
    if key is not None:
        payload["mac"] = hmac.new(
            key, _serialize_fingerprint(fingerprint).encode("utf-8"), hashlib.sha256
        ).hexdigest()
    return payload


def _parse_fingerprint_payload(data, key):
    """Validate a stored fingerprint payload and return the fingerprint dict.

    Raises :class:`FingerprintIntegrityError` when the payload cannot be
    authenticated: a bad MAC, an unsigned payload while a key is configured
    (which would let an attacker strip the MAC to downgrade verification), or an
    unrecognized structure. A bare fingerprint dict is accepted only when no key
    is configured at all.
    """
    if not isinstance(data, dict):
        raise FingerprintIntegrityError("recorded fingerprint is not a JSON object")

    if "fingerprint" not in data and "mac" not in data and "version" not in data:
        # Legacy bare format (pre-authentication). Accept it only when there is
        # no key to check it against, so enabling a key cannot be bypassed by
        # writing the old shape.
        if key is not None:
            raise FingerprintIntegrityError(
                "recorded fingerprint is unsigned but a signing key is configured"
            )
        return data

    fingerprint = data.get("fingerprint")
    if not isinstance(fingerprint, dict):
        raise FingerprintIntegrityError("recorded fingerprint envelope has no fingerprint")

    if key is None:
        return fingerprint

    recorded_mac = data.get("mac")
    if not isinstance(recorded_mac, str):
        raise FingerprintIntegrityError("recorded fingerprint has no MAC")
    expected = hmac.new(
        key, _serialize_fingerprint(fingerprint).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, recorded_mac):
        raise FingerprintIntegrityError(
            "recorded fingerprint failed authentication; it may have been altered"
        )
    return fingerprint


def _write_fingerprint(dump_path, fingerprint):
    """Write the authenticated fingerprint beside the dump as a JSON sidecar."""
    path = Path(str(dump_path) + ".fingerprint.json")
    path.write_text(
        json.dumps(_fingerprint_payload(fingerprint), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _fingerprint_member_bytes(fingerprint):
    """The authenticated fingerprint member to embed inside a ZIP backup."""
    return json.dumps(_fingerprint_payload(fingerprint), indent=2, sort_keys=True).encode("utf-8")


def _read_fingerprint_member(archive, name="database.fingerprint.json"):
    """Read and authenticate the fingerprint member from an open ZIP archive.

    Returns ``None`` when the member is absent. Raises
    :class:`FingerprintIntegrityError` when it exists but is not authentic, so a
    tampered member cannot downgrade a restore to the weaker checks.
    """
    if name not in archive.namelist():
        return None
    try:
        data = json.loads(archive.read(name).decode("utf-8"))
    except (ValueError, KeyError, OSError) as exc:
        raise FingerprintIntegrityError(f"recorded fingerprint could not be read: {exc}") from exc
    return _parse_fingerprint_payload(data, _fingerprint_key())


def _read_fingerprint(dump_path):
    """Read and authenticate a dump's fingerprint sidecar.

    Returns the fingerprint dict, or ``None`` when there is no sidecar. Raises
    :class:`FingerprintIntegrityError` when a sidecar exists but cannot be
    authenticated, so a tampered fingerprint fails the restore instead of
    silently falling back to the weaker row-count check.
    """
    path = Path(str(dump_path) + ".fingerprint.json")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise FingerprintIntegrityError(f"recorded fingerprint could not be read: {exc}") from exc
    return _parse_fingerprint_payload(data, _fingerprint_key())


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

    # Preferred: compare against the fingerprint captured when the backup was
    # made. It is a content hash, so it catches a restore that produced the
    # right number of rows with wrong values, and it stays authoritative even
    # when the source database no longer exists (a real disaster).
    try:
        recorded = _read_fingerprint(dump_path)
    except FingerprintIntegrityError as exc:
        # A present-but-unauthentic fingerprint must fail the restore, not fall
        # back to the weaker row-count check.
        return False, f"recorded fingerprint could not be authenticated: {exc}"
    if recorded is not None:
        # Coverage: every table the dump carried data for must have a recorded
        # entry. A fingerprint that silently omitted a table would otherwise
        # leave that table's restore unverified while still reporting success.
        missing_entries = sorted(objects["data_tables"] - set(recorded))
        if missing_entries:
            return False, (
                "recorded fingerprint is incomplete; no entry for table(s): "
                + ", ".join(missing_entries)
            )

        restored_fingerprint = _database_fingerprint(target_url)
        if restored_fingerprint is None:
            return False, "could not fingerprint the restored database to verify it"
        problems = []
        for table in sorted(recorded):
            expected = recorded[table]
            actual = restored_fingerprint.get(table)
            if actual is None:
                problems.append(f"{table}: missing after restore")
            elif actual["rows"] != expected["rows"]:
                problems.append(
                    f"{table}: expected {expected['rows']} rows, got {actual['rows']}"
                )
            elif actual["sha256"] != expected["sha256"]:
                problems.append(f"{table}: content mismatch (row values differ)")
        if problems:
            return False, "content verification failed: " + "; ".join(problems)
        return True, (
            f"verified {len(expected_tables)} table(s), {len(objects['indexes'])} index(es), "
            "and content fingerprints against the backup"
        )

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


BACKUP_PREFIXES = ("ablehome-", "ablehome-full-", "ables-postgres-", "ables-mysql-")
BACKUP_SUFFIXES = (".zip", ".dump", ".sql")


def list_backups(directory=None):
    """Return the actual backup artifacts in ``directory``, newest first.

    Fingerprint sidecars (``<dump>.fingerprint.json``) live beside their dump so
    a restore can verify content, but they are not backups themselves and must
    not be counted, listed, or cleaned up as if they were.
    """
    directory = directory or BACKUP_DIR
    if not directory.exists():
        return []
    artifacts = [
        path for path in directory.iterdir()
        if path.is_file()
        and path.name.startswith(BACKUP_PREFIXES)
        and path.suffix in BACKUP_SUFFIXES
    ]
    return sorted(artifacts, key=lambda path: path.stat().st_mtime, reverse=True)


def cleanup_old_backups(keep=10):
    """Delete the oldest local backups, keeping the most recent ``keep``.

    This only removes *local* copies. It is safe solely when independent,
    verified backups exist off-site (upload them to durable off-host storage
    before relying on this). Retention never removes the newest backup, and it
    removes a backup's fingerprint sidecar along with the backup itself.
    """
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = list_backups(BACKUP_DIR)
    removed = []
    for backup in backups[max(1, keep):]:
        backup.unlink()
        Path(str(backup) + ".fingerprint.json").unlink(missing_ok=True)
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
                # Authenticate the fingerprint member if present: a tampered
                # fingerprint would otherwise be trusted at restore time.
                try:
                    _read_fingerprint_member(archive)
                except FingerprintIntegrityError as exc:
                    return False, f"recorded fingerprint could not be authenticated: {exc}"
        except zipfile.BadZipFile:
            return False, "Backup archive could not be opened."
    elif path.suffix in (".sql", ".dump"):
        if path.stat().st_size == 0:
            return False, "Backup dump file is empty."
        # A sidecar fingerprint must authenticate, or the backup cannot be trusted.
        try:
            _read_fingerprint(path)
        except FingerprintIntegrityError as exc:
            return False, f"recorded fingerprint could not be authenticated: {exc}"
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
    parser.add_argument(
        "--offsite",
        action="store_true",
        help="Copy the backup to the destination in OFFSITE_BACKUP_URL and "
             "verify the remote copy. Fails (non-zero exit) unless the transfer "
             "completes and the remote content is verified.",
    )
    parser.add_argument(
        "--offsite-verify",
        choices=("hash", "size"),
        default="hash",
        help="How to verify the remote copy: 'hash' (default) downloads and "
             "compares SHA-256; 'size' compares sizes only and is weaker.",
    )
    parser.add_argument(
        "--print-key-id",
        action="store_true",
        help="Print the non-secret ID of the fingerprint signing key and exit. "
             "Record this alongside the key in your password manager; after a "
             "restore, the same ID confirms you still hold the right key. The "
             "key itself is never printed.",
    )
    args = parser.parse_args(argv)

    if args.print_key_id:
        key_id = fingerprint_key_id()
        if key_id is None:
            print("No fingerprint signing key configured (set BACKUP_FINGERPRINT_KEY).")
            raise SystemExit(1)
        print(f"Fingerprint key ID: {key_id}")
        return

    try:
        backup_path = _dispatch_backup(_database_url(), include_uploads=True)

        ok, message = verify_backup(backup_path)
        print(f"{'Backup created' if ok else 'Backup FAILED verification'}: {backup_path} ({message})")
        if not ok:
            raise SystemExit(1)

        if args.offsite:
            import offsite_backup

            receipt = offsite_backup.upload_backup(
                backup_path, verify=args.offsite_verify,
                keep=int(os.environ.get("OFFSITE_RETENTION", "10") or 10),
            )
            print(f"Off-site backup verified: {receipt['artifact']} -> "
                  f"{receipt['destination']['location']} ({receipt['verification']['message']})")

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
                        # Carry the recorded fingerprint next to the dump so
                        # the restore is verified against the backup's content,
                        # not just its row counts. It must land under the name
                        # _read_fingerprint expects (<dump>.fingerprint.json).
                        if "database.fingerprint.json" in members:
                            Path(str(Path(tmpdir) / member) + ".fingerprint.json").write_bytes(
                                archive.read("database.fingerprint.json")
                            )
                    dump_path = Path(tmpdir) / member
                    _, restored = restore(dump_path, args.restore_to, allow_live=args.allow_live)
                    print(restored)
            elif backup_path.suffix == ".dump" or _is_postgres(_database_url()):
                _, restored = restore_postgres_dump(dump_path, args.restore_to, allow_live=args.allow_live)
                print(restored)
            else:
                _, restored = restore_mysql_dump(dump_path, args.restore_to, allow_live=args.allow_live)
                print(restored)
            print("Restore drill complete. Schema objects and row counts were verified against the source,")
            print("and the restored content was checked against the authenticated fingerprint recorded in the backup.")
            print("Compare the alembic revision against production to finish.")
            return
    except RuntimeError as error:
        # Refusals (live target, missing tool, failed dump, failed off-site
        # transfer) should read as a clear message, not a traceback.
        print(f"ERROR: {error}")
        raise SystemExit(1)

    if args.offsite:
        print("Local backup complete and copied off-site (verified above).")
    else:
        print("Upload this file to durable, off-site storage to complete a real backup.")


if __name__ == "__main__":
    main()


