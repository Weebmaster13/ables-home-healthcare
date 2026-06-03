import argparse

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from app import app
from extensions import db
from sqlalchemy import text
from production_check import CHECKS


def migration_status():
    try:
        current = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:
        return False, f"Could not read Alembic migration version: {exc}"

    alembic_config = AlembicConfig("migrations/alembic.ini")
    alembic_config.set_main_option("script_location", "migrations")
    heads = ScriptDirectory.from_config(alembic_config).get_heads()
    if len(heads) != 1:
        return False, f"Expected one migration head, found: {', '.join(heads) or 'none'}"

    if current == heads[0]:
        return True, f"Migration current: {current}"

    return False, f"Migration mismatch. Database: {current or 'none'}, migration head: {heads[0]}"


def main():
    parser = argparse.ArgumentParser(description="Check local app health or strict production readiness.")
    parser.add_argument(
        "--production",
        action="store_true",
        help="Require all production launch settings such as PostgreSQL, SMTP, HTTPS, and legal confirmations.",
    )
    args = parser.parse_args()

    failures = []
    with app.app_context():
        if args.production:
            for name, predicate, fix in CHECKS:
                ready = predicate(app.config)
                print(f"[{'OK' if ready else 'TODO'}] {name}")
                if not ready:
                    failures.append(fix)
        else:
            print("[OK] Local development mode")
            print("[INFO] Run `python deploy_check.py --production` before launching with real clients.")

        health_ok = True
        try:
            from routes import health_status_data
            status = health_status_data()
            health_ok = status["database_ok"] and status["upload_folder_exists"]
            print(f"[{'OK' if health_ok else 'TODO'}] Health dependencies")
            if not status["upload_folder_exists"]:
                failures.append("Create or mount the production upload folder.")
        except Exception as exc:
            health_ok = False
            failures.append(f"Fix health check failure: {exc}")

        migrations_ok, migration_message = migration_status()
        print(f"[{'OK' if migrations_ok else 'TODO'}] {migration_message}")
        if not migrations_ok:
            failures.append("Run `flask --app app db upgrade` before launch.")

    if failures or not health_ok:
        print("\nProduction deploy is not ready:")
        for item in failures:
            print(f"- {item}")
        raise SystemExit(1)

    print("\nDeploy checks passed." if args.production else "\nLocal checks passed.")


if __name__ == "__main__":
    main()


