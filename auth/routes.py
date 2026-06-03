from flask import Blueprint, current_app, render_template, request, redirect, session, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import generate_password_hash, check_password_hash

from models import User
from extensions import db
from notifications import send_admin_notification, send_email_notification
from security import (
    audit,
    clear_login_failures,
    clear_user_login_failures,
    login_is_limited,
    password_feedback,
    password_is_strong,
    record_login_failure,
    record_user_login_failure,
    admin_recovery_codes_enabled,
    user_is_locked,
    verify_admin_recovery_code,
)

# ---------------- AUTH BLUEPRINT ----------------
auth_bp = Blueprint(
    "auth",
    __name__,
    url_prefix="/auth"
)


# ---------------- REGISTER ----------------
@auth_bp.route("/register", methods=["GET", "POST"])
def register():

    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.register"))

        if not password_is_strong(password):
            feedback = password_feedback(password)
            flash("Password is not safe enough. Add " + ", ".join(feedback["missing"]) + ".", "danger")
            return redirect(url_for("auth.register"))

        existing_user = User.query.filter_by(
            email=email
        ).first()

        if existing_user:
            flash("Email already registered.", "danger")
            return redirect(url_for("auth.register"))

        user = User(
            name=name,
            email=email,
            password=generate_password_hash(password),
            role="client",
            password_changed_at=db.func.now(),
        )

        db.session.add(user)
        db.session.commit()

        flash("Account created successfully.", "success")

        return redirect(url_for("auth.login"))

    return render_template("register.html")


# ---------------- LOGIN ----------------
@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":

        if login_is_limited():
            flash("Too many failed login attempts. Please wait and try again.", "danger")
            return redirect(url_for("auth.login"))

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        recovery_code = request.form.get("recovery_code", "").strip()

        user = User.query.filter_by(
            email=email
        ).first()

        if not user:
            record_login_failure()
            flash("Invalid email or password.", "danger")
            return redirect(url_for("auth.login"))

        if not user.is_active_account:
            flash("This account is inactive. Please contact the office.", "danger")
            return redirect(url_for("auth.login"))

        if user_is_locked(user):
            flash("Too many failed login attempts. Please wait and try again or contact the office.", "danger")
            return redirect(url_for("auth.login"))

        if not check_password_hash(
            user.password,
            password
        ):
            record_login_failure()
            record_user_login_failure(user)
            audit("login_failure", "User", user.id, "Failed password attempt")
            db.session.commit()
            flash("Invalid email or password.", "danger")
            return redirect(url_for("auth.login"))

        recovery_required = current_app.config.get("ADMIN_RECOVERY_CODES_REQUIRED")
        if recovery_required and user.role == "admin" and admin_recovery_codes_enabled(user) and not verify_admin_recovery_code(user, recovery_code):
            record_login_failure()
            record_user_login_failure(user)
            audit("admin_recovery_code_failure", "User", user.id, "Missing or invalid admin recovery code")
            db.session.commit()
            flash("Enter a valid admin recovery code.", "danger")
            return redirect(url_for("auth.login"))

        clear_login_failures()
        clear_user_login_failures(user)
        audit("login_success", "User", user.id, "Successful login")
        db.session.commit()
        session.permanent = True
        login_user(user)

        flash(
            f"Welcome back, {user.name}!",
            "success"
        )

        return redirect(
            url_for("main.dashboard")
        )

    return render_template("login.html")


# ---------------- LOGOUT ----------------
@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    audit("logout", "User", current_user.id, "User logged out")
    db.session.commit()

    logout_user()

    flash(
        "You have been logged out.",
        "info"
    )

    return redirect(
        url_for("auth.login")
    )


def reset_serializer():
    from flask import current_app

    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    dev_reset_url = session.pop("dev_password_reset_url", None)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            token = reset_serializer().dumps(user.email, salt="password-reset")
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            body = (
                f"Hello {user.name},\n\n"
                "A password reset was requested for your Ables Home Healthcare account.\n\n"
                f"Reset your password here: {reset_url}\n\n"
                "This link expires in 1 hour. If you did not request this, please ignore this email or contact the office."
            )
            sent = send_email_notification(
                user.email,
                "Reset your Ables Home Healthcare password",
                body,
                related_type="User",
                related_id=user.id,
            )
            send_admin_notification(
                "Password reset requested",
                f"Password reset requested for {user.email}. Delivery status: {'sent' if sent else 'not sent'}.\nNo reset link is included for privacy.",
                reply_to=user.email,
                related_type="User",
                related_id=user.id,
            )
            audit("password_reset_requested", "User", user.id, f"Delivery status: {'sent' if sent else 'not sent'}")
            db.session.commit()

            if not sent and not current_app.config.get("SESSION_COOKIE_SECURE"):
                session["dev_password_reset_url"] = reset_url

        flash("If that account exists, a password reset email has been sent.", "success")
        return redirect(url_for("auth.forgot_password"))

    return render_template("forgot_password.html", dev_reset_url=dev_reset_url)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    try:
        email = reset_serializer().loads(token, salt="password-reset", max_age=3600)
    except SignatureExpired:
        flash("This reset link expired. Please request a new one.", "danger")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        flash("Invalid reset link.", "danger")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter_by(email=email).first_or_404()

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.reset_password", token=token))

        if not password_is_strong(password):
            feedback = password_feedback(password)
            flash("Password is not safe enough. Add " + ", ".join(feedback["missing"]) + ".", "danger")
            return redirect(url_for("auth.reset_password", token=token))

        user.password = generate_password_hash(password)
        user.password_changed_at = db.func.now()
        clear_user_login_failures(user)
        db.session.commit()

        flash("Password updated. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not check_password_hash(current_user.password, current_password):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("auth.change_password"))

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.change_password"))

        if not password_is_strong(password):
            feedback = password_feedback(password)
            flash("Password is not safe enough. Add " + ", ".join(feedback["missing"]) + ".", "danger")
            return redirect(url_for("auth.change_password"))

        current_user.password = generate_password_hash(password)
        current_user.password_changed_at = db.func.now()
        audit("change_password", "User", current_user.id, "Password changed")
        db.session.commit()

        flash("Password changed successfully.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("change_password.html")


