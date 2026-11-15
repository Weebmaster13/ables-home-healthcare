"""Regression tests: reset-link host trust + reset-token log redaction."""
import logging
import uuid

from app import app
from extensions import db
from models import User
from notifications import redact_for_log
from werkzeug.security import generate_password_hash


def _make_user():
    email = f"reset-{uuid.uuid4().hex}@example.com"
    user = User(name="Reset Probe", email=email,
                password=generate_password_hash("SafePass2026!"),
                role="client")
    db.session.add(user)
    db.session.commit()
    return user


def test_reset_link_uses_canonical_base_not_request_host():
    """A forged Host header must not poison the emailed reset link."""
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        user = _make_user()
        email, user_id = user.email, user.id
        canonical = app.config["SITE_BASE_URL"].rstrip("/")
    try:
        with app.test_client() as client:
            response = client.post(
                "/auth/forgot-password",
                data={"email": email},
                headers={"Host": "evil.example"},
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"evil.example" not in response.data
            marker = b"/auth/reset-password/"
            assert marker in response.data
            start = response.data.index(b"http", response.data.index(marker) - 256)
            end = response.data.index(b"\"", response.data.index(marker))
            link = response.data[start:end].decode("utf-8")
            assert link.startswith(canonical + "/auth/reset-password/"), link
    finally:
        with app.app_context():
            User.query.filter_by(id=user_id).delete()
            db.session.commit()


def test_skipped_reset_email_redacts_token_in_logs(caplog):
    """With SMTP down, the reset token must never reach the log file."""
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["MAIL_SERVER"] = ""
    with app.app_context():
        user = _make_user()
        email, user_id = user.email, user.id
    try:
        with caplog.at_level(logging.INFO, logger="app"):
            with app.test_client() as client:
                client.post("/auth/forgot-password",
                            data={"email": email},
                            follow_redirects=True)
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "/reset-password/" not in logged.replace(
            "/reset-password/[redacted]", "")
        assert "redacted-reset-link" in logged or \
            "/reset-password/[redacted]" in logged
    finally:
        with app.app_context():
            User.query.filter_by(id=user_id).delete()
            db.session.commit()


def test_redact_for_log_keeps_ordinary_text():
    assert redact_for_log("delivery ok, no links") == "delivery ok, no links"
    assert redact_for_log("") == ""
    red = redact_for_log(
        "go to https://x.test/auth/reset-password/abc123 now")
    assert "abc123" not in red
    assert "redacted-reset-link" in red
    red = redact_for_log("path /reset-password/abc123 end")
    assert "abc123" not in red


def _login_client(password, email):
    from test_core_features import login as login_helper
    from types import SimpleNamespace
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    with app.app_context():
        from models import User as UserModel
        user = UserModel.query.filter_by(email=email).first()
        helper_user = SimpleNamespace(email=user.email, id=user.id)
    assert login_helper(client, helper_user,
                        password).status_code == 302
    return client


def test_deactivation_kills_live_session():
    from models import User as UserModel
    from extensions import db as db_ext
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from test_core_features import create_user as make_user
        user, password = make_user("client")
        email, uid = user.email, user.id
    client = _login_client(password, email)
    assert client.get("/dashboard").status_code == 200
    with app.app_context():
        row = db_ext.session.get(UserModel, uid)
        row.is_active_account = False
        db_ext.session.commit()
    try:
        response = client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]
    finally:
        with app.app_context():
            UserModel.query.filter_by(id=uid).delete()
            db_ext.session.commit()


def test_password_change_invalidates_sibling_session_keeps_changer():
    from models import User as UserModel
    from extensions import db as db_ext
    with app.app_context():
        from test_core_features import create_user as make_user
        user, password = make_user("client")
        email, uid = user.email, user.id
    changer = _login_client(password, email)
    sibling = _login_client(password, email)
    assert changer.get("/dashboard").status_code == 200
    assert sibling.get("/dashboard").status_code == 200
    try:
        response = changer.post("/auth/change-password", data={
            "current_password": password,
            "password": "BrandNewSafe2026!",
            "confirm_password": "BrandNewSafe2026!",
        }, follow_redirects=False)
        assert response.status_code == 302
        assert changer.get("/dashboard").status_code == 200
        stale = sibling.get("/dashboard", follow_redirects=False)
        assert stale.status_code == 302
        assert "/auth/login" in stale.headers["Location"]
    finally:
        with app.app_context():
            UserModel.query.filter_by(id=uid).delete()
            db_ext.session.commit()


def test_reset_token_cannot_be_reused():
    from urllib.parse import urlparse
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from test_core_features import create_user as make_user
        user, _ = make_user("client")
        email, uid = user.email, user.id
    try:
        with app.test_client() as client:
            posted = client.post("/auth/forgot-password",
                                 data={"email": email},
                                 follow_redirects=False)
            assert posted.status_code == 302
            with client.session_transaction() as session:
                dev_url = session.get("dev_password_reset_url")
            assert dev_url, "dev reset URL expected without SMTP"
            path = urlparse(dev_url).path
            first = client.post(path, data={
                "password": "OnceUsedSafe2026!",
                "confirm_password": "OnceUsedSafe2026!",
            }, follow_redirects=False)
            assert first.status_code == 302
            assert "/auth/login" in first.headers["Location"]
            second = client.get(path, follow_redirects=False)
            assert second.status_code == 302
            assert "/auth/forgot-password" in second.headers["Location"]
    finally:
        with app.app_context():
            from extensions import db as db_ext
            from models import User as UserModel
            UserModel.query.filter_by(id=uid).delete()
            db_ext.session.commit()


def test_login_regenerates_session():
    from types import SimpleNamespace
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from test_core_features import create_user as make_user
        user, password = make_user("client")
        email, uid = user.email, user.id
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["planted"] = "attacker-value"
            from test_core_features import login as login_helper
            helper = SimpleNamespace(email=email, id=uid)
            assert login_helper(client, helper, password).status_code == 302
            with client.session_transaction() as session:
                assert "planted" not in session
            assert client.get("/dashboard").status_code == 200
    finally:
        with app.app_context():
            from extensions import db as db_ext
            from models import User as UserModel
            UserModel.query.filter_by(id=uid).delete()
            db_ext.session.commit()


def _dev_reset_path(client, email):
    client.post("/auth/forgot-password", data={"email": email},
                follow_redirects=False)
    with client.session_transaction() as session:
        dev_url = session.get("dev_password_reset_url")
    assert dev_url, "dev reset URL expected without SMTP"
    from urllib.parse import urlparse
    return urlparse(dev_url).path


def test_stale_version_token_rejected():
    """A token bound to an older version fails after a concurrent bump."""
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from test_core_features import create_user as make_user
        user, _ = make_user("client")
        email, uid = user.email, user.id
    try:
        with app.test_client() as client:
            path = _dev_reset_path(client, email)
            with app.app_context():
                from extensions import db as db_ext
                from models import User as UserModel
                row = db_ext.session.get(UserModel, uid)
                row.reset_token_version = (row.reset_token_version or 0) + 1
                db_ext.session.commit()
            response = client.post(path, data={
                "password": "StaleVersion2026!",
                "confirm_password": "StaleVersion2026!",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "/auth/forgot-password" in response.headers["Location"]
    finally:
        with app.app_context():
            from extensions import db as db_ext
            from models import User as UserModel
            UserModel.query.filter_by(id=uid).delete()
            db_ext.session.commit()


def test_concurrent_replay_allows_exactly_one_use():
    """Two simultaneous POSTs of one token: exactly one succeeds."""
    import threading
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from test_core_features import create_user as make_user
        user, _ = make_user("client")
        email, uid = user.email, user.id
    try:
        with app.test_client() as probe:
            path = _dev_reset_path(probe, email)
        gate = threading.Barrier(2)
        outcomes = []

        def attempt(tag):
            with app.test_client() as client:
                gate.wait(timeout=15)
                response = client.post(path, data={
                    "password": f"RaceWin2026-{tag}!",
                    "confirm_password": f"RaceWin2026-{tag}!",
                }, follow_redirects=False)
                outcomes.append(response.headers.get("Location", ""))

        threads = [threading.Thread(target=attempt, args=(i,))
                   for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert len(outcomes) == 2
        wins = [u for u in outcomes if u.endswith("/auth/login")]
        used = [u for u in outcomes if "forgot-password" in u]
        assert len(wins) == 1, outcomes
        assert len(used) == 1, outcomes
    finally:
        with app.app_context():
            from extensions import db as db_ext
            from models import User as UserModel
            UserModel.query.filter_by(id=uid).delete()
            db_ext.session.commit()


def test_migration_chain_includes_token_version():
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory
    from pathlib import Path
    base = Path("migrations")
    cfg = AlembicConfig(str(base / "alembic.ini"))
    cfg.set_main_option("script_location", str(base))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["9f3c1a72b84d"]
    from models import User as UserModel
    assert "reset_token_version" in UserModel.__table__.columns
    assert UserModel.__table__.columns["reset_token_version"].default.arg == 0
