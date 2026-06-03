import os
import tempfile

TEST_DB = os.path.join(tempfile.mkdtemp(prefix="ables-tests-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["ENV_FILE"] = ".env.example"

from app import app
from extensions import db


def pytest_configure():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()


def pytest_runtest_setup(item):
    with app.app_context():
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()

