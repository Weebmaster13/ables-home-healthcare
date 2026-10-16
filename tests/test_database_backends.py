"""Regression tests for database backend selection and backups.

Covers URL normalization, driver/tool detection, strict backend dispatch,
MySQL/MariaDB dump handling, and the guarded restore helper. Most tests use
deterministic fakes for ``subprocess``/``shutil.which``; two optional live
tests (connection and a full backup/restore round trip) skip unless a
MariaDB/MySQL URL is provided via ``MYSQL_TEST_URL``.
"""
import os
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


def test_backup_dispatches_to_mysql_when_configured(monkeypatch):
    calls = {}

    def fake_which(name):
        return "/usr/bin/mysqldump" if name == "mysqldump" else None

    def fake_run(cmd, env=None, capture_output=True, check=False):
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
        return backup_db.subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="empty backup"):
        backup_db.create_backup(include_uploads=False)


def test_mysql_backup_passes_password_via_environment_not_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, env=None, capture_output=True, check=False):
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



