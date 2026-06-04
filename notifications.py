from email.message import EmailMessage
from html import escape
import smtplib
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import base64

from flask import current_app

from extensions import db
from models import NotificationLog


def log_notification(channel, recipient, subject, status, provider="", related_type=None, related_id=None, error=None):
    try:
        db.session.add(NotificationLog(
            channel=channel,
            recipient=recipient,
            subject=subject,
            status=status,
            provider=provider,
            related_type=related_type,
            related_id=related_id,
            error=error,
        ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning("Could not queue notification log: %s", exc)


def send_email_notification(to_email, subject, body, reply_to=None, related_type=None, related_id=None, html_body=None):
    """Send email when SMTP is configured; otherwise log it."""
    app = current_app
    mail_server = app.config.get("MAIL_SERVER")

    if not mail_server or not to_email:
        app.logger.info("Email notification skipped: %s\n%s", subject, body)
        log_notification("email", to_email, subject, "skipped", "smtp", related_type, related_id, "SMTP or recipient missing")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = app.config.get("MAIL_DEFAULT_SENDER")
    message["To"] = to_email

    if reply_to:
        message["Reply-To"] = reply_to

    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        mail_port = int(app.config.get("MAIL_PORT", 587))
        with smtplib.SMTP(mail_server, mail_port) as smtp:
            if app.config.get("MAIL_USE_TLS"):
                smtp.starttls()

            username = app.config.get("MAIL_USERNAME")
            password = app.config.get("MAIL_PASSWORD")

            if username and password:
                smtp.login(username, password)

            smtp.send_message(message)
    except Exception as exc:
        app.logger.exception("Email notification failed: %s", subject)
        log_notification("email", to_email, subject, "failed", "smtp", related_type, related_id, str(exc))
        return False

    log_notification("email", to_email, subject, "sent", "smtp", related_type, related_id)
    return True


def admin_email_html(subject, body):
    safe_subject = escape(subject)
    rows = []
    for line in body.splitlines():
        if not line.strip():
            continue
        if ":" in line:
            label, value = line.split(":", 1)
            rows.append(
                f"<tr><th style=\"padding:9px 10px;text-align:left;border-bottom:1px solid #e6eef5;color:#2b5d80;width:36%;\">{escape(label.strip())}</th><td style=\"padding:9px 10px;border-bottom:1px solid #e6eef5;color:#1f2f3f;\">{escape(value.strip())}</td></tr>"
            )
        else:
            rows.append(f"<tr><td colspan=\"2\" style=\"padding:9px 10px;border-bottom:1px solid #e6eef5;color:#1f2f3f;\">{escape(line.strip())}</td></tr>")
    table = "\n".join(rows)
    return f"""<!doctype html>
<html>
<body style="margin:0;background:#f3f7fb;font-family:Arial,sans-serif;color:#173b63;">
  <div style="max-width:680px;margin:0 auto;padding:24px;">
    <div style="background:#ffffff;border:1px solid #d8e4ef;border-radius:8px;overflow:hidden;">
      <div style="background:#2b5d80;color:#ffffff;padding:18px 22px;">
        <h1 style="font-size:22px;margin:0;">{safe_subject}</h1>
        <p style="margin:6px 0 0;">Ables Home Healthcare website notification</p>
      </div>
      <div style="padding:22px;">
        <table style="border-collapse:collapse;width:100%;font-size:15px;">
          {table}
        </table>
        <p style="border-top:1px solid #d8e4ef;margin-top:22px;padding-top:14px;color:#5f7185;">
          For emergencies, call 911. This email was generated from the website.
        </p>
      </div>
    </div>
  </div>
</body>
</html>"""


def send_admin_notification(subject, body, reply_to=None, related_type=None, related_id=None):
    admin_email = current_app.config.get("ADMIN_EMAIL")
    return send_email_notification(
        admin_email,
        subject,
        body,
        reply_to=reply_to,
        related_type=related_type,
        related_id=related_id,
        html_body=admin_email_html(subject, body),
    )


def send_sms_notification(to_number, body, related_type=None, related_id=None):
    app = current_app
    if not app.config.get("ENABLE_SMS_REMINDERS"):
        app.logger.info("SMS skipped because ENABLE_SMS_REMINDERS is false.")
        log_notification("sms", to_number, "SMS reminder", "skipped", "twilio", related_type, related_id, "SMS disabled")
        return False

    sid = app.config.get("TWILIO_ACCOUNT_SID")
    token = app.config.get("TWILIO_AUTH_TOKEN")
    from_number = app.config.get("TWILIO_FROM_NUMBER")
    if not sid or not token or not from_number or not to_number:
        app.logger.info("SMS skipped: Twilio settings or recipient phone missing.")
        log_notification("sms", to_number, "SMS reminder", "skipped", "twilio", related_type, related_id, "Twilio settings or recipient missing")
        return False

    payload = urlencode({
        "From": from_number,
        "To": to_number,
        "Body": body[:1500],
    }).encode("utf-8")
    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    request = Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=payload,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            app.logger.info("SMS reminder sent with status %s", response.status)
    except Exception as exc:
        app.logger.exception("SMS notification failed")
        log_notification("sms", to_number, "SMS reminder", "failed", "twilio", related_type, related_id, str(exc))
        return False
    log_notification("sms", to_number, "SMS reminder", "sent", "twilio", related_type, related_id)
    return True


