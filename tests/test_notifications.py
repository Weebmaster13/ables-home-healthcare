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
