from logging.handlers import RotatingFileHandler
from pathlib import Path
import gzip
import logging
import os

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, logout_user
from flask_migrate import upgrade
from flask_wtf.csrf import CSRFError
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
    register_canonical_host_redirect(app)
    register_session_staleness_check(app)
    register_admin_recovery_gate(app)
    register_security_headers(app)
    register_compression(app)
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
    log_dir = Path(app.instance_path) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=5)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


def register_template_helpers(app):
    @app.template_global()
    def asset_version(filename):
        """Cache-busting token for a static file (its last-modified time)."""
        try:
            path = os.path.join(app.static_folder, filename)
            return int(os.path.getmtime(path))
        except OSError:
            return 0

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


def register_session_staleness_check(app):
    @app.before_request
    def reject_stale_password_session():
        # Password changes/resets bump password_changed_at; sessions stamped
        # with an older value are logged out on their next request. Sessions
        # predating this feature carry no stamp and are logged out once
        # (one-time re-login, fail-closed). Legacy NULL timestamps skip
        # the check so old rows are never locked out by it.
        if not current_user.is_authenticated or request.endpoint == "static":
            return None
        from security import session_password_stamp
        current_stamp = session_password_stamp(current_user)
        if not current_stamp:
            return None
        if session.get("pwd_ts") != current_stamp:
            logout_user()
            session.pop("pwd_ts", None)
            return redirect(url_for("auth.login"))
        return None


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


def _is_loopback_request():
    host = (request.host or "").split(":")[0].lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def register_https_enforcement(app):
    @app.before_request
    def enforce_https():
        if not app.config.get("FORCE_HTTPS"):
            return None

        forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
        is_secure = request.is_secure or forwarded_proto == "https"

        if is_secure or _is_loopback_request():
            return None

        # Build the target from the configured canonical host rather than
        # the attacker-controlled Host header.
        base = (app.config.get("SITE_BASE_URL") or "").rstrip("/")
        if base:
            target = base + request.full_path
        else:
            target = "https://" + (request.host or "") + request.full_path
        return redirect(target, code=301)


def register_canonical_host_redirect(app):
    @app.before_request
    def canonical_host_and_trailing_slash():
        if not app.config.get("PRODUCTION_ENVIRONMENT") or _is_loopback_request():
            return None

        host = (request.host or "").split(":")[0].lower()
        if host.startswith("www."):
            target = "https://" + host[4:] + request.full_path
            return redirect(target, code=301)

        # /about/ -> /about when the slashless rule actually exists.
        if request.method in ("GET", "HEAD") and request.path not in ("/", "") and request.path.endswith("/"):
            candidate = request.path.rstrip("/")
            rules = {rule.rule for rule in app.url_map.iter_rules()}
            if candidate in rules:
                target = candidate
                if request.query_string:
                    target += "?" + request.query_string.decode("latin-1")
                return redirect(target, code=301)
        return None


def register_compression(app):
    @app.after_request
    def compress_text_response(response):
        if response.direct_passthrough:
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if response.headers.get("Content-Encoding"):
            return response
        if "gzip" not in request.accept_encodings:
            return response

        content_type = (response.content_type or "").lower()
        compressible = (
            content_type.startswith("text/")
            or content_type.startswith("application/javascript")
            or content_type.startswith("application/json")
            or content_type.startswith("application/xml")
            or content_type.startswith("image/svg+xml")
        )
        if not compressible:
            return response

        raw = response.get_data()
        if len(raw) < 500:
            return response
        compressed = gzip.compress(raw, 6)
        if len(compressed) >= len(raw):
            return response
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        response.headers["Vary"] = "Accept-Encoding"
        return response


def register_security_headers(app):
    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault("X-DNS-Prefetch-Control", "off")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://translate.google.com https://translate.googleapis.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com https://translate.googleapis.com https://translate.google.com https://www.gstatic.com; "
            "img-src 'self' data: https:; "
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
            "/healthz",
            "/launch-readiness",
        )
        if request.path.startswith(private_prefixes):
            response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")

        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        return response


def register_error_handlers(app):
    @app.errorhandler(CSRFError)
    def csrf_error(error):
        app.logger.warning("CSRF validation failed: %s", error.description)
        flash("Your session expired or the form was out of date. Please try again.", "warning")
        return render_template("errors/400.html"), 400

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("errors/400.html"), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def request_too_large(error):
        return render_template("errors/413.html"), 413

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled server error: %s", error)
        return render_template("errors/500.html"), 500


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"})


