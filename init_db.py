"""Create local development tables and an optional development admin.

Development only. Credentials come from env vars, never hard-coded, so this
file cannot leak a usable password if it ends up somewhere unexpected.
"""
import os
import secrets

from app import app
from extensions import db
from models import User
from werkzeug.security import generate_password_hash


def main():
    with app.app_context():
        db.create_all()

        admin_email = os.environ.get("DEV_ADMIN_EMAIL", "admin@test.com")
        admin_name = os.environ.get("DEV_ADMIN_NAME", "Development Admin")
        admin = User.query.filter_by(email=admin_email).first()

        if admin:
            print(f"Database initialized. Admin user already exists: {admin_email}")
            return

        password = os.environ.get("DEV_ADMIN_PASSWORD")
        if not password:
            password = secrets.token_urlsafe(12)
            print("No DEV_ADMIN_PASSWORD set; generated a one-time development password.")
            print(f"Development admin password (change on first login): {password}")

        db.session.add(User(
            name=admin_name,
            email=admin_email,
            phone="",
            role="admin",
            password=generate_password_hash(password),
        ))
        db.session.commit()
        print(f"Database initialized and admin user created: {admin_email}")


if __name__ == "__main__":
    main()
