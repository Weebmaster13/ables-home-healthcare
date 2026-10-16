from app import app


DEVELOPMENT_SECRET_KEYS = {
    "dev-secret-change-later",
    "dev-change-this-before-production",
}

PLACEHOLDER_MARKERS = (
    "REPLACE_",
    "replace-with",
    "example.com",
    "user:password",
)


def configured(value):
    text = str(value or "")
    return bool(text) and not any(marker in text for marker in PLACEHOLDER_MARKERS)


CHECKS = [
    (
        "Production mode",
        lambda c: c.get("SESSION_COOKIE_SECURE") is True,
        "Set FLASK_ENV=production.",
    ),
    (
        "Secret key",
        lambda c: configured(c.get("SECRET_KEY")) and c.get("SECRET_KEY") not in DEVELOPMENT_SECRET_KEYS,
        "Set a long random SECRET_KEY.",
    ),
    (
        "HTTPS site URL",
        lambda c: c.get("SITE_BASE_URL", "").startswith("https://"),
        "Set SITE_BASE_URL to the final HTTPS domain.",
    ),
    (
        "Production database",
        lambda c: configured(c.get("SQLALCHEMY_DATABASE_URI")) and not c.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite:///"),
        "Set DATABASE_URL to PostgreSQL before collecting real client data.",
    ),
    (
        "SMTP email",
        lambda c: configured(c.get("MAIL_SERVER")) and configured(c.get("MAIL_USERNAME")) and configured(c.get("MAIL_PASSWORD")) and configured(c.get("ADMIN_EMAIL")),
        "Set real MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD, and ADMIN_EMAIL.",
    ),
    (
        "SMS reminders optional",
        lambda c: (
            c.get("ENABLE_SMS_REMINDERS") is False
            or (
                bool(c.get("TWILIO_ACCOUNT_SID"))
                and bool(c.get("TWILIO_AUTH_TOKEN"))
                and bool(c.get("TWILIO_FROM_NUMBER"))
            )
        ),
        "Either set ENABLE_SMS_REMINDERS=false or configure TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER.",
    ),
    (
        "HTTPS enforcement",
        lambda c: c.get("FORCE_HTTPS") is True,
        "Set FORCE_HTTPS=true in production.",
    ),
    (
        "Admin recovery codes required",
        lambda c: c.get("ADMIN_RECOVERY_CODES_REQUIRED") is True,
        "Set ADMIN_RECOVERY_CODES_REQUIRED=true in production.",
    ),
    (
        "Upload antivirus scanner",
        lambda c: configured(c.get("VIRUS_SCAN_COMMAND")),
        "Set VIRUS_SCAN_COMMAND (for example: clamdscan --stream) so uploads are really scanned.",
    ),
    (
        "Durable upload storage",
        lambda c: bool(c.get("UPLOAD_FOLDER")) and not str(c.get("UPLOAD_FOLDER", "")).startswith(("/tmp", "/var/tmp")),
        "Point UPLOAD_FOLDER at persistent storage; /tmp is wiped on restart or redeploy.",
    ),
    (
        "Secure sessions",
        lambda c: c.get("SESSION_COOKIE_HTTPONLY") is True and c.get("SESSION_COOKIE_SAMESITE") in {"Lax", "Strict"},
        "Keep HTTP-only session cookies and SameSite enabled.",
    ),
    (
        "Business details confirmed",
        lambda c: c.get("BUSINESS_DETAILS_CONFIRMED") is True,
        "Set BUSINESS_DETAILS_CONFIRMED=true after owner review.",
    ),
    (
        "Legal service scope confirmed",
        lambda c: c.get("LEGAL_SCOPE_CONFIRMED") is True,
        "Set LEGAL_SCOPE_CONFIRMED=true after service/licensing review.",
    ),
    (
        "Legal privacy review confirmed",
        lambda c: c.get("LEGAL_PRIVACY_REVIEW_CONFIRMED") is True,
        "Set LEGAL_PRIVACY_REVIEW_CONFIRMED=true after privacy/document-upload review.",
    ),
    (
        "Approved photos ready",
        lambda c: c.get("APPROVED_PHOTOS_READY") is True,
        "Set APPROVED_PHOTOS_READY=true after replacing development images.",
    ),
]


def main():
    failures = []

    with app.app_context():
        for name, predicate, fix in CHECKS:
            ready = predicate(app.config)
            print(f"[{'OK' if ready else 'TODO'}] {name}")
            if not ready:
                failures.append(fix)

    if failures:
        print("\nNext actions:")
        for item in failures:
            print(f"- {item}")
        raise SystemExit(1)

    print("\nProduction configuration checks passed.")


if __name__ == "__main__":
    main()


