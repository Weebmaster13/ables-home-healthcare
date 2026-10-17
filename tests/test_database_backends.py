"""Regression tests for database backend selection and backups.

Covers URL normalization, driver/tool detection, strict backend dispatch,
MySQL/MariaDB dump handling, and the guarded restore helper. Most tests use
deterministic fakes for ``subprocess``/``shutil.which``; two optional live
tests (connection and a full backup/restore round trip) skip unless a
MariaDB/MySQL URL is provided via ``MYSQL_TEST_URL``.
"""
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import backup_db
import config
from app import app
from production_check import database_backup_tool_available, database_driver_available


def _write_dump(command):
    """Return a fake mysqldump that writes plausible SQL to --result-file."""
    result_file = Path(command[command.index("--result-file") + 1])
    result_file.write_bytes(b"-- MySQL dump 10.13\nCREATE TABLE t (id INT);\nINSERT INTO t VALUES (1);\n")


def test_mysql_url_is_normalized_to_pymysql():
    os.environ["DATABASE_URL"] = "mysql://user:pass@localhost:3306/ables"
    try:
        assert config.database_url() == "mysql+pymysql://user:pass@localhost:3306/ables"
    finally:
        os.environ.pop("DATABASE_URL", None)


def test_explicit_pymysql_url_is_left_unchanged():
    os.environ["DATABASE_URL"] = "mysql+pymysql://user:pass@localhost/ables"
    try:
        assert config.database_url() == "mysql+pymysql://user:pass@localhost/ables"
    finally:
        os.environ.pop("DATABASE_URL", None)


def test_postgres_url_normalization_is_preserved():
    os.environ["DATABASE_URL"] = "postgres://user:pass@host:5432/db"
    try:
        assert config.database_url() == "postgresql://user:pass@host:5432/db"
    finally:
        os.environ.pop("DATABASE_URL", None)


@pytest.mark.parametrize(
    "url,is_pg,is_mysql",
    [
        ("postgresql://u:p@h/db", True, False),
        ("postgres://u:p@h/db", True, False),
        ("mysql://u:p@h/db", False, True),
        ("mysql+pymysql://u:p@h/db", False, True),
        ("mariadb://u:p@h/db", False, True),
        ("sqlite:///instance/ablehome.db", False, False),
    ],
)
def test_backend_detection(url, is_pg, is_mysql):
    assert backup_db._is_postgres(url) is is_pg
    assert backup_db._is_mysql(url) is is_mysql


def test_client_url_strips_driver_suffix_for_native_clients():
    """The native client rejects `mysql+pymysql://`; the helper must rewrite it."""
    assert backup_db.client_url("mysql+pymysql://u:p@h:3306/db") == "mysql://u:p@h:3306/db"
    # A bare URL is already client-safe and must be left alone.
    assert backup_db.client_url("mysql://u:p@h:3306/db") == "mysql://u:p@h:3306/db"
    assert backup_db.client_url("mariadb://u:p@h/db") == "mysql://u:p@h/db"
    # pg_restore understands the SQLAlchemy scheme, so postgres is untouched.
    assert backup_db.client_url("postgresql://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert backup_db.client_url("postgres://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"


def test_restore_refuses_to_target_the_live_database(monkeypatch, tmp_path):
    """A restore drill must not be able to overwrite production by accident."""
    dump = tmp_path / "database.sql"
    dump.write_bytes(b"-- MySQL dump 10.13\nCREATE TABLE t (id INT);\n")
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@dbhost:3306/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/mysql")

    with pytest.raises(RuntimeError, match="Refusing to restore"):
        backup_db.restore_mysql_dump(dump, "mysql+pymysql://u:p@dbhost:3306/ables")


def test_restore_passes_credentials_via_env_not_argv(monkeypatch, tmp_path):
    """The password must not appear in the process list during a restore."""
    captured = {}

    def fake_run(cmd, stdin=None, env=None, capture_output=True, check=False):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["stdin_read"] = stdin.read()
        return backup_db.subprocess.CompletedProcess(cmd, 0, b"", b"")

    dump = tmp_path / "database.sql"
    dump.write_bytes(b"-- MySQL dump 10.13\nCREATE TABLE t (id INT);\n")
    monkeypatch.setenv("DATABASE_URL", "mysql://u:livepw@dbhost:3306/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/mysql")
    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)
    # This test is about credential handling, not the post-restore inspection.
    monkeypatch.setattr(
        backup_db, "_verify_restored_database", lambda url, dump_path, objects: (True, "verified")
    )

    ok, message = backup_db.restore_mysql_dump(
        dump, "mysql+pymysql://restorer:scratchpw@dbhost:3306/ables_restore"
    )

    assert ok is True
    assert "scratchpw" not in " ".join(captured["cmd"])
    assert captured["env"]["MYSQL_PWD"] == "scratchpw"
    assert "ables_restore" in captured["cmd"]
    # The dump file is streamed to the client on stdin.
    assert captured["stdin_read"].startswith(b"-- MySQL dump")


def test_restore_raises_when_client_tool_is_missing(monkeypatch, tmp_path):
    dump = tmp_path / "database.sql"
    dump.write_bytes(b"-- MySQL dump 10.13\n")
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@dbhost:3306/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="mysql/mariadb client"):
        backup_db.restore_mysql_dump(dump, "mysql+pymysql://u:p@dbhost:3306/scratch")


def test_restore_raises_on_empty_dump(monkeypatch, tmp_path):
    dump = tmp_path / "database.sql"
    dump.write_bytes(b"")
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@dbhost:3306/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/mysql")

    with pytest.raises(RuntimeError, match="missing or empty"):
        backup_db.restore_mysql_dump(dump, "mysql+pymysql://u:p@dbhost:3306/scratch")


def test_postgres_restore_refuses_to_target_the_live_database(monkeypatch, tmp_path):
    """The PostgreSQL drill must not be able to overwrite production either."""
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"PGDMP fake")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dbhost:5432/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/pg_restore")

    with pytest.raises(RuntimeError, match="Refusing to restore"):
        backup_db.restore_postgres_dump(dump, "postgres://u:p@dbhost:5432/ables")


def test_postgres_restore_passes_password_via_env_not_argv(monkeypatch, tmp_path):
    """pg_restore must get PGPASSWORD in the environment, not on the command line."""
    captured = {}

    def fake_run(cmd, env=None, capture_output=True, check=False):
        if "--list" in cmd:
            # The post-restore verification lists the TOC first; only the
            # actual restore invocation carries the connection flags.
            return backup_db.subprocess.CompletedProcess(cmd, 0, b"", b"")
        captured["cmd"] = cmd
        captured["env"] = env
        return backup_db.subprocess.CompletedProcess(cmd, 0, b"", b"")

    dump = tmp_path / "database.dump"
    dump.write_bytes(b"PGDMP fake")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:livepw@dbhost:5432/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/pg_restore")
    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)
    monkeypatch.setattr(
        backup_db, "_verify_postgres_restore", lambda url, dump_path: (True, "verified")
    )

    ok, message = backup_db.restore_postgres_dump(
        dump, "postgresql://restorer:scratchpw@dbhost:5432/ables_restore"
    )

    assert ok is True
    assert "scratchpw" not in " ".join(captured["cmd"])
    assert captured["env"]["PGPASSWORD"] == "scratchpw"
    assert "ables_restore" in captured["cmd"]
    assert str(dump) in captured["cmd"]


def _fake_pg_restore(monkeypatch, tmp_path, stderr, returncode=1, tables=("client",)):
    """Patch pg_restore so restore_postgres_dump sees ``stderr``.

    The TOC listing returns ``tables`` so the post-restore verification can run
    against a patched inspector. Returns the dump path.
    """
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"PGDMP fake")

    def fake_run(cmd, env=None, capture_output=True, check=False):
        if "--list" in cmd:
            toc = "".join(
                f"218; 1259 {16386 + i} TABLE public {name} ables\n"
                for i, name in enumerate(tables)
            )
            return backup_db.subprocess.CompletedProcess(cmd, 0, toc.encode(), b"")
        return backup_db.subprocess.CompletedProcess(cmd, returncode, b"", stderr)

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dbhost:5432/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/pg_restore")
    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)
    monkeypatch.setattr(
        backup_db, "_verify_postgres_restore", lambda url, dump_path: (True, "verified")
    )
    return dump


def test_postgres_restore_accepts_only_a_version_skew_guc(monkeypatch, tmp_path):
    """The one understood benign error: a newer pg_dump's SET of a GUC that an
    older server does not know. It carries no data, so it is safe to ignore."""
    dump = _fake_pg_restore(
        monkeypatch, tmp_path,
        b'pg_restore: error: could not execute query: ERROR:  '
        b'unrecognized configuration parameter "transaction_timeout"\n'
        b'Command was: SET transaction_timeout = 0;\n'
        b'pg_restore: warning: errors ignored on restore: 1\n',
    )

    ok, message = backup_db.restore_postgres_dump(
        dump, "postgresql://u:p@dbhost:5432/ables_restore"
    )

    assert ok is True
    assert 'unrecognized configuration parameter "transaction_timeout"' in message
    assert "errors ignored" not in message


def test_postgres_restore_rejects_ignored_errors_that_lost_data(monkeypatch, tmp_path):
    """A summary warning alone is NOT success. Real errors such as a table that
    already exists, or a COPY that hit a duplicate key, mean data was lost, so
    the drill must fail even though pg_restore printed the same summary line."""
    dump = _fake_pg_restore(
        monkeypatch, tmp_path,
        b'pg_restore: error: could not execute query: ERROR:  '
        b'relation "client" already exists\n'
        b'Command was: CREATE TABLE public.client (\n    id integer NOT NULL\n);\n'
        b'pg_restore: error: COPY failed for table "client": ERROR:  duplicate '
        b'key value violates unique constraint "client_pkey"\n'
        b'pg_restore: warning: errors ignored on restore: 2\n',
    )

    with pytest.raises(RuntimeError, match="Restore failed"):
        backup_db.restore_postgres_dump(
            dump, "postgresql://u:p@dbhost:5432/ables_restore"
        )


def test_postgres_restore_rejects_mixed_guc_and_real_error(monkeypatch, tmp_path):
    """One ignorable GUC does not excuse a second, data-losing error."""
    dump = _fake_pg_restore(
        monkeypatch, tmp_path,
        b'pg_restore: error: could not execute query: ERROR:  '
        b'unrecognized configuration parameter "transaction_timeout"\n'
        b'Command was: SET transaction_timeout = 0;\n'
        b'pg_restore: error: could not execute query: ERROR:  '
        b'relation "client" already exists\n'
        b'Command was: CREATE TABLE public.client (\n    id integer NOT NULL\n);\n'
        b'pg_restore: warning: errors ignored on restore: 2\n',
    )

    with pytest.raises(RuntimeError, match="Restore failed"):
        backup_db.restore_postgres_dump(
            dump, "postgresql://u:p@dbhost:5432/ables_restore"
        )


def test_postgres_restore_raises_when_a_table_is_missing_after_restore(monkeypatch, tmp_path):
    """Exit 0 is not proof on its own: the dumped tables must actually exist."""
    dump = _fake_pg_restore(monkeypatch, tmp_path, b"", returncode=0, tables=("client",))
    monkeypatch.setattr(
        backup_db, "_verify_postgres_restore",
        lambda url, dump_path: (False, "missing tables after restore: client"),
    )

    with pytest.raises(RuntimeError, match="verification failed"):
        backup_db.restore_postgres_dump(
            dump, "postgresql://u:p@dbhost:5432/ables_restore"
        )


def test_postgres_restore_still_raises_on_a_real_failure(monkeypatch, tmp_path):
    """Exit 1 without the ignored-errors summary is a genuine failure."""
    dump = _fake_pg_restore(
        monkeypatch, tmp_path, b"pg_restore: error: connection to server failed\n"
    )

    with pytest.raises(RuntimeError, match="Restore failed"):
        backup_db.restore_postgres_dump(
            dump, "postgresql://u:p@dbhost:5432/ables_restore"
        )


def test_postgres_restore_raises_when_pg_restore_missing(monkeypatch, tmp_path):
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"PGDMP fake")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dbhost:5432/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="pg_restore"):
        backup_db.restore_postgres_dump(dump, "postgresql://u:p@dbhost:5432/scratch")


def test_postgres_restore_raises_on_empty_dump(monkeypatch, tmp_path):
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dbhost:5432/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/pg_restore")

    with pytest.raises(RuntimeError, match="missing or empty"):
        backup_db.restore_postgres_dump(dump, "postgresql://u:p@dbhost:5432/scratch")


def test_restore_to_dispatch_handles_a_postgres_bundle(monkeypatch, tmp_path):
    """`backup_db.py --restore-to` must pick pg_restore for a postgres bundle.

    The documented drill was MySQL-only before: a PostgreSQL URL reached
    restore_mysql_dump and failed with "target must be a MySQL/MariaDB URL".
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dbhost:5432/ables")
    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path)

    def fake_dispatch(url, include_uploads=True):
        bundle = tmp_path / "ablehome-full-test.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("database.dump", b"PGDMP fake")
        return bundle

    calls = {}

    def fake_pg_restore(dump_path, target_url, allow_live=False):
        calls["restored"] = Path(dump_path).name
        return True, "Restored database.dump into ables_restore."

    monkeypatch.setattr(backup_db, "_dispatch_backup", fake_dispatch)
    monkeypatch.setattr(backup_db, "verify_backup", lambda path: (True, "verified"))
    monkeypatch.setattr(backup_db, "restore_postgres_dump", fake_pg_restore)

    backup_db.main(["--restore-to", "postgresql://u:p@dbhost:5432/ables_restore"])

    assert calls["restored"] == "database.dump"


def test_dump_flags_skip_column_statistics_for_mariadb_client(monkeypatch):
    """The MariaDB client rejects --column-statistics; MySQL 8 needs it."""

    def fake_run(cmd, **kwargs):
        return backup_db.subprocess.CompletedProcess(
            cmd, 0, b"mysqldump from 11.8.6-MariaDB, client 10.19 for debian-linux-gnu (x86_64)", b""
        )

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)
    assert backup_db._dump_client_extra_flags("/usr/bin/mysqldump") == []


def test_dump_flags_disable_column_statistics_for_mysql8_client(monkeypatch):
    """MySQL 8 probes COLUMN_STATISTICS, which MariaDB does not have."""

    def fake_run(cmd, **kwargs):
        return backup_db.subprocess.CompletedProcess(
            cmd, 0, b"mysqldump  Ver 8.0.36-0ubuntu0.22.04.1 for Linux on x86_64 (MySQL Community Server)", b""
        )

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)
    assert backup_db._dump_client_extra_flags("/usr/bin/mysqldump") == ["--column-statistics=0"]


def test_dump_flags_leave_mysql57_client_alone(monkeypatch):
    """MySQL 5.7 has no COLUMN_STATISTICS probe and rejects the flag."""

    def fake_run(cmd, **kwargs):
        return backup_db.subprocess.CompletedProcess(
            cmd, 0, b"mysqldump  Ver 10.13 Distrib 5.7.42, for Linux (x86_64)", b""
        )

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)
    assert backup_db._dump_client_extra_flags("/usr/bin/mysqldump") == []


def test_mysql_backup_includes_version_appropriate_flags(monkeypatch):
    """The real command must carry the flag chosen for the client on PATH."""
    captured = {}

    def fake_run(cmd, env=None, capture_output=True, check=False, **kwargs):
        if cmd[1:2] == ["--version"]:
            return backup_db.subprocess.CompletedProcess(cmd, 0, b"mysqldump  Ver 8.0.36 (MySQL)", b"")
        captured["cmd"] = cmd
        _write_dump(cmd)
        return backup_db.subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@dbhost:3306/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/mysqldump")
    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    result = backup_db.create_backup(include_uploads=False)
    try:
        assert "--column-statistics=0" in captured["cmd"]
    finally:
        result.unlink(missing_ok=True)


def test_backup_dispatches_to_mysql_when_configured(monkeypatch):
    calls = {}

    def fake_which(name):
        return "/usr/bin/mysqldump" if name == "mysqldump" else None

    def fake_run(cmd, env=None, capture_output=True, check=False):
        if cmd[1:2] == ["--version"]:
            return backup_db.subprocess.CompletedProcess(cmd, 0, b"mysqldump from MariaDB", b"")
        calls["cmd"] = cmd
        _write_dump(cmd)
        return backup_db.subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@dbhost:3306/ables")
    monkeypatch.setattr(backup_db.shutil, "which", fake_which)
    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    result = backup_db.create_backup(include_uploads=False)

    assert calls["cmd"][0] == "/usr/bin/mysqldump"
    assert "--single-transaction" in calls["cmd"]
    assert "dbhost" in calls["cmd"]
    assert "ables" in calls["cmd"]
    assert result.name.startswith("ables-mysql-")
    assert result.suffix == ".sql"
    result.unlink(missing_ok=True)


def test_mysql_url_never_falls_back_to_sqlite(monkeypatch):
    """The core regression: a MySQL URL must not silently back up SQLite."""
    monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@dbhost:3306/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: None)

    def fail_sqlite(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("SQLite backup must not run for a MySQL URL")

    monkeypatch.setattr(backup_db, "create_sqlite_backup", fail_sqlite)

    with pytest.raises(RuntimeError, match="mysqldump"):
        backup_db.create_backup(include_uploads=False)


def test_mysql_backup_raises_when_dump_tool_fails(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://user:pass@dbhost/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/mysqldump")

    def fake_run(cmd, env=None, capture_output=True, check=False):
        return backup_db.subprocess.CompletedProcess(cmd, 1, b"", b"Access denied for user")

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="mysqldump failed"):
        backup_db.create_backup(include_uploads=False)


def test_mysql_backup_raises_on_empty_dump(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://user:pass@dbhost/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/mysqldump")

    def fake_run(cmd, env=None, capture_output=True, check=False):
        return backup_db.subprocess.CompletedProcess(cmd, 0, b"mysqldump from MariaDB", b"")

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="empty backup"):
        backup_db.create_backup(include_uploads=False)


def test_mysql_backup_passes_password_via_environment_not_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, env=None, capture_output=True, check=False):
        if cmd[1:2] == ["--version"]:
            return backup_db.subprocess.CompletedProcess(cmd, 0, b"mysqldump from MariaDB", b"")
        captured["cmd"] = cmd
        captured["env"] = env
        _write_dump(cmd)
        return backup_db.subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setenv("DATABASE_URL", "mysql://user:sekret@dbhost:3306/ables")
    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/mysqldump")
    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    result = backup_db.create_backup(include_uploads=False)
    try:
        assert "sekret" not in " ".join(captured["cmd"])
        assert captured["env"]["MYSQL_PWD"] == "sekret"
    finally:
        result.unlink(missing_ok=True)


def test_verify_backup_rejects_corrupt_mysql_dump(tmp_path):
    import zipfile

    bad = tmp_path / "ablehome-mysql-bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("database.sql", b"not a sql dump")
    ok, message = backup_db.verify_backup(bad)
    assert ok is False
    assert "MySQL dump" in message


def test_verify_backup_accepts_valid_mysql_dump(tmp_path):
    import zipfile

    good = tmp_path / "ablehome-mysql-good.zip"
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("database.sql", b"-- MySQL dump 10.13\nINSERT INTO t VALUES (1);\n")
    ok, message = backup_db.verify_backup(good)
    assert ok is True, message


def test_database_driver_and_tool_detection():
    # SQLite needs no external driver or dump tool.
    assert database_driver_available("sqlite:///x.db") is True
    assert database_backup_tool_available("sqlite:///x.db") is True
    # Unknown schemes default to True (no false launch blockers).
    assert database_driver_available("") is True


def test_mysql_url_actually_connects_when_driver_present():
    """Connect to a real MariaDB/MySQL only when MYSQL_TEST_URL is provided."""
    url = os.environ.get("MYSQL_TEST_URL")
    if not url:
        pytest.skip("MYSQL_TEST_URL not set; live MySQL connection test skipped")
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1


def test_live_backup_and_restore_round_trip():
    """Dump a real database, restore it into a scratch database, compare rows.

    This is the drill the runbook documents, executed end to end rather than
    described: write rows, ``create_backup``, restore into a second database
    with the guarded helper, and read the rows back. Skipped unless
    ``MYSQL_TEST_URL`` names a reachable server; the scratch database is
    created and dropped by the test.
    """
    url = os.environ.get("MYSQL_TEST_URL")
    if not url:
        pytest.skip("MYSQL_TEST_URL not set; live backup/restore drill skipped")

    from sqlalchemy import create_engine, text
    from urllib.parse import urlsplit, urlunsplit

    source_db = "ables_drill_source"
    scratch_db = "ables_drill_restore"
    parts = urlsplit(url)
    server_url = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")

    def _recreate(name):
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
            conn.execute(text(f"CREATE DATABASE {name}"))

    _recreate(source_db)
    _recreate(scratch_db)
    source_url = urlunsplit((parts.scheme, parts.netloc, "/" + source_db, "", ""))
    scratch_url = urlunsplit((parts.scheme, parts.netloc, "/" + scratch_db, "", ""))
    try:
        source = create_engine(source_url)
        with source.begin() as conn:
            conn.execute(text("CREATE TABLE patients (id INT PRIMARY KEY, name VARCHAR(50))"))
            conn.execute(text("INSERT INTO patients VALUES (1, 'Ada'), (2, 'Grace')"))

        os.environ["DATABASE_URL"] = source_url
        try:
            backup_path = backup_db.create_backup(include_uploads=False)
            try:
                ok, message = backup_db.verify_backup(backup_path)
                assert ok is True, message

                # --restore-to must target the scratch database, not the source.
                with pytest.raises(RuntimeError, match="Refusing to restore"):
                    backup_db.restore_mysql_dump(backup_path, source_url)

                # include_uploads=False yields a bare .sql; the bundled form is a .zip.
                if backup_path.suffix == ".zip":
                    with zipfile.ZipFile(backup_path) as archive:
                        archive.extract("database.sql", backup_path.parent)
                    dump = backup_path.parent / "database.sql"
                else:
                    dump = backup_path
                try:
                    restored, _ = backup_db.restore_mysql_dump(dump, scratch_url)
                    assert restored is True

                    with create_engine(scratch_url).connect() as conn:
                        rows = conn.execute(
                            text("SELECT id, name FROM patients ORDER BY id")
                        ).fetchall()
                    assert rows == [(1, "Ada"), (2, "Grace")]
                finally:
                    if dump != backup_path:
                        dump.unlink(missing_ok=True)
            finally:
                backup_path.unlink(missing_ok=True)
        finally:
            os.environ.pop("DATABASE_URL", None)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {source_db}"))
            conn.execute(text(f"DROP DATABASE IF EXISTS {scratch_db}"))


def test_live_postgres_backup_and_restore_round_trip():
    """Same drill as the MySQL one, for PostgreSQL.

    The MySQL path had a live round trip but PostgreSQL only had mocked tests,
    so the documented PostgreSQL restore was never actually executed. Skipped
    unless ``POSTGRES_TEST_URL`` names a reachable server; the scratch database
    is created and dropped by the test. The client and server must be the same
    major version, as pg_dump refuses to restore newer output into older
    servers.
    """
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_URL not set; live PostgreSQL drill skipped")
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        pytest.skip("pg_dump/pg_restore not installed; live PostgreSQL drill skipped")

    from sqlalchemy import create_engine, text
    from urllib.parse import urlsplit, urlunsplit

    source_db = "ables_pg_drill_source"
    scratch_db = "ables_pg_drill_restore"
    parts = urlsplit(url)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    # pg_dump cannot dump a server newer than itself and pg_restore can reject
    # output written by a newer client, so a mismatched major would fail with a
    # confusing client error. Skip with a clear reason instead; CI pins the
    # service to the runner's client major so the drill does run there.
    client_major = int(
        subprocess.check_output(["pg_dump", "--version"]).split()[2].split(b".")[0]
    )
    with admin.connect() as conn:
        server_version = conn.execute(text("SHOW server_version")).scalar()
    server_major = int(str(server_version).split(".")[0])
    if client_major != server_major:
        admin.dispose()
        pytest.skip(
            f"pg_dump client major {client_major} != server major {server_major}; "
            "live PostgreSQL drill needs matching majors"
        )

    def _recreate(name):
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
            conn.execute(text(f"CREATE DATABASE {name}"))

    _recreate(source_db)
    _recreate(scratch_db)
    source_url = urlunsplit((parts.scheme, parts.netloc, "/" + source_db, "", ""))
    scratch_url = urlunsplit((parts.scheme, parts.netloc, "/" + scratch_db, "", ""))
    source = create_engine(source_url)
    scratch = create_engine(scratch_url)
    try:
        with source.begin() as conn:
            conn.execute(text("CREATE TABLE patients (id INT PRIMARY KEY, name VARCHAR(50))"))
            conn.execute(text("INSERT INTO patients VALUES (1, 'Ada'), (2, 'Grace')"))

        os.environ["DATABASE_URL"] = source_url
        try:
            backup_path = backup_db.create_backup(include_uploads=False)
            try:
                ok, message = backup_db.verify_backup(backup_path)
                assert ok is True, message

                with pytest.raises(RuntimeError, match="Refusing to restore"):
                    backup_db.restore_postgres_dump(backup_path, source_url)

                restored, message = backup_db.restore_postgres_dump(backup_path, scratch_url)
                assert restored is True
                assert "verified" in message

                with scratch.connect() as conn:
                    rows = conn.execute(text("SELECT id, name FROM patients ORDER BY id")).fetchall()
                assert rows == [(1, "Ada"), (2, "Grace")]

                # The verification must be able to fail on real data loss, not
                # merely pass. Delete rows from the restored copy, restore over
                # it with --clean (which drops and recreates the table), and
                # confirm the row-count check still accepts a full restore...
                with scratch.begin() as conn:
                    conn.execute(text("DELETE FROM patients WHERE id = 2"))
                restored, message = backup_db.restore_postgres_dump(backup_path, scratch_url)
                assert restored is True
                with scratch.connect() as conn:
                    count = conn.execute(text("SELECT COUNT(*) FROM patients")).scalar()
                assert count == 2

                # The verification must be able to fail, not merely pass. Give
                # the source a row the dump does not contain, so the restored
                # copy (2 rows) no longer matches the source (3 rows). This is
                # the exact condition a silently truncated restore produces, and
                # it must be rejected rather than reported as success.
                with source.begin() as conn:
                    conn.execute(text("INSERT INTO patients VALUES (3, 'Linus')"))
                with pytest.raises(RuntimeError, match="row-count mismatch"):
                    backup_db.restore_postgres_dump(
                        backup_path, scratch_url
                    )
            finally:
                backup_path.unlink(missing_ok=True)
        finally:
            os.environ.pop("DATABASE_URL", None)
    finally:
        # Release pooled connections; DROP DATABASE fails while any remain open.
        source.dispose()
        scratch.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {source_db}"))
            conn.execute(text(f"DROP DATABASE IF EXISTS {scratch_db}"))


def test_verify_restored_database_accepts_a_faithful_restore(tmp_path, monkeypatch):
    """A restored copy that matches the source in schema and rows passes.

    Uses real SQLite databases and real SQLAlchemy inspection, so the check
    itself is exercised rather than mocked.
    """
    from sqlalchemy import create_engine, text

    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    engine = create_engine(f"sqlite:///{source}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO patients VALUES (1, 'Ada'), (2, 'Grace')"))
    engine.dispose()
    shutil.copy(source, target)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{source}")

    objects = {"tables": {"patients"}, "indexes": set(), "constraints": set(),
               "sequences": set(), "data_tables": {"patients"}}
    ok, detail = backup_db._verify_restored_database(f"sqlite:///{target}", target, objects)
    assert ok is True, detail
    assert "row counts against the source" in detail


def test_verify_restored_database_rejects_missing_rows(tmp_path, monkeypatch):
    """Schema present but data lost must fail. This is the caution under test:
    table existence alone does not prove the data was restored."""
    from sqlalchemy import create_engine, text

    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    engine = create_engine(f"sqlite:///{source}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO patients VALUES (1, 'Ada'), (2, 'Grace')"))
    engine.dispose()

    # The target has the table but only one of the two rows.
    engine = create_engine(f"sqlite:///{target}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO patients VALUES (1, 'Ada')"))
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{source}")

    objects = {"tables": {"patients"}, "indexes": set(), "constraints": set(),
               "sequences": set(), "data_tables": {"patients"}}
    ok, detail = backup_db._verify_restored_database(f"sqlite:///{target}", target, objects)
    assert ok is False
    assert "row-count mismatch" in detail
    assert "patients" in detail


def test_verify_restored_database_rejects_a_missing_table(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    engine = create_engine(f"sqlite:///{source}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("CREATE TABLE clients (id INTEGER PRIMARY KEY)"))
    engine.dispose()
    engine = create_engine(f"sqlite:///{target}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT)"))
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{source}")

    objects = {"tables": {"patients", "clients"}, "indexes": set(), "constraints": set(),
               "sequences": set(), "data_tables": set()}
    ok, detail = backup_db._verify_restored_database(f"sqlite:///{target}", target, objects)
    assert ok is False
    assert "missing tables" in detail and "clients" in detail


def test_verify_restored_database_without_a_reachable_source_requires_non_empty(tmp_path, monkeypatch):
    """When the source is gone, counts cannot be compared, so a table the dump
    carried data for must at least be non-empty."""
    from sqlalchemy import create_engine, text

    target = tmp_path / "target.db"
    engine = create_engine(f"sqlite:///{target}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO patients VALUES (1, 'Ada')"))
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'does-not-exist.db'}")

    objects = {"tables": {"patients"}, "indexes": set(), "constraints": set(),
               "sequences": set(), "data_tables": {"patients"}}
    ok, detail = backup_db._verify_restored_database(f"sqlite:///{target}", target, objects)
    assert ok is True, detail
    assert "non-empty" in detail

    # Empty the table: now the weaker check must fail.
    engine = create_engine(f"sqlite:///{target}")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM patients"))
    engine.dispose()
    ok, detail = backup_db._verify_restored_database(f"sqlite:///{target}", target, objects)
    assert ok is False
    assert "empty after restore" in detail


def test_verify_restored_database_rejects_an_unreadable_toc(tmp_path, monkeypatch):
    """A dump whose table of contents cannot be read cannot be verified."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'source.db'}")
    ok, detail = backup_db._verify_restored_database(
        "sqlite:///:memory:", tmp_path / "database.dump", None
    )
    assert ok is False
    assert "table of contents" in detail


def test_verify_restored_database_rejects_an_unreachable_target(tmp_path, monkeypatch):
    """An unreachable target cannot be confirmed, so it is a failure."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'source.db'}")
    objects = {"tables": {"patients"}, "indexes": set(), "constraints": set(),
               "sequences": set(), "data_tables": {"patients"}}
    ok, detail = backup_db._verify_restored_database(
        "postgresql://u:p@127.0.0.1:1/none", tmp_path / "database.dump", objects
    )
    assert ok is False
    assert "could not inspect" in detail


def test_mysql_dump_objects_parses_create_and_insert(tmp_path):
    dump = tmp_path / "database.sql"
    dump.write_text(
        "-- MySQL dump\n"
        "CREATE TABLE `patients` (id INT PRIMARY KEY, name VARCHAR(50));\n"
        "INSERT INTO `patients` VALUES (1,'Ada'),(2,'Grace');\n"
        "CREATE TABLE IF NOT EXISTS clients (id INT);\n"
        "INSERT INTO clients VALUES (1);\n",
        encoding="utf-8",
    )
    objects = backup_db._mysql_dump_objects(dump)
    assert objects["tables"] == {"patients", "clients"}
    assert objects["data_tables"] == {"patients", "clients"}


def test_postgres_dump_objects_classifies_toc_lines(tmp_path, monkeypatch):
    # Real `pg_restore --list` output, including the lines that must not be
    # mistaken for a table or a plain sequence.
    toc = (
        ";\n"
        "218; 1259 16386 TABLE public client ables\n"
        "3458; 0 16386 TABLE DATA public client ables\n"
        "3470; 0 16386 TABLE ATTACH public client ables\n"
        "217; 1259 16385 SEQUENCE public client_id_seq ables\n"
        "3479; 0 0 SEQUENCE OWNED BY public client_id_seq ables\n"
        "3480; 0 0 SEQUENCE SET public client_id_seq ables\n"
        "3317; 1259 16394 INDEX public client_name_idx ables\n"
        "3319; 2606 16393 CONSTRAINT public client client_pkey ables\n"
    )

    def fake_run(cmd, capture_output=True, check=False):
        return backup_db.subprocess.CompletedProcess(cmd, 0, toc.encode(), b"")

    monkeypatch.setattr(backup_db.shutil, "which", lambda name: "/usr/bin/pg_restore")
    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)
    objects = backup_db._postgres_dump_objects(tmp_path / "database.dump")
    assert objects["tables"] == {"client"}
    assert objects["data_tables"] == {"client"}
    assert objects["indexes"] == {"client_name_idx"}
    assert objects["constraints"] == {"client_pkey"}
    assert objects["sequences"] == {"client_id_seq"}


def test_postgres_dump_objects_reads_a_real_dump(tmp_path):
    """Parse an actual pg_dump file when a PostgreSQL server is available."""
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_URL not set; real-dump parse skipped")
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        pytest.skip("pg_dump/pg_restore not installed")
    dump = tmp_path / "real.dump"
    created = subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(dump), "--schema-only", url],
        capture_output=True, check=False,
    )
    if created.returncode != 0 or not dump.is_file():
        pytest.skip(f"pg_dump could not dump {url!r}")
    objects = backup_db._postgres_dump_objects(dump)
    assert objects is not None
    assert isinstance(objects["tables"], set)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql://u:p@h/db", "postgresql"),
        ("postgres://u:p@h/db", "other"),  # raw scheme; config normalizes first
        ("mysql+pymysql://u:p@h/db", "mysql"),
        ("mariadb://u:p@h/db", "mysql"),
        ("sqlite:///instance/ablehome.db", "sqlite"),
        ("", "other"),
    ],
)
def test_database_backend_name(url, expected):
    from routes import database_backend_name

    assert database_backend_name(url) == expected


def test_health_schema_check_works_on_mysql(monkeypatch):
    """The health schema probe must not use PostgreSQL-only quoting.

    On MySQL/MariaDB a quoted ``"user"`` is a string literal, so the old
    ``SELECT COUNT(*) FROM "user"`` reported an unhealthy schema. The probe now
    inspects table names, which is backend-agnostic.
    """
    from routes import _core_health_checks

    with app.app_context():
        checks = _core_health_checks()
    assert checks["database_schema"] == "ok"
    assert checks["database"] == "ok"


def test_production_without_database_url_fails_fast(monkeypatch):
    """Production must not silently boot on the local SQLite fallback."""
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        config.database_url()


def test_production_with_database_url_is_allowed(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "mysql://user:pass@localhost:3306/ables")
    assert config.database_url() == "mysql+pymysql://user:pass@localhost:3306/ables"


def test_non_production_without_database_url_uses_sqlite(monkeypatch):
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert config.database_url().startswith("sqlite:///")


def test_passenger_entrypoint_reexports_wsgi_application():
    """cPanel/Passenger loads ``passenger_wsgi.application``."""
    import passenger_wsgi

    assert passenger_wsgi.application is not None
    assert callable(passenger_wsgi.application)


def test_production_with_development_secret_key_fails_fast(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "dev-secret-change-later")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.secret_key(production=True)


def test_production_with_missing_secret_key_fails_fast(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.secret_key(production=True)


def test_production_with_short_secret_key_fails_fast(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "too-short")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.secret_key(production=True)


def test_production_with_strong_secret_key_is_allowed(monkeypatch):
    strong = "f" * 64
    monkeypatch.setenv("SECRET_KEY", strong)
    assert config.secret_key(production=True) == strong


def test_development_uses_fallback_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert config.secret_key(production=False) == "dev-secret-change-later"


def test_create_backup_rejects_empty_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="Unsupported or missing DATABASE_URL"):
        backup_db.create_backup(include_uploads=False)


def test_create_backup_rejects_unsupported_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "oracle://user:pass@host/db")
    with pytest.raises(RuntimeError, match="Unsupported or missing DATABASE_URL"):
        backup_db.create_backup(include_uploads=False)


def test_create_backup_uses_sqlite_only_for_sqlite_url(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/x.db")
    called = {}

    def fake_sqlite(include_uploads=True):
        called["sqlite"] = True
        return tmp_path / "fake.zip"

    monkeypatch.setattr(backup_db, "create_sqlite_backup", fake_sqlite)
    result = backup_db.create_backup(include_uploads=False)
    assert called.get("sqlite") is True
    assert result == tmp_path / "fake.zip"


def test_migrate_data_target_counts_raises_on_missing_table():
    """A missing target table must raise, not be reported as zero rows."""
    import migrate_data
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        with pytest.raises(Exception):
            migrate_data.target_counts(connection)


def test_postgres_backup_hands_pg_dump_a_client_parseable_url(monkeypatch, tmp_path):
    """pg_dump must not receive a SQLAlchemy-only URL.

    ``postgres://`` (Heroku-style) is not understood by every pg_dump build, so
    the helper must hand over the normalized ``postgresql://`` form. This runs
    the real ``main()`` and inspects the argv it builds.
    """
    import postgres_backup

    captured = {}

    def fake_run(cmd, capture_output=True, check=False, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("--file") + 1]).write_bytes(b"PGDMP fake dump")
        return backup_db.subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@dbhost:5432/ables")
    monkeypatch.setattr(postgres_backup, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(postgres_backup.shutil, "which", lambda name: "/usr/bin/pg_dump")
    monkeypatch.setattr(postgres_backup.subprocess, "run", fake_run)

    postgres_backup.main()

    assert captured["cmd"][0] == "pg_dump"
    assert captured["cmd"][-1] == "postgresql://u:p@dbhost:5432/ables"
    assert "postgres://u:p@dbhost:5432/ables" != captured["cmd"][-1]


def test_postgres_backup_removes_a_truncated_dump_on_failure(monkeypatch, tmp_path):
    """A failed pg_dump must not leave a zero-byte file that looks like a backup."""
    import postgres_backup

    def fake_run(cmd, capture_output=True, check=False, **kwargs):
        # Simulate pg_dump creating the file then failing.
        Path(cmd[cmd.index("--file") + 1]).write_bytes(b"")
        return backup_db.subprocess.CompletedProcess(cmd, 1, b"", b"connection refused")

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dbhost:5432/ables")
    monkeypatch.setattr(postgres_backup, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(postgres_backup.shutil, "which", lambda name: "/usr/bin/pg_dump")
    monkeypatch.setattr(postgres_backup.subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="pg_dump failed"):
        postgres_backup.main()
    assert list(tmp_path.iterdir()) == [], "truncated dump was left behind"


def test_postgres_backup_requires_pg_dump_on_path(monkeypatch, tmp_path):
    """Missing pg_dump must be reported clearly, not raise FileNotFoundError."""
    import postgres_backup

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@dbhost:5432/ables")
    monkeypatch.setattr(postgres_backup, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(postgres_backup.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit, match="pg_dump is required"):
        postgres_backup.main()




