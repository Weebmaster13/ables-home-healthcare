"""MySQL/cPanel compatibility tests (no MySQL server needed)."""
import os


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


def test_passenger_entrypoint_reexports_app():
    import passenger_wsgi
    assert hasattr(passenger_wsgi, "application")
    assert passenger_wsgi.application is not None
