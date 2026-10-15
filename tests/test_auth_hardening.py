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
