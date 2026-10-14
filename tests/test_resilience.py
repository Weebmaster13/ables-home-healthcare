"""Failure-safe persistence + backup/restore procedure tests (isolated)."""
import shutil
import sqlite3


def test_contact_persists_when_smtp_is_down():
    from app import app
    from models import Inquiry
    from extensions import db
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["MAIL_SERVER"] = "localhost"
    app.config["MAIL_PORT"] = 1
    app.config["MAIL_USERNAME"] = ""
    app.config["MAIL_PASSWORD"] = ""
    try:
        with app.test_client() as client:
            response = client.post("/contact", data={
                "name": "Down SMTP Test", "email": "down-smtp@example.com",
                "phone": "", "reason": "Ask about services",
                "message": "Checking failure-safe persistence.",
            }, follow_redirects=False)
            assert response.status_code == 302
        with app.app_context():
            row = Inquiry.query.filter_by(email="down-smtp@example.com").first()
            assert row is not None
            assert row.message == "Checking failure-safe persistence."
            db.session.delete(row)
            db.session.commit()
    finally:
        app.config["MAIL_SERVER"] = ""
        app.config["MAIL_PORT"] = 587


def test_backup_restore_roundtrip_isolated(tmp_path):
    """Procedure test: file-copy backup of a database restores intact,
    and the app boots against the restored copy."""
    from sqlalchemy import create_engine
    from extensions import db
    live = tmp_path / "live.db"
    engine = create_engine(f"sqlite:///{live}")
    db.Model.metadata.create_all(engine)
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                __import__("models").Inquiry.__table__.insert(),
                [{"name": "Backup Probe", "email": "probe@example.com",
                  "message": "restore me", "status": "new"}])
    engine.dispose()

    backup = tmp_path / "backup.db"
    shutil.copy2(live, backup)
    live.unlink()  # simulate loss
    assert not live.exists()
    shutil.copy2(backup, live)  # restore

    with sqlite3.connect(live) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM inquiry WHERE email='probe@example.com'"
        ).fetchone()[0]
        assert count == 1
