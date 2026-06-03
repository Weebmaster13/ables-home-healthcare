from notifications import send_admin_notification
from app import app


def main():
    with app.app_context():
        sent = send_admin_notification(
            "Ables Home Healthcare SMTP test",
            "This is a test email from the Ables Home Healthcare website configuration.",
        )

    if sent:
        print("SMTP test email sent. Check ADMIN_EMAIL inbox.")
        return

    print("SMTP is not configured. Set MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD, and ADMIN_EMAIL.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()


