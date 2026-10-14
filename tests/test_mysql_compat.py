"""MySQL/cPanel compatibility tests (no MySQL server needed)."""
import os

import pytest


def _load_config(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    import importlib
    import config
    importlib.reload(config)
    try:
        return config.database_url()
    finally:
        importlib.reload(config)


def test_mysql_url_normalizes_to_pymysql_driver(monkeypatch):
    assert _load_config(
        monkeypatch, "mysql://u:p@localhost:3306/ables"
    ) == "mysql+pymysql://u:p@localhost:3306/ables"


def test_postgres_and_sqlite_urls_untouched(monkeypatch):
    assert _load_config(
        monkeypatch, "postgres://u:p@h:5432/db"
    ) == "postgresql://u:p@h:5432/db"
    assert _load_config(
        monkeypatch, "postgresql://u:p@h:5432/db"
    ) == "postgresql://u:p@h:5432/db"


def test_audit_search_ilike_compiles_on_mysql():
    from sqlalchemy.dialects import mysql
    from app import app
    from models import AuditLog
    with app.app_context():
        statement = AuditLog.query.filter(
            AuditLog.action.ilike("%login%")
        ).statement
    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert "LIKE" in compiled.upper()


def test_requirements_keep_postgres_and_add_pymysql():
    text = open("requirements.txt", encoding="utf-8").read()
    assert "psycopg2-binary" in text  # Railway production stays intact
    assert "PyMySQL" in text  # cPanel shared hosting driver


def test_engine_uses_pre_ping_for_stale_connections():
    from app import app
    assert app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}).get("pool_pre_ping") is True


def test_production_without_database_url_fails_fast(monkeypatch):
    import importlib
    import config
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # load_dotenv would otherwise restore DATABASE_URL from .env.example
    monkeypatch.setenv("ENV_FILE", ".env.nonexistent-for-test")
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
            importlib.reload(config)
    finally:
        monkeypatch.delenv("FLASK_ENV", raising=False)
        importlib.reload(config)


def test_secure_cookie_flags_default_on_and_allow_override(monkeypatch):
    import importlib
    import config
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@localhost:3306/db")
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    importlib.reload(config)
    try:
        assert config.Config.SESSION_COOKIE_SECURE is True
        assert config.Config.REMEMBER_COOKIE_SECURE is True
    finally:
        importlib.reload(config)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    importlib.reload(config)
    try:
        assert config.Config.SESSION_COOKIE_SECURE is False
    finally:
        importlib.reload(config)


def test_healthz_schema_check_has_no_mysql_breakers():
    import pathlib
    lines = pathlib.Path("routes.py").read_text(encoding="utf-8").splitlines()
    code_hits = [line for line in lines if 'FROM "user"' in line and not line.strip().startswith("#")]
    assert code_hits == []


def test_passenger_entrypoint_reexports_app():
    import passenger_wsgi
    assert hasattr(passenger_wsgi, "application")
    assert passenger_wsgi.application is not None


def test_codebase_parses_under_python_311_grammar():
    """cPanel tops out at 3.11: no 3.12-only syntax may ship.

    Uses ast feature_version, so it runs on any newer interpreter.
    """
    import ast
    import pathlib
    roots = [
        pathlib.Path("app.py"),
        pathlib.Path("wsgi.py"),
        pathlib.Path("passenger_wsgi.py"),
        pathlib.Path("config.py"),
        pathlib.Path("models.py"),
        pathlib.Path("routes.py"),
        *sorted(pathlib.Path("auth").glob("*.py")),
        *sorted(pathlib.Path("migrations/versions").glob("*.py")),
    ]
    assert roots, "expected project sources to exist"
    for path in roots:
        if path.name == "__init__.py" and "migrations" in str(path):
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path),
                      feature_version=(3, 11))
        except SyntaxError as exc:
            raise AssertionError(f"{path} needs newer than Python 3.11: {exc}")
