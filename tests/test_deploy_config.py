"""Deployment-config regression tests: no Docker daemon needed."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_smoke_workflow_secret_key_passes_production_guard():
    """The CI dummy key must satisfy the production SECRET_KEY guard.

    A too-short CI key silently breaks the smoke stack (the app refuses to
    boot), which is exactly what the production guard is meant to prevent.
    """
    from config import DEVELOPMENT_SECRET_KEYS

    text = (ROOT / ".github" / "workflows" / "deploy-smoke.yml").read_text()
    match = re.search(r"^\s*SECRET_KEY=(\S+)\s*$", text, re.MULTILINE)
    assert match, "deploy-smoke.yml must set a SECRET_KEY for the smoke stack"
    key = match.group(1)
    assert len(key) >= 32, f"CI SECRET_KEY too short for the production guard: {len(key)} chars"
    assert key not in DEVELOPMENT_SECRET_KEYS


def test_dockerfile_uses_supported_runtime_and_port():
    text = (ROOT / "Dockerfile").read_text()
    assert "python:3.12" in text
    assert "$PORT" in text
    assert "wsgi:app" in text
    assert "gunicorn" in text


def test_dockerignore_excludes_secrets_and_private_data():
    text = (ROOT / ".dockerignore").read_text()
    for entry in ["instance/", ".env", "__pycache__/"]:
        assert entry in text
    # migrations must ship (needed for `flask db upgrade` jobs)
    assert "migrations" not in text.replace("migrations must ship", "")


def test_wsgi_entrypoint_is_deployable():
    import wsgi
    assert hasattr(wsgi, "app")
    assert hasattr(wsgi, "application")
    # production boot through the entrypoint serves public pages
    client = wsgi.flask_app.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/care").status_code == 200


def test_oracle_compose_is_complete_and_safe():
    import yaml
    compose = yaml.safe_load((ROOT / "docker-compose.oracle.yml").read_text())
    services = compose["services"]
    assert set(services) == {"db", "web", "nginx"}
    web = services["web"]
    assert "gunicorn" in web["command"] and "wsgi:app" in web["command"]
    assert "db upgrade" in web["command"]
    assert web.get("healthcheck"), "web service must have a healthcheck"
    # uploads must persist on a named volume, not ephemeral container disk
    volumes = " ".join(str(v) for v in web.get("volumes", []))
    assert "uploads" in volumes
    assert "uploads" in compose.get("volumes", {})
    # no secret values baked into the compose file
    flat = str(compose)
    assert "REPLACE_" not in flat
    for key in ["SECRET_KEY", "DATABASE_URL", "MAIL_PASSWORD"]:
        assert key not in flat


def test_nginx_conf_proxies_app_and_canonicalizes_domain():
    text = (ROOT / "nginx.oracle.conf").read_text()
    assert "proxy_pass http://ables" in text
    assert "X-Forwarded-Proto" in text
    assert "ableshomehealthcare.com" in text
    assert "www.ableshomehealthcare.com" in text
    assert "return 301 https://ableshomehealthcare.com" in text


def test_oracle_env_template_has_placeholders_only():
    text = (ROOT / ".env.oracle.example").read_text()
    assert "REPLACE_" in text
    assert ".env.oracle" in (ROOT / ".gitignore").read_text()


def test_requirements_include_both_database_drivers():
    text = (ROOT / "requirements.txt").read_text()
    # PostgreSQL (Railway/VPS) and MySQL/MariaDB (cPanel) must both install.
    assert "psycopg2-binary" in text
    assert "PyMySQL" in text


def test_config_normalizes_mysql_and_postgres_schemes():
    text = (ROOT / "config.py").read_text()
    assert "mysql+pymysql://" in text
    assert "postgresql://" in text
