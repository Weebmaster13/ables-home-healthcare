from logging.handlers import RotatingFileHandler
from pathlib import Path
import logging

from flask import Flask, redirect, request, url_for
from flask_login import current_user
from flask_migrate import upgrade
from config import Config
from extensions import csrf, db, login_manager, migrate

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    setattr(login_manager, "login_view", "auth.login")
    login_manager.session_protection = "strong"

    # BLUEPRINTS
    from auth.routes import auth_bp
    from routes import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    configure_logging(app)
    register_https_enforcement(app)
    register_admin_recovery_gate(app)
    register_security_headers(app)
    register_error_handlers(app)
    register_template_helpers(app)
    run_startup_migrations(app)

    return app


def run_startup_migrations(app):
    if not app.config.get("AUTO_MIGRATE_ON_STARTUP"):
        return

    with app.app_context():
        try:
            upgrade()
            app.logger.info("Database migrations applied on startup.")
        except Exception:
            app.logger.exception("Database migration failed on startup.")
            raise


def configure_logging(app):
    """Log to stdout/stderr (captured by container platforms) plus a local file.

    Local rotating files are not durable in ephemeral containers, so the
    stream handler is what platform log aggregation actually collects.
    """
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    app.logger.addHandler(stream_handler)

    try:
        log_dir = Path(app.instance_path) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=5)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        app.logger.addHandler(file_handler)
    except OSError:
        app.logger.warning("Local log file unavailable; continuing with stream logging only.")

    app.logger.setLevel(logging.INFO)


def register_template_helpers(app):
    @app.context_processor
    def inject_portal_counts():
        from flask_login import current_user
        from models import PortalMessage

        counts = {
            "admin_unread_messages": 0,
            "user_unread_messages": 0,
            "max_upload_mb": int(app.config.get("MAX_CONTENT_LENGTH", 0) / (1024 * 1024)),
        }
        if not current_user.is_authenticated:
            return counts

        if current_user.role == "admin":
            counts["admin_unread_messages"] = PortalMessage.query.filter_by(admin_seen=False).count()
        else:
            counts["user_unread_messages"] = PortalMessage.query.filter_by(
                sender_id=current_user.id,
                sender_seen_reply=False,
            ).count()

        return counts


def register_admin_recovery_gate(app):
    @app.before_request
    def require_admin_recovery_setup():
        if not current_user.is_authenticated or current_user.role != "admin":
            return None

        allowed_endpoints = {
            "main.admin_security",
            "main.admin_generate_recovery_codes",
            "auth.logout",
            "static",
        }
        if request.endpoint in allowed_endpoints:
            return None

        if not app.config.get("ADMIN_RECOVERY_CODES_REQUIRED"):
            return None

        from security import admin_recovery_codes_enabled
        if not admin_recovery_codes_enabled(current_user):
            return redirect(url_for("main.admin_security"))

        return None


def register_https_enforcement(app):
    @app.before_request
    def enforce_https():
        if not app.config.get("FORCE_HTTPS"):
            return None

        forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
        is_secure = request.is_secure or forwarded_proto == "https"

        if is_secure or request.host.startswith("127.0.0.1") or request.host.startswith("localhost"):
            return None

        return redirect(request.url.replace("http://", "https://", 1), code=301)


def register_security_headers(app):
    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://translate.google.com https://translate.googleapis.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://translate.googleapis.com https://translate.google.com; "
            "img-src 'self' data: https: http://127.0.0.1:5000; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
            "connect-src 'self' https://translate.googleapis.com https://translate.google.com; "
            "frame-src 'self' https://translate.google.com; "
            "frame-ancestors 'self'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

        private_prefixes = (
            "/admin",
            "/auth",
            "/dashboard",
            "/profile",
            "/request-care",
            "/caregiver",
            "/messages",
            "/documents",
            "/my-appointments",
            "/appointment",
        )
        if request.path.startswith(private_prefixes):
            response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")

        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        return response


def register_error_handlers(app):
    from flask import render_template

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled server error: %s", error)
        return render_template("errors/500.html"), 500


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)


