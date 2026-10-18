"""End-to-end tests for the email notification path.

The reviewer's concern was that no test proved a message actually leaves the
application. These tests run a real (dependency-free) SMTP server on localhost,
point the app at it, and assert the message is received and recorded as
``sent``. This exercises smtplib, the message construction, and the
NotificationLog write together.

It still does not prove deliverability to the admin's real inbox: that needs
the owner's SMTP provider and credentials. It does prove the code sends when a
server is reachable, and records ``skipped``/``failed`` honestly when not.
"""
import socket
import threading
import uuid

import pytest

from app import app
from extensions import db
from models import NotificationLog
from notifications import send_email_notification


class _SMTPSink:
    """A minimal SMTP server that accepts a message and stores it."""

    def __init__(self):
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self.messages = []
        self._closing = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._closing:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            stream = conn.makefile("rwb")

            def reply(line):
                stream.write((line + "\r\n").encode())
                stream.flush()

            reply("220 test-sink ready")
            in_data = False
            buffer = []
            while True:
                raw = stream.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if in_data:
                    if line == ".":
                        in_data = False
                        self.messages.append("\n".join(buffer))
                        buffer = []
                        reply("250 OK queued")
                    else:
                        buffer.append(line)
                    continue
                upper = line.upper()
                if upper.startswith(("EHLO", "HELO")):
                    reply("250-test-sink")
                    reply("250 OK")
                elif upper.startswith("DATA"):
                    in_data = True
                    reply("354 End data with <CR><LF>.<CR><LF>")
                elif upper.startswith("QUIT"):
                    reply("221 Bye")
                    return
                else:
                    reply("250 OK")

    def close(self):
        self._closing = True
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def smtp_sink():
    sink = _SMTPSink()
    yield sink
    sink.close()


def _configure_smtp(port):
    app.config.update(
        MAIL_SERVER="127.0.0.1",
        MAIL_PORT=port,
        MAIL_USE_TLS=False,
        MAIL_USERNAME=None,
        MAIL_PASSWORD=None,
        MAIL_DEFAULT_SENDER="admin@ableshomehealthcare.com",
    )


def _decoded_body(raw_message):
    """Decode a raw SMTP DATA payload into readable text.

    The sink stores the bytes exactly as sent, so the body may be quoted-
    printable or base64 MIME and the link can be split across lines.
    """
    from email import message_from_string

    parsed = message_from_string(raw_message)
    if parsed.is_multipart():
        chunks = []
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                chunks.append(part.get_payload(decode=True).decode("utf-8", "replace"))
        return "\n".join(chunks)
    payload = parsed.get_payload(decode=True)
    if payload is None:
        return raw_message
    return payload.decode("utf-8", "replace")


def test_send_email_reaches_a_real_smtp_server_and_is_logged_sent(smtp_sink):
    _configure_smtp(smtp_sink.port)
    with app.app_context():
        sent = send_email_notification(
            "owner@ableshomehealthcare.com",
            "New website inquiry",
            "Name: Ada\nEmail: ada@example.com",
            related_type="Inquiry",
            related_id=7,
        )

        assert sent is True
        assert len(smtp_sink.messages) == 1
        received = smtp_sink.messages[0]
        assert "New website inquiry" in received
        assert "ada@example.com" in received

        log = NotificationLog.query.filter_by(channel="email", status="sent").first()
        assert log is not None
        assert log.recipient == "owner@ableshomehealthcare.com"
        assert log.related_type == "Inquiry"
        assert log.related_id == 7


def test_send_email_without_smtp_config_is_logged_skipped():
    """The production misconfiguration the audit reported must be visible."""
    app.config.update(MAIL_SERVER=None, MAIL_DEFAULT_SENDER="admin@ableshomehealthcare.com")
    with app.app_context():
        sent = send_email_notification("owner@ableshomehealthcare.com", "Subject", "Body")

        assert sent is False
        log = NotificationLog.query.filter_by(channel="email", status="skipped").first()
        assert log is not None
        assert "SMTP" in (log.error or "")


def test_send_email_to_unreachable_server_is_logged_failed():
    """A dead SMTP host must record 'failed', not a silent 'sent'."""
    # Bind then close to obtain a port that is almost certainly not listening.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    _configure_smtp(dead_port)
    with app.app_context():
        sent = send_email_notification("owner@ableshomehealthcare.com", "Subject", "Body")

        assert sent is False
        log = NotificationLog.query.filter_by(channel="email", status="failed").first()
        assert log is not None
        assert log.error


def test_sent_log_recipient_matches_the_address_actually_used(smtp_sink):
    """Guards against logging 'sent' for a different address than was emailed."""
    _configure_smtp(smtp_sink.port)
    with app.app_context():
        send_email_notification("care@ableshomehealthcare.com", "Hi", "Body")
        assert "care@ableshomehealthcare.com" in smtp_sink.messages[0]
        log = NotificationLog.query.filter_by(status="sent").first()
        assert log.recipient == "care@ableshomehealthcare.com"


def test_forgot_password_emails_a_working_reset_link(smtp_sink):
    """The reset flow must deliver a usable link over real SMTP.

    The existing reset test only covers the development fallback that appears
    when email is skipped, so it never proved the emailed link works. Point the
    app at the SMTP sink, request a reset, extract the link from the message the
    server actually received, and reset through it.
    """
    app.config["WTF_CSRF_ENABLED"] = False
    _configure_smtp(smtp_sink.port)

    from app import app as flask_app
    from models import User
    from werkzeug.security import generate_password_hash, check_password_hash

    email = f"reset-{uuid.uuid4().hex[:10]}@example.com"
    with flask_app.app_context():
        user = User(name="Reset Target", email=email, role="client",
                    password=generate_password_hash("OldPass2026!"))
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    try:
        with flask_app.test_client() as client:
            response = client.post(
                "/auth/forgot-password", data={"email": email}, follow_redirects=True
            )
            assert response.status_code == 200
            # Email is configured, so the development fallback link must not appear.
            assert b"Development mode" not in response.data

        # The flow emails the user and a separate admin notice; find the user's.
        bodies = [_decoded_body(message) for message in smtp_sink.messages]
        reset_bodies = [b for b in bodies if "/auth/reset-password/" in b]
        assert len(reset_bodies) == 1
        marker = "/auth/reset-password/"
        start = reset_bodies[0].index(marker)
        tail = reset_bodies[0][start:]
        reset_path = tail.split()[0].strip().rstrip(">")

        with flask_app.test_client() as client:
            response = client.post(
                reset_path,
                data={"password": "BrandNewPass2026!", "confirm_password": "BrandNewPass2026!"},
                follow_redirects=False,
            )
            assert response.status_code == 302

        with flask_app.app_context():
            refreshed = db.session.get(User, user_id)
            assert check_password_hash(refreshed.password, "BrandNewPass2026!")
            log = NotificationLog.query.filter_by(
                recipient=email, status="sent"
            ).first()
            assert log is not None
    finally:
        with flask_app.app_context():
            User.query.filter_by(id=user_id).delete()
            db.session.commit()
