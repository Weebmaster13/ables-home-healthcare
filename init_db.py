from app import app
from extensions import db
from models import User
from werkzeug.security import generate_password_hash


with app.app_context():
    db.create_all()

    admin_email = "admin@test.com"
    admin = User.query.filter_by(email=admin_email).first()

    if not admin:
        admin = User(
            name="Justus Nzomo Mulwa",
            email=admin_email,
            phone="3013262225",
            role="admin",
            password=generate_password_hash("HarborHealth!2026"),
        )

        db.session.add(admin)
        db.session.commit()
        print("Database initialized and admin user created.")
    else:
        print("Database initialized. Admin user already exists.")


