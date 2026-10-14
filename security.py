from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re
import secrets
import subprocess

from flask import abort, current_app, request
from flask_login import current_user
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models import AdminRecoveryCode, AuditLog


LOGIN_ATTEMPTS = defaultdict(list)
LOGIN_LIMIT = 5
LOGIN_WINDOW = timedelta(minutes=10)
ROLE_PERMISSIONS = {
    "admin": {
        "admin:access",
        "admin:audit",
        "admin:security",
        "admin:reports",
        "admin:users",
        "documents:manage",
        "appointments:manage",
    },
    "caregiver": {
        "caregiver:schedule",
        "caregiver:availability",
        "documents:own",
        "messages:own",
    },
    "client": {
        "appointments:own",
        "documents:own",
        "messages:own",
        "profile:own",
    },
}


def require_role(*roles):
    if not current_user.is_authenticated or current_user.role not in roles:
        abort(403)


def has_permission(permission):
    if not current_user.is_authenticated:
        return False
    return permission in ROLE_PERMISSIONS.get(current_user.role, set())


def require_permission(permission):
    if not has_permission(permission):
        abort(403)


def password_is_strong(password):
    return len(password_feedback(password)["missing"]) == 0


def password_feedback(password):
    password = password or ""
    missing = []

    if len(password) < 12:
        missing.append("at least 12 characters")
    if not re.search(r"[A-Z]", password):
        missing.append("an uppercase letter")
    if not re.search(r"[a-z]", password):
        missing.append("a lowercase letter")
    if not re.search(r"\d", password):
        missing.append("a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        missing.append("a symbol")

    common_words = ["password", "admin", "client", "caregiver", "ables", "homecare", "123456"]
    lowered = password.lower()
    if any(word in lowered for word in common_words):
        missing.append("no common words like password, admin, ables, or 123456")

    score = 6 - len(missing)
    if score >= 6:
        label = "Strong"
    elif score >= 4:
        label = "Good"
    elif score >= 2:
        label = "Fair"
    else:
        label = "Weak"

    return {
        "label": label,
        "missing": missing,
    }


def generate_recovery_codes(admin_user, count=8):
    codes = []
    AdminRecoveryCode.query.filter_by(admin_id=admin_user.id, used_at=None).delete()
    for _ in range(count):
        code = secrets.token_hex(4).upper()
        codes.append(code)
        db.session.add(AdminRecoveryCode(admin_id=admin_user.id, code_hash=generate_password_hash(code)))
    audit("generate_admin_recovery_codes", "User", admin_user.id, f"Generated {count} admin recovery codes")
    return codes


def admin_recovery_codes_enabled(admin_user):
    if not admin_user or admin_user.role != "admin":
        return False
    return AdminRecoveryCode.query.filter_by(admin_id=admin_user.id, used_at=None).count() > 0


def verify_admin_recovery_code(admin_user, code):
    code = (code or "").strip().upper()
    if not admin_recovery_codes_enabled(admin_user):
        return True
    if not code:
        return False

    for recovery_code in AdminRecoveryCode.query.filter_by(admin_id=admin_user.id, used_at=None).all():
        if check_password_hash(recovery_code.code_hash, code):
            recovery_code.used_at = utc_now()
            audit("use_admin_recovery_code", "User", admin_user.id, "Admin recovery code accepted")
            return True
    return False


def scan_upload_stream(file_storage):
    if not file_storage:
        return True, "No file"

    position = file_storage.stream.tell()
    sample = file_storage.stream.read(4096)
    file_storage.stream.seek(position)

    if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in sample:
        return False, "Virus test signature detected"

    command = current_app.config.get("VIRUS_SCAN_COMMAND")
    if not command:
        return True, "No external scanner configured"

    try:
        result = subprocess.run(
            command.split(),
            input=sample,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        current_app.logger.warning("Upload virus scan command failed: %s", exc)
        return False, "Virus scanner unavailable"

    if result.returncode != 0:
        return False, (result.stderr or result.stdout or b"Upload scan failed").decode("utf-8", errors="replace")[:300]
    return True, "Scan passed"


def rate_limit_key():
    return request.remote_addr or "unknown"


def login_is_limited():
    key = rate_limit_key()
    now = datetime.now(timezone.utc)
    LOGIN_ATTEMPTS[key] = [item for item in LOGIN_ATTEMPTS[key] if now - item < LOGIN_WINDOW]
    return len(LOGIN_ATTEMPTS[key]) >= LOGIN_LIMIT


def record_login_failure():
    LOGIN_ATTEMPTS[rate_limit_key()].append(datetime.now(timezone.utc))


def clear_login_failures():
    LOGIN_ATTEMPTS.pop(rate_limit_key(), None)


PUBLIC_FORM_ATTEMPTS = defaultdict(list)
PUBLIC_FORM_LIMIT = 10
PUBLIC_FORM_WINDOW = timedelta(minutes=10)


def public_form_is_limited():
    """Separate throttle bucket for unauthenticated public forms.

    Kept apart from the login bucket so form spam can never lock an IP
    out of signing in.
    """
    key = rate_limit_key()
    now = datetime.now(timezone.utc)
    PUBLIC_FORM_ATTEMPTS[key] = [
        item for item in PUBLIC_FORM_ATTEMPTS[key] if now - item < PUBLIC_FORM_WINDOW
    ]
    return len(PUBLIC_FORM_ATTEMPTS[key]) >= PUBLIC_FORM_LIMIT


def record_public_form():
    PUBLIC_FORM_ATTEMPTS[rate_limit_key()].append(datetime.now(timezone.utc))


def utc_now():
    return datetime.now(timezone.utc)


def normalize_dt(value):
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def user_is_locked(user):
    locked_until = normalize_dt(getattr(user, "locked_until", None))
    return bool(locked_until and locked_until > utc_now())


def lockout_remaining_minutes(user):
    locked_until = normalize_dt(getattr(user, "locked_until", None))
    if not locked_until:
        return 0
    remaining = locked_until - utc_now()
    return max(0, int(remaining.total_seconds() // 60) + 1)


def record_user_login_failure(user):
    if not user:
        return

    user.failed_login_count = (user.failed_login_count or 0) + 1
    max_attempts = current_app.config.get("ACCOUNT_LOCKOUT_ATTEMPTS", 5)

    if user.failed_login_count >= max_attempts:
        lockout_minutes = current_app.config.get("ACCOUNT_LOCKOUT_MINUTES", 15)
        user.locked_until = utc_now() + timedelta(minutes=lockout_minutes)
        audit("account_locked", "User", user.id, f"Locked after {user.failed_login_count} failed attempts")


def clear_user_login_failures(user):
    if not user:
        return

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utc_now()


def audit(action, entity_type=None, entity_id=None, details=None):
    user_id = current_user.id if getattr(current_user, "is_authenticated", False) else None
    db.session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
    )


