"""Destroy and recreate the local development database.

Refuses to run in a production configuration and requires an explicit
confirmation flag, so it cannot silently wipe real data. Credentials come
from env vars instead of being hard-coded.
"""
import os
import secrets

from app import app
from extensions import db
from models import User
from werkzeug.security import generate_password_hash


def main():
    if app.config.get("PRODUCTION_ENVIRONMENT"):
        raise SystemExit(
            "Refusing to run: this environment looks like production. "
            "reset_all.py only targets local development databases."
        )

    if os.environ.get("RESET_ALL_CONFIRM") != "yes":
        raise SystemExit(
            "This deletes ALL local data. Re-run with RESET_ALL_CONFIRM=yes to proceed."
        )

    with app.app_context():
        db.drop_all()
        db.create_all()

        admin_email = os.environ.get("DEV_ADMIN_EMAIL", "admin@test.com")
        password = os.environ.get("DEV_ADMIN_PASSWORD")
        if not password:
            password = secrets.token_urlsafe(12)
            print("No DEV_ADMIN_PASSWORD set; generated a one-time development password.")
            print(f"Development admin password (change on first login): {password}")

        db.session.add(User(
            name="Admin",
            email=admin_email,
            role="admin",
            password=generate_password_hash(password),
        ))
        db.session.commit()
        print("RESET COMPLETE: admin created")


if __name__ == "__main__":
    main()
