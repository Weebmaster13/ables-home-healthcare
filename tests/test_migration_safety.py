"""Tests for safe data migration, registration throttling, init guard."""
import sqlite3

import migrate_data
from models import Appointment, Inquiry, Referral, User


def _seed(source_url, target_url=None):
    from sqlalchemy import create_engine
    from extensions import db
    engine = create_engine(source_url)
    db.Model.metadata.create_all(engine)
    if target_url is not None:
        # Mirror the documented precondition: schema exists on target
        # (in production via `flask db upgrade`) before any copy.
        db.Model.metadata.create_all(create_engine(target_url))
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(User.__table__.insert(), [
                {"id": 1, "name": "Client One", "email": "c1@example.com",
                 "password": "hashed", "role": "client"},
            ])
            connection.execute(Inquiry.__table__.insert(), [
                {"id": 1, "name": "Family", "email": "f@example.com",
                 "message": "need care", "status": "new"},
            ])
            connection.execute(Referral.__table__.insert(), [
                {"id": 1, "referral_source": "Clinic", "source_phone": "1",
                 "client_name": "Client One", "status": "new"},
            ])
            connection.execute(Appointment.__table__.insert(), [
                {"id": 1, "client_id": 1, "service_type": "Personal care",
                 "status": "pending"},
            ])
    return engine


def test_migrate_dry_run_writes_nothing(tmp_path):
    source = f"sqlite:///{tmp_path}/src.db"
    target = f"sqlite:///{tmp_path}/dst.db"
    _seed(source, target)
    assert migrate_data.main(["--from", source, "--to", target]) == 0
    with sqlite3.connect(tmp_path / "dst.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 0


def test_migrate_execute_preserves_rows_and_relationships(tmp_path, capsys):
    source = f"sqlite:///{tmp_path}/src.db"
    target = f"sqlite:///{tmp_path}/dst.db"
    _seed(source, target)
    assert migrate_data.main(["--from", source, "--to", target, "--execute"]) == 0
    with sqlite3.connect(tmp_path / "dst.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 1
        row = connection.execute(
            "SELECT client_id, service_type FROM appointment").fetchone()
        assert row == (1, "Personal care")
        assert connection.execute("SELECT COUNT(*) FROM inquiry").fetchone()[0] == 1


def test_migrate_refuses_nonempty_target_and_same_db(tmp_path):
    source = f"sqlite:///{tmp_path}/src.db"
    target = f"sqlite:///{tmp_path}/dst.db"
    _seed(source, target)
    assert migrate_data.main(["--from", source, "--to", target, "--execute"]) == 0
    assert migrate_data.main(["--from", source, "--to", target, "--execute"]) == 2
    assert migrate_data.main(["--from", source, "--to", source, "--execute"]) == 2


def test_init_db_refuses_production():
    import subprocess
    import sys
    proc = subprocess.run(
        [sys.executable, "init_db.py"], capture_output=True, text=True,
        env={"PATH": __import__("os").environ["PATH"],
             "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
             "FLASK_ENV": "production"},
    )
    assert proc.returncode != 0
    assert "seed_production_admin" in (proc.stdout + proc.stderr)


def test_registration_is_rate_limited_like_login():
    from app import app
    from security import LOGIN_ATTEMPTS
    app.config["WTF_CSRF_ENABLED"] = False
    try:
        with app.test_client() as client:
            for _ in range(6):
                response = client.post("/auth/register", data={
                    "name": "Spam", "email": "spam@example.com",
                    "password": "x", "confirm_password": "y",
                }, follow_redirects=False)
                assert response.status_code == 302
            limited = client.get("/auth/register")
            assert limited.status_code == 200
            blocked = client.post("/auth/register", data={
                "name": "Spam", "email": "spam2@example.com",
                "password": "x", "confirm_password": "y",
            }, follow_redirects=True)
            assert b"Too many attempts" in blocked.data
    finally:
        LOGIN_ATTEMPTS.clear()
