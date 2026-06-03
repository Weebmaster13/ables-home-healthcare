from pathlib import Path
import sqlite3


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "ablehome.db"


def column_names(cursor, table_name):
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})")}


def add_column(cursor, table_name, column_name, definition):
    if column_name not in column_names(cursor, table_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        print(f"Added {table_name}.{column_name}")


def main():
    if not DB_PATH.exists():
        print("Database does not exist yet. Run python init_db.py instead.")
        return

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    add_column(cursor, "user", "phone", "VARCHAR(50)")
    add_column(cursor, "user", "address", "VARCHAR(200)")
    add_column(cursor, "user", "emergency_contact_name", "VARCHAR(120)")
    add_column(cursor, "user", "emergency_contact_phone", "VARCHAR(50)")
    add_column(cursor, "user", "care_needs", "TEXT")
    add_column(cursor, "user", "preferred_schedule", "VARCHAR(200)")
    add_column(cursor, "user", "is_active_account", "BOOLEAN DEFAULT 1")
    add_column(cursor, "user", "failed_login_count", "INTEGER DEFAULT 0")
    add_column(cursor, "user", "locked_until", "DATETIME")
    add_column(cursor, "user", "last_login_at", "DATETIME")
    add_column(cursor, "user", "password_changed_at", "DATETIME")
    add_column(cursor, "user", "created_at", "DATETIME")
    add_column(cursor, "appointment", "caregiver_notes", "TEXT")
    add_column(cursor, "appointment", "service_address", "VARCHAR(220)")
    add_column(cursor, "appointment", "schedule_preference", "VARCHAR(220)")
    add_column(cursor, "appointment", "mobility_needs", "TEXT")
    add_column(cursor, "appointment", "emergency_contact", "VARCHAR(220)")
    add_column(cursor, "appointment", "preferred_language", "VARCHAR(80)")
    add_column(cursor, "appointment", "consent_confirmed", "BOOLEAN DEFAULT 0")
    add_column(cursor, "appointment", "created_at", "DATETIME")
    add_column(cursor, "appointment", "updated_at", "DATETIME")
    add_column(cursor, "appointment", "recurring_group_id", "VARCHAR(80)")
    add_column(cursor, "appointment", "recurring_interval_weeks", "INTEGER")
    add_column(cursor, "appointment", "recurring_paused", "BOOLEAN DEFAULT 0")
    add_column(cursor, "appointment", "reminder_sent_for", "VARCHAR(40)")
    add_column(cursor, "appointment", "urgent_flag", "BOOLEAN DEFAULT 0")
    add_column(cursor, "appointment", "urgent_reason", "VARCHAR(220)")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS inquiry (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            email VARCHAR(120) NOT NULL,
            phone VARCHAR(50),
            reason VARCHAR(100),
            message TEXT NOT NULL,
            status VARCHAR(30),
            created_at DATETIME
        )
        """
    )

    for column, definition in {
        "care_recipient": "VARCHAR(120)",
        "service_address": "VARCHAR(220)",
        "city": "VARCHAR(120)",
        "start_date": "VARCHAR(50)",
        "start_timeline": "VARCHAR(120)",
        "services_needed": "VARCHAR(220)",
        "schedule_preference": "VARCHAR(220)",
        "mobility_needs": "TEXT",
        "diagnosis_notes": "TEXT",
        "emergency_contact": "VARCHAR(220)",
        "preferred_language": "VARCHAR(80)",
        "consent_confirmed": "BOOLEAN DEFAULT 0",
    }.items():
        add_column(cursor, "inquiry", column, definition)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS referral (
            id INTEGER NOT NULL PRIMARY KEY,
            referral_source VARCHAR(120) NOT NULL,
            source_phone VARCHAR(50) NOT NULL,
            client_name VARCHAR(120) NOT NULL,
            client_phone VARCHAR(50),
            care_needed VARCHAR(120),
            follow_up VARCHAR(120),
            notes TEXT,
            status VARCHAR(30),
            created_at DATETIME
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER NOT NULL PRIMARY KEY,
            user_id INTEGER,
            action VARCHAR(120) NOT NULL,
            entity_type VARCHAR(80),
            entity_id INTEGER,
            details TEXT,
            created_at DATETIME,
            FOREIGN KEY(user_id) REFERENCES user (id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_recovery_code (
            id INTEGER NOT NULL PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            code_hash VARCHAR(255) NOT NULL,
            used_at DATETIME,
            created_at DATETIME,
            FOREIGN KEY(admin_id) REFERENCES user (id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER NOT NULL PRIMARY KEY,
            channel VARCHAR(20) NOT NULL,
            recipient VARCHAR(160),
            subject VARCHAR(220),
            status VARCHAR(30) NOT NULL,
            provider VARCHAR(80),
            related_type VARCHAR(80),
            related_id INTEGER,
            error TEXT,
            created_at DATETIME
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS care_plan (
            id INTEGER NOT NULL PRIMARY KEY,
            inquiry_id INTEGER,
            referral_id INTEGER,
            client_name VARCHAR(120) NOT NULL,
            client_phone VARCHAR(50),
            service_address VARCHAR(220),
            service_type VARCHAR(160),
            schedule VARCHAR(220),
            tasks TEXT,
            risks TEXT,
            emergency_contacts TEXT,
            caregiver_preferences TEXT,
            notes TEXT,
            status VARCHAR(40),
            created_at DATETIME,
            updated_at DATETIME,
            FOREIGN KEY(inquiry_id) REFERENCES inquiry (id),
            FOREIGN KEY(referral_id) REFERENCES referral (id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS caregiver_application (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            email VARCHAR(120) NOT NULL,
            phone VARCHAR(50) NOT NULL,
            address VARCHAR(220),
            availability VARCHAR(220),
            weekday_availability VARCHAR(220),
            weekend_availability VARCHAR(220),
            certifications VARCHAR(220),
            years_experience VARCHAR(80),
            transportation VARCHAR(120),
            preferred_service_areas VARCHAR(220),
            applicant_references TEXT,
            background_check_consent BOOLEAN DEFAULT 0,
            resume_filename VARCHAR(220),
            experience TEXT,
            status VARCHAR(40),
            created_at DATETIME,
            updated_at DATETIME
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS portal_message (
            id INTEGER NOT NULL PRIMARY KEY,
            sender_id INTEGER NOT NULL,
            audience VARCHAR(40),
            topic VARCHAR(80) NOT NULL,
            subject VARCHAR(160) NOT NULL,
            body TEXT NOT NULL,
            priority VARCHAR(30),
            status VARCHAR(30),
            admin_reply TEXT,
            replied_by_id INTEGER,
            created_at DATETIME,
            updated_at DATETIME,
            FOREIGN KEY(sender_id) REFERENCES user (id),
            FOREIGN KEY(replied_by_id) REFERENCES user (id)
        )
        """
    )
    add_column(cursor, "portal_message", "admin_seen", "BOOLEAN DEFAULT 0")
    add_column(cursor, "portal_message", "sender_seen_reply", "BOOLEAN DEFAULT 1")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS client_document (
            id INTEGER NOT NULL PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            uploaded_by_id INTEGER NOT NULL,
            title VARCHAR(160) NOT NULL,
            category VARCHAR(80),
            filename VARCHAR(220) NOT NULL,
            original_filename VARCHAR(220) NOT NULL,
            notes TEXT,
            created_at DATETIME,
            FOREIGN KEY(owner_id) REFERENCES user (id),
            FOREIGN KEY(uploaded_by_id) REFERENCES user (id)
        )
        """
    )
    add_column(cursor, "client_document", "archived_at", "DATETIME")
    add_column(cursor, "client_document", "archived_by_id", "INTEGER")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_note (
            id INTEGER NOT NULL PRIMARY KEY,
            subject_user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            note TEXT NOT NULL,
            created_at DATETIME,
            FOREIGN KEY(subject_user_id) REFERENCES user (id),
            FOREIGN KEY(admin_id) REFERENCES user (id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS caregiver_availability (
            id INTEGER NOT NULL PRIMARY KEY,
            caregiver_id INTEGER NOT NULL,
            weekday VARCHAR(20) NOT NULL,
            start_time VARCHAR(20) NOT NULL,
            end_time VARCHAR(20) NOT NULL,
            service_area VARCHAR(220),
            notes TEXT,
            created_at DATETIME,
            FOREIGN KEY(caregiver_id) REFERENCES user (id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS caregiver_onboarding_item (
            id INTEGER NOT NULL PRIMARY KEY,
            caregiver_id INTEGER NOT NULL,
            item_type VARCHAR(80) NOT NULL,
            status VARCHAR(40),
            notes TEXT,
            completed_at DATETIME,
            created_at DATETIME,
            updated_at DATETIME,
            FOREIGN KEY(caregiver_id) REFERENCES user (id)
        )
        """
    )

    connection.commit()
    connection.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()


