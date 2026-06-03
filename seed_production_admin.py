import os
import secrets

from werkzeug.security import generate_password_hash

from app import app
from extensions import db
from models import User


def main():
    email = os.environ.get("ADMIN_SEED_EMAIL", "").strip().lower()
    name = os.environ.get("ADMIN_SEED_NAME", "Production Admin").strip()
    password = os.environ.get("ADMIN_SEED_PASSWORD", "").strip() or secrets.token_urlsafe(18)

    if not email:
        raise SystemExit("Set ADMIN_SEED_EMAIL before running this script.")

    with app.app_context():
        existing = User.query.filter_by(email=email).first()
        if existing:
            existing.role = "admin"
            existing.is_active_account = True
            print(f"Updated existing admin: {email}")
        else:
            db.session.add(User(name=name, email=email, password=generate_password_hash(password), role="admin"))
            print(f"Created admin: {email}")
            print(f"Temporary password: {password}")
        db.session.commit()


if __name__ == "__main__":
    main()


