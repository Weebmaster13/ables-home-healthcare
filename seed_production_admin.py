import os

from werkzeug.security import generate_password_hash

from app import app
from extensions import db
from models import AuditLog, User


def main():
    email = os.environ.get("ADMIN_SEED_EMAIL", "").strip().lower()
    name = os.environ.get("ADMIN_SEED_NAME", "Production Admin").strip()
    password = os.environ.get("ADMIN_SEED_PASSWORD", "").strip()
    allow_promote = os.environ.get("ADMIN_SEED_ALLOW_PROMOTE", "").lower() == "true"

    if not email:
        raise SystemExit("Set ADMIN_SEED_EMAIL before running this script.")

    if not password:
        # Never print a generated password to stdout; deployment logs persist.
        print(
            "Set ADMIN_SEED_PASSWORD to the password for the new admin account.\n"
            "Generate one locally with: python -c \"import secrets; print(secrets.token_urlsafe(18))\"\n"
            "The password is not printed here so it is not captured in deployment logs."
        )
        raise SystemExit(1)

    with app.app_context():
        existing = User.query.filter_by(email=email).first()
        if existing:
            if existing.role == "admin" and existing.is_active_account:
                print(f"Admin account already present: {email}")
                return
            if not allow_promote:
                print(
                    f"An account already exists for {email} with role '{existing.role}'.\n"
                    "Refusing to change its role automatically. To intentionally promote this\n"
                    "account to admin, re-run with ADMIN_SEED_ALLOW_PROMOTE=true. Verify the email\n"
                    "address belongs to the intended person before doing so."
                )
                raise SystemExit(1)
            existing.role = "admin"
            existing.is_active_account = True
            db.session.add(
                AuditLog(
                    user_id=None,
                    action="seed_promote_admin",
                    entity_type="User",
                    entity_id=existing.id,
                    details=f"seed_production_admin promoted {email} to admin (existing password preserved)",
                )
            )
            print(f"Promoted existing account to admin: {email} (existing password preserved)")
        else:
            db.session.add(
                User(
                    name=name,
                    email=email,
                    password=generate_password_hash(password),
                    role="admin",
                    is_active_account=True,
                )
            )
            print(f"Created admin: {email}")
        db.session.commit()


if __name__ == "__main__":
    main()


