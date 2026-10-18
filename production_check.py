from app import app
from config import DEVELOPMENT_SECRET_KEYS

PLACEHOLDER_MARKERS = (
    "REPLACE_",
    "replace-with",
    "example.com",
    "user:password",
)


def configured(value):
    text = str(value or "")
    return bool(text) and not any(marker in text for marker in PLACEHOLDER_MARKERS)


def database_driver_available(database_uri):
    """True when the Python driver for the configured database is importable."""
    import importlib.util

    uri = str(database_uri or "")
    if uri.startswith(("postgresql://", "postgres://")):
        return importlib.util.find_spec("psycopg2") is not None
    if uri.startswith(("mysql://", "mysql+pymysql://", "mariadb://")):
        return importlib.util.find_spec("pymysql") is not None
    if uri.startswith("sqlite:///"):
        return True
    return True


def database_backup_tool_available(database_uri):
    """True when the host has the dump tool matching the configured database."""
    import shutil

    uri = str(database_uri or "")
    if uri.startswith(("postgresql://", "postgres://")):
        return shutil.which("pg_dump") is not None
    if uri.startswith(("mysql://", "mysql+pymysql://", "mariadb://")):
        return shutil.which("mysqldump") is not None or shutil.which("mariadb-dump") is not None
    return True


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
        "Set DATABASE_URL to PostgreSQL or MySQL/MariaDB before collecting real client data.",
    ),
    (
        "Database driver installed",
        lambda c: database_driver_available(c.get("SQLALCHEMY_DATABASE_URI", "")),
        "Install the driver for the configured database (psycopg2-binary for PostgreSQL, PyMySQL for MySQL/MariaDB).",
    ),
    (
        "Database backup tool installed",
        lambda c: database_backup_tool_available(c.get("SQLALCHEMY_DATABASE_URI", "")),
        "Install the matching dump tool on the host (pg_dump for PostgreSQL, mysqldump/mariadb-dump for MySQL/MariaDB).",
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
        "Stable backup fingerprint key",
        lambda c: configured(c.get("BACKUP_FINGERPRINT_KEY")),
        "Set BACKUP_FINGERPRINT_KEY to a stable, backed-up value. It authenticates "
        "backup fingerprints; without it the key is derived from SECRET_KEY, so "
        "rotating SECRET_KEY would make older backups unverifiable.",
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


