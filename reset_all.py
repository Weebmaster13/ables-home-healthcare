from app import app
from extensions import db
from models import User
from werkzeug.security import generate_password_hash

with app.app_context():

    # 🔥 wipe EVERYTHING
    db.drop_all()
    db.create_all()

    # 🔥 create fresh admin
    admin = User(
        name="Admin",
        email="admin@test.com",
        role="admin",
        password=generate_password_hash("admin123")
    )

    db.session.add(admin)
    db.session.commit()

    print("✅ RESET COMPLETE: admin created")

