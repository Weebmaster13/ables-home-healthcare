from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re
import secrets
import subprocess

from flask import abort, current_app, flash, request
from flask_login import current_user
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models import AdminRecoveryCode, AuditLog, LoginAttempt


LOGIN_ATTEMPTS = defaultdict(list)
LOGIN_LIMIT = 5
LOGIN_WINDOW = timedelta(minutes=10)
# Public forms share the persistent LoginAttempt store under a namespaced key so
# bot floods are throttled across workers instead of per-process.
PUBLIC_FORM_WINDOW = timedelta(minutes=10)
PUBLIC_FORM_LIMIT = 8
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


def password_is_breached(password):
    """Check the password against the Have I Been Pwned range API.

    Uses k-anonymity: only the first 5 characters of the SHA-1 hash leave the
    server. Returns False (fail-open) if the network check cannot run, so a
    third-party outage never blocks account creation.
    """
    import hashlib
    from urllib.error import URLError
    from urllib.request import Request as UrlRequest, urlopen

    if not password:
        return False

    if not current_app.config.get("PASSWORD_BREACH_CHECK_ENABLED", True):
        return False

    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]

    try:
        request = UrlRequest(
            f"https://api.pwnedpasswords.com/range/{prefix}",
            headers={"User-Agent": "ables-home-healthcare-password-check"},
        )
        with urlopen(request, timeout=3) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (URLError, OSError, ValueError):
        return False

    return any(line.split(":", 1)[0].strip() == suffix for line in body.splitlines())


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


EICAR_SIGNATURE = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"


def read_full_upload(file_storage):
    """Return the entire upload as bytes, restoring the stream position."""
    stream = file_storage.stream
    position = stream.tell()
    try:
        stream.seek(0)
        return stream.read()
    finally:
        stream.seek(position)


def read_upload_head(file_storage, size=4096):
    """Return the leading bytes of the upload, restoring the stream position."""
    stream = file_storage.stream
    position = stream.tell()
    try:
        stream.seek(0)
        return stream.read(size)
    finally:
        stream.seek(position)


def contains_eicar_signature(data):
    """Detect the EICAR signature even when it straddles a read boundary."""
    return EICAR_SIGNATURE in data


def scan_upload_stream(file_storage):
    if not file_storage:
        return True, "No file"

    contents = read_full_upload(file_storage)

    if contains_eicar_signature(contents):
        return False, "Virus test signature detected"

    command = current_app.config.get("VIRUS_SCAN_COMMAND")
    if not command:
        # Fail closed in production: without an external scanner, client and
        # caregiver documents would be stored unscanned. The owner can opt in
        # to the documented risk with UPLOAD_SCAN_FAIL_OPEN=true.
        if current_app.config.get("PRODUCTION_ENVIRONMENT") and not current_app.config.get("UPLOAD_SCAN_FAIL_OPEN"):
            return False, "No antivirus scanner configured"
        return True, "No external scanner configured"

    # The external scanner receives the complete file on stdin, not a sample,
    # so payloads anywhere in the upload are examined.
    try:
        result = subprocess.run(
            command.split(),
            input=contents,
            capture_output=True,
            timeout=30,
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


def _recent_attempts(key, now, window):
    """Return recent attempts from the shared store, falling back to memory."""
    cutoff = now - window
    try:
        LoginAttempt.query.filter(LoginAttempt.attempted_at < now - LOGIN_WINDOW).delete()
        db.session.commit()
        return LoginAttempt.query.filter(
            LoginAttempt.key == key,
            LoginAttempt.attempted_at >= cutoff,
        ).count()
    except Exception:
        db.session.rollback()
        LOGIN_ATTEMPTS[key] = [item for item in LOGIN_ATTEMPTS[key] if now - item < window]
        return len(LOGIN_ATTEMPTS[key])


def login_is_limited():
    key = rate_limit_key()
    now = datetime.now(timezone.utc)
    return _recent_attempts(key, now, LOGIN_WINDOW) >= LOGIN_LIMIT


def record_login_failure():
    key = rate_limit_key()
    try:
        db.session.add(LoginAttempt(key=key))
        db.session.commit()
    except Exception:
        db.session.rollback()
        LOGIN_ATTEMPTS[key].append(datetime.now(timezone.utc))


def clear_login_failures():
    key = rate_limit_key()
    LOGIN_ATTEMPTS.pop(key, None)
    try:
        LoginAttempt.query.filter_by(key=key).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()


def password_fingerprint(user):
    """Stable, non-secret fingerprint of a user's current password hash.

    Stored in the session so a password change can invalidate old sessions
    without keeping the hash itself in the cookie.
    """
    import hashlib

    password_hash = getattr(user, "password", "") or ""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:32]


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


def commit_or_flash_failure(message=None):
    """Commit the current transaction, or roll back and flash a clear error.

    Returns True when the write succeeded and False when it failed. A failed
    write must not surface as an unhandled 500: the visitor gets an actionable
    message and the transaction is rolled back so no partial record is left
    behind.
    """
    try:
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Could not save public form submission")
        flash(
            message
            or (
                "We could not save your submission because of a temporary problem. "
                "Please try again or call the office at 301-455-3030."
            ),
            "danger",
        )
        return False


def public_form_is_limited(scope):
    """Throttle repeated public form submissions from one client.

    Uses the shared LoginAttempt store under a ``form:<scope>:`` key so the
    limit survives restarts and is shared across Gunicorn workers. Call this
    *after* a successful validation so typos do not consume the allowance.
    """
    key = f"form:{scope}:{rate_limit_key()}"
    now = datetime.now(timezone.utc)
    cutoff = now - PUBLIC_FORM_WINDOW
    try:
        LoginAttempt.query.filter(LoginAttempt.attempted_at < cutoff).delete(synchronize_session=False)
        db.session.commit()
        recent = LoginAttempt.query.filter(
            LoginAttempt.key == key,
            LoginAttempt.attempted_at >= cutoff,
        ).count()
        if recent >= PUBLIC_FORM_LIMIT:
            return True
        db.session.add(LoginAttempt(key=key))
        db.session.commit()
        return False
    except Exception:
        db.session.rollback()
        LOGIN_ATTEMPTS[key] = [item for item in LOGIN_ATTEMPTS[key] if now - item < PUBLIC_FORM_WINDOW]
        if len(LOGIN_ATTEMPTS[key]) >= PUBLIC_FORM_LIMIT:
            return True
        LOGIN_ATTEMPTS[key].append(now)
        return False


def looks_like_spam(*values):
    """Cheap heuristic for obvious bot submissions (links, injected scripts).

    Returns True when the combined text contains URLs, BBCode/HTML link markup,
    or script tags. Human messages that merely mention a website are uncommon on
    these intake forms, so a false positive is preferable to unbounded spam.
    """
    text = " ".join(str(value or "") for value in values)
    lowered = text.lower()
    if "<script" in lowered or "javascript:" in lowered or "[url=" in lowered:
        return True
    return bool(re.search(r"https?://|www\.[a-z0-9-]+\.[a-z]{2,}", lowered))


def honeypot_triggered():
    """Return True when the hidden field bots fill in is present and non-empty."""
    return bool(request.form.get("website", "").strip())


def reject_public_submission(scope, redirect_endpoint, *fields):
    """Shared gate for public forms.

    Returns a redirect response when the submission is clearly abusive, or
    ``None`` when the caller should proceed. Records an audit entry on block.
    """
    from flask import flash, redirect, url_for

    if honeypot_triggered() or looks_like_spam(*fields) or public_form_is_limited(scope):
        audit("public_form_blocked", "PublicForm", None, f"scope={scope}")
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash("We could not process that submission. Please try again or call the office.", "danger")
        return redirect(url_for(redirect_endpoint))
    return None


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


