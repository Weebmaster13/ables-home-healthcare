"""Set the administrator sign-in details on the live server.

Run with the app's own environment (so the database URL is available):

    cd /home/soev6l6iqyxn/ables-app
    /home/soev6l6iqyxn/virtualenv/ables-app/3.11/bin/python bootstrap_admin.py

It can either point the existing admin account at a new address (for
example a Gmail address) or create a fresh admin account, and it always
sets a known password so the owner can sign in immediately.
"""

import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from werkzeug.security import generate_password_hash  # noqa: E402

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from models import User  # noqa: E402

NEW_EMAIL = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
NEW_PASSWORD = (os.environ.get("ADMIN_SEED_PASSWORD") or "").strip()
ADMIN_NAME = (os.environ.get("ADMIN_SEED_NAME") or "Production Admin").strip()


def main():
    if not NEW_EMAIL:
        raise SystemExit("Set ADMIN_EMAIL to the address that should be used to sign in.")

    password = NEW_PASSWORD or secrets.token_urlsafe(12)

    with app.app_context():
        existing = User.query.filter_by(email=NEW_EMAIL).first()
        if existing:
            existing.role = "admin"
            existing.is_active_account = True
            existing.password = generate_password_hash(password)
            action = "updated"
        else:
            db.session.add(User(
                name=ADMIN_NAME,
                email=NEW_EMAIL,
                password=generate_password_hash(password),
                role="admin",
            ))
            action = "created"
        db.session.commit()

    print("Admin %s: %s" % (action, NEW_EMAIL))
    print("Temporary password: %s" % password)
    print("Sign in at https://ableshomehealthcare.com/auth/login")
    print("Change it afterwards at https://ableshomehealthcare.com/change-password")


if __name__ == "__main__":
    main()
