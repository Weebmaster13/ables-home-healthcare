"""Generate fresh admin recovery codes for staging and print the first.

Staging runs with ADMIN_RECOVERY_CODES_REQUIRED=true (production semantics), so
an admin login consumes one unused recovery code. Generating a new set
invalidates the previous unused codes, so this helper tops the codes up and
prints one plaintext code for the verification harness to use immediately.

    ENV_FILE=/tmp/ables-staging.env python tools/staging_recovery.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from extensions import db
from models import User
import security


def main():
    email = os.environ.get("ADMIN_SEED_EMAIL", "admin@staging.invalid").strip().lower()
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            raise SystemExit(f"No account found for {email}")
        codes = security.generate_recovery_codes(user)
        db.session.commit()
        print(codes[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
