from datetime import timedelta
import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_FILE = os.environ.get("ENV_FILE", ".env")
load_dotenv(os.path.join(BASE_DIR, ENV_FILE))

def database_url():
    if os.environ.get("FLASK_ENV", "").lower() == "production" and not os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL is required when FLASK_ENV=production: refusing to "
            "boot on the local SQLite fallback with production settings."
        )
    url = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "instance", "ablehome.db")
    )

    if url.startswith("sqlite:///"):
        sqlite_path = url.replace("sqlite:///", "", 1)
        if sqlite_path and sqlite_path != ":memory:" and not os.path.isabs(sqlite_path):
            return "sqlite:///" + os.path.join(BASE_DIR, sqlite_path)

    # Some hosts still expose the older Heroku-style postgres:// URL.
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)

    # GoDaddy cPanel MySQL/MariaDB uses the pure-Python PyMySQL driver.
    # Normalize bare mysql:// URLs so SQLAlchemy selects that dialect.
    if url.startswith("mysql://"):
        return url.replace("mysql://", "mysql+pymysql://", 1)

    return url


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() == "true"


def is_production_environment(database_uri):
    flask_env = os.environ.get("FLASK_ENV", "").lower()
    if flask_env == "production":
        return True
    if env_bool("RENDER"):
        return True
    return bool(os.environ.get("DATABASE_URL")) and not database_uri.startswith("sqlite:///")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-later")
    SITE_BASE_URL = os.environ.get(
        "SITE_BASE_URL",
        "https://ableshomehealthcare.com"
    ).rstrip("/")
    SQLALCHEMY_DATABASE_URI = database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Shared hosting (cPanel MySQL/MariaDB) drops idle connections; pre-ping
    # transparently reconnects. Safe on SQLite and PostgreSQL too.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "instance", "uploads"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(5 * 1024 * 1024)))
    PRODUCTION_ENVIRONMENT = is_production_environment(SQLALCHEMY_DATABASE_URI)
    # Migrations run via an explicit deploy step (preDeployCommand / CLI) so a
    # single, deliberate strategy applies rather than multiple triggers.
    AUTO_MIGRATE_ON_STARTUP = env_bool("AUTO_MIGRATE_ON_STARTUP", False)
    REMEMBER_COOKIE_SECURE = PRODUCTION_ENVIRONMENT
    SESSION_COOKIE_SECURE = PRODUCTION_ENVIRONMENT
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_REFRESH_EACH_REQUEST = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get("SESSION_HOURS", "8")))
    WTF_CSRF_TIME_LIMIT = int(os.environ.get("WTF_CSRF_TIME_LIMIT", "3600"))
    FORCE_HTTPS = env_bool("FORCE_HTTPS", PRODUCTION_ENVIRONMENT)
    ACCOUNT_LOCKOUT_ATTEMPTS = int(os.environ.get("ACCOUNT_LOCKOUT_ATTEMPTS", "5"))
    ACCOUNT_LOCKOUT_MINUTES = int(os.environ.get("ACCOUNT_LOCKOUT_MINUTES", "15"))
    # Recovery-code enforcement defaults on in production; dev stays lenient.
    ADMIN_RECOVERY_CODES_REQUIRED = env_bool("ADMIN_RECOVERY_CODES_REQUIRED", PRODUCTION_ENVIRONMENT)
    # Breached-password (Have I Been Pwned) check. Fail-open on network errors;
    # disable only if outbound network calls are not acceptable.
    PASSWORD_BREACH_CHECK_ENABLED = env_bool("PASSWORD_BREACH_CHECK_ENABLED", True)

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_USE_TLS = env_bool("MAIL_USE_TLS", True)
    MAIL_DEFAULT_SENDER = os.environ.get(
        "MAIL_DEFAULT_SENDER",
        "admin@ableshomehealthcare.com"
    )
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@ableshomehealthcare.com")
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
    ENABLE_SMS_REMINDERS = env_bool("ENABLE_SMS_REMINDERS", False)
    VIRUS_SCAN_COMMAND = os.environ.get("VIRUS_SCAN_COMMAND", "")
    BUSINESS_DETAILS_CONFIRMED = env_bool("BUSINESS_DETAILS_CONFIRMED")
    LEGAL_SCOPE_CONFIRMED = env_bool("LEGAL_SCOPE_CONFIRMED")
    LEGAL_PRIVACY_REVIEW_CONFIRMED = env_bool("LEGAL_PRIVACY_REVIEW_CONFIRMED")
    APPROVED_PHOTOS_READY = env_bool("APPROVED_PHOTOS_READY")
    GOOGLE_ANALYTICS_ID = os.environ.get("GOOGLE_ANALYTICS_ID", "")
    GOOGLE_SITE_VERIFICATION = os.environ.get("GOOGLE_SITE_VERIFICATION", "")



