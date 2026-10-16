"""Regression tests for database backend selection and backups.

Covers URL normalization, driver/tool detection, strict backend dispatch, and
MySQL/MariaDB dump handling. These tests never touch a real database server:
``subprocess``/``shutil.which`` are replaced with deterministic fakes, and the
one optional live-connection test skips unless a MariaDB/MySQL URL is provided
via ``MYSQL_TEST_URL``.
"""
import os
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

