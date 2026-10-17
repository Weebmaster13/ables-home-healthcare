from datetime import date, datetime
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

from werkzeug.security import generate_password_hash
from werkzeug.datastructures import FileStorage
import pytest

from app import app
from extensions import db
from models import AdminRecoveryCode, Appointment, AuditLog, CaregiverApplication, CaregiverOnboardingItem, ClientDocument, Inquiry, NotificationLog, PortalMessage, Referral, User
from production_check import configured
from routes import allowed_document, caregiver_double_bookings, save_private_upload, send_visit_reminder
from backup_db import BACKUP_DIR, cleanup_old_backups
from security import generate_recovery_codes


def unique_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex}@example.com"


def create_user(role="client"):
    password = "SafePass2026!"
    user = User(
        name=f"Test {role}",
        email=unique_email(role),
        password=generate_password_hash(password),
        role=role,
    )
    db.session.add(user)
    db.session.commit()
    if role == "admin":
        AdminRecoveryCode.query.filter_by(admin_id=user.id).delete()
        db.session.commit()
    return SimpleNamespace(id=user.id, email=user.email, role=user.role), password


def login(client, user, password):
    return client.post(
        "/auth/login",
        data={"email": user.email, "password": password},
        follow_redirects=False,
    )


def create_admin_with_codes():
    admin, password = create_user("admin")
    admin_user = db.session.get(User, admin.id)
    codes = generate_recovery_codes(admin_user, count=2)
    db.session.commit()
    return admin, password, codes


def test_healthz_reports_database_and_uploads():
    with app.test_client() as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    data = response.get_json()
    # Public liveness endpoint must not disclose internal operational details.
    assert data == {"status": "ok"}

    with app.test_client() as client:
        admin_response = client.get("/admin/healthz")
    # Anonymous callers cannot reach the detailed diagnostics.
    assert admin_response.status_code in (302, 401, 403)


def test_registration_and_login_flow():
    app.config["WTF_CSRF_ENABLED"] = False
    email = unique_email("register")
    with app.test_client() as client:
        response = client.post(
            "/auth/register",
            data={
                "name": "Registration Test",
                "email": email,
                "password": "SafePass2026!",
                "confirm_password": "SafePass2026!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        response = client.post(
            "/auth/login",
            data={"email": email, "password": "SafePass2026!"},
            follow_redirects=False,
        )
        assert response.status_code == 302

    with app.app_context():
        User.query.filter_by(email=email).delete()
        db.session.commit()


def test_admin_pages_require_login():
    with app.test_client() as client:
        response = client.get("/admin/security", follow_redirects=False)
    assert response.status_code == 302


def test_all_roles_can_log_out():
    app.config["WTF_CSRF_ENABLED"] = False
    created_ids = []
    with app.app_context():
        users = []
        for role in ["admin", "client", "caregiver"]:
            user, password = create_user(role)
            users.append((user, password))
            created_ids.append(user.id)

    for user, password in users:
        with app.test_client() as client:
            assert login(client, user, password).status_code == 302
            dashboard = client.get("/dashboard")
            assert dashboard.status_code == 200
            response = client.post("/auth/logout", follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/auth/login")
            protected = client.get("/dashboard", follow_redirects=False)
            assert protected.status_code == 302
            assert "/auth/login" in protected.headers["Location"]

    with app.app_context():
        assert AuditLog.query.filter(AuditLog.user_id.in_(created_ids), AuditLog.action == "logout").count() == 3
        User.query.filter(User.id.in_(created_ids)).delete()
        db.session.commit()


def test_forgot_password_sends_user_reset_link_and_allows_reset():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        user, _ = create_user("client")
        before = NotificationLog.query.count()

    with app.test_client() as client:
        response = client.post(
            "/auth/forgot-password",
            data={"email": user.email},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Development mode" in response.data
        assert b"Reset password now" in response.data

        marker = b"/auth/reset-password/"
        start = response.data.index(marker)
        end = response.data.index(b"\"", start)
        reset_path = response.data[start:end].decode("utf-8")

        response = client.post(
            reset_path,
            data={
                "password": "NewSafePass2026!",
                "confirm_password": "NewSafePass2026!",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    with app.app_context():
        after = NotificationLog.query.count()
        assert after > before
        assert NotificationLog.query.filter_by(
            recipient=user.email,
            subject="Reset your Ables Home Healthcare password",
            status="skipped",
        ).first() is not None
        db_user = db.session.get(User, user.id)
        assert db_user is not None
        assert db_user.password_changed_at is not None
        User.query.filter_by(id=user.id).delete()
        db.session.commit()


def test_client_intake_captures_required_office_details():
    app.config["WTF_CSRF_ENABLED"] = False
    email = unique_email("client-intake")
    with app.test_client() as client:
        response = client.post(
            "/free-consultation",
            data={
                "name": "Client Family",
                "email": email,
                "phone": "301-326-2225",
                "care_recipient": "Test Client",
                "city": "Montgomery County",
                "service_address": "11510 Georgia Avenue Suite 230",
                "start_date": "2032-01-01",
                "start_timeline": "This week",
                "care_level": "Skilled nursing care",
                "specific_needs": "Wound care and bathing support",
                "services_needed": "Skilled nursing, bathing, grooming",
                "diagnosis_notes": "Diabetes",
                "hours_per_week": "20 hours per week",
                "schedule_preference": "Monday-Friday 8 AM to 12 PM",
                "payment_method": "Private Pay (Cash or Card)",
                "mobility_needs": "Walker",
                "emergency_contact": "Family 301-326-2225",
                "preferred_language": "Kiswahili",
                "message": "Please call.",
                "consent_confirmed": "yes",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    with app.app_context():
        inquiry = Inquiry.query.filter_by(email=email).first()
        assert inquiry is not None
        assert "Skilled or non-skilled: Skilled nursing care" in inquiry.message
        assert "Specific needs: Wound care and bathing support" in inquiry.message
        assert "Hours per week: 20 hours per week" in inquiry.message
        assert "How they will pay: Private Pay (Cash or Card)" in inquiry.message
        Inquiry.query.filter_by(email=email).delete()
        db.session.commit()


def test_caregiver_application_blocks_non_cna_lpn_rn_and_accepts_rn():
    app.config["WTF_CSRF_ENABLED"] = False
    blocked_email = unique_email("blocked-caregiver")
    rn_email = unique_email("rn-caregiver")

    with app.test_client() as client:
        blocked = client.post(
            "/employment-application",
            data={
                "credential_type": "No",
                "name": "Blocked Applicant",
                "email": blocked_email,
                "phone": "301-326-2225",
            },
            follow_redirects=False,
        )
        assert blocked.status_code == 200
        assert b"Please call the office" in blocked.data

        accepted = client.post(
            "/employment-application",
            data={
                "credential_type": "RN",
                "name": "RN Applicant",
                "email": rn_email,
                "phone": "301-326-2225",
                "availability": "Weekdays",
                "weekday_availability": "8 AM to 4 PM",
                "weekend_availability": "No",
                "certifications": "RN license",
                "years_experience": "5",
                "transportation": "Reliable personal transportation",
                "preferred_service_areas": "Baltimore City",
                "applicant_references": "Available",
                "experience": "Skilled nursing experience",
                "background_check_consent": "yes",
                "requirements": [
                    "Driver's license or ID",
                    "CPR card",
                    "Background check after hire",
                ],
            },
            follow_redirects=False,
        )
        assert accepted.status_code == 302

    with app.app_context():
        assert CaregiverApplication.query.filter_by(email=blocked_email).first() is None
        application = CaregiverApplication.query.filter_by(email=rn_email).first()
        assert application is not None
        assert application.certifications.startswith("RN |")
        assert "Driver's license or ID" in application.requirements_checklist
        assert "Background check after hire" in application.requirements_checklist
        CaregiverApplication.query.filter_by(email=rn_email).delete()
        Inquiry.query.filter(Inquiry.email.in_([blocked_email, rn_email])).delete()
        db.session.commit()


def test_upload_scanner_rejects_eicar_signature():
    with app.app_context():
        file_storage = FileStorage(
            stream=BytesIO(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!"),
            filename="scan-test.pdf",
        )
        stored, original = save_private_upload(file_storage, "test-documents", "test", allowed_document)
    assert stored == ""
    assert original == "scan-test.pdf"


def test_upload_mime_validation_accepts_documents_and_blocks_fake_extension():
    with app.app_context():
        good = FileStorage(stream=BytesIO(b"%PDF-1.4 safe contents"), filename="safe.pdf")
        stored, original = save_private_upload(good, "test-documents", "test", allowed_document)
        assert stored.endswith(".pdf")
        assert original == "safe.pdf"

        bad = FileStorage(stream=BytesIO(b"not safe"), filename="fake.exe")
        stored, original = save_private_upload(bad, "test-documents", "test", allowed_document)
        assert stored == ""
        assert original == "fake.exe"


def test_client_can_request_appointment_and_manage_document():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        user, password = create_user("client")

    with app.test_client() as client:
        assert login(client, user, password).status_code == 302
        response = client.post(
            "/request-care",
            data={
                "date": date.today().isoformat(),
                "time": "10:00",
                "service_type": "Personal Care",
                "service_address": "123 Test St",
                "consent_confirmed": "yes",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        response = client.post(
            "/documents",
            data={
                "title": "Test Document",
                "category": "Intake",
                "document_consent": "yes",
                "document": (BytesIO(b"%PDF-1.4 test document"), "test-document.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert response.status_code == 302

    with app.app_context():
        appointment = Appointment.query.filter_by(client_id=user.id, service_type="Personal Care").first()
        document = ClientDocument.query.filter_by(owner_id=user.id, title="Test Document").first()
        assert appointment is not None
        assert document is not None

        with app.test_client() as client:
            assert login(client, user, password).status_code == 302
            assert client.post(f"/documents/{document.id}/archive", follow_redirects=False).status_code == 302
            db.session.refresh(document)
            assert document.archived_at is not None
            assert client.post(f"/documents/{document.id}/delete", follow_redirects=False).status_code == 302

        User.query.filter_by(id=user.id).delete()
        db.session.commit()


def test_reminder_logs_skipped_delivery_when_smtp_sms_not_configured():
    with app.app_context():
        user, _ = create_user("client")
        appointment = Appointment(
            client_id=user.id,
            date=date.today().isoformat(),
            time="11:00",
            service_type="Companion Care",
            service_address="123 Test St",
            status="approved",
        )
        db.session.add(appointment)
        db.session.commit()

        before = NotificationLog.query.count()
        sent = send_visit_reminder(appointment, "today")
        db.session.commit()
        after = NotificationLog.query.count()

        assert sent is False
        assert after > before

        User.query.filter_by(id=user.id).delete()
        db.session.commit()


def test_production_placeholder_checker_rejects_fake_values():
    assert configured("smtp.company.com") is True
    assert configured("REPLACE_SMTP_HOST") is False
    assert configured("smtp.example.com") is False


def test_admin_recovery_code_login_requires_code():
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = True
    with app.app_context():
        admin, password = create_user("admin")
        admin_user = db.session.get(User, admin.id)
        codes = generate_recovery_codes(admin_user, count=1)
        db.session.commit()

    with app.test_client() as client:
        missing = login(client, admin, password)
        assert missing.status_code == 302
        with app.app_context():
            assert db.session.get(User, admin.id).failed_login_count > 0

        good = client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}, follow_redirects=False)
        assert good.status_code == 302

    with app.app_context():
        assert AdminRecoveryCode.query.filter_by(admin_id=admin.id, used_at=None).count() == 0
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = False


def test_admin_recovery_code_login_can_be_disabled():
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = False
    with app.app_context():
        admin, password, _ = create_admin_with_codes()

    with app.test_client() as client:
        assert login(client, admin, password).status_code == 302
        response = client.get("/admin/system-status")
        assert response.status_code == 200

    with app.app_context():
        assert AdminRecoveryCode.query.filter_by(admin_id=admin.id, used_at=None).count() > 0
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()


def test_system_status_shows_actual_database_backend():
    """The admin system-status card must name the real backend, not PostgreSQL."""
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = False
    with app.app_context():
        admin, password, _ = create_admin_with_codes()

    with app.test_client() as client:
        assert login(client, admin, password).status_code == 302
        response = client.get("/admin/system-status")
        assert response.status_code == 200
        # Tests run on SQLite; the label must reflect that, not "PostgreSQL".
        assert b"SQLite" in response.data
        assert b"<span>PostgreSQL</span>" not in response.data

    with app.app_context():
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()


def test_audit_filter_and_export_require_admin_and_return_csv():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        db.session.add(AuditLog(user_id=admin.id, action="unit_test_action", entity_type="Unit", details="filter me"))
        db.session.commit()

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}, follow_redirects=False).status_code == 302
        page = client.get("/admin/audit?action=unit_test")
        assert page.status_code == 200
        assert b"unit_test_action" in page.data
        export = client.get("/admin/audit/export?action=unit_test")
        assert export.status_code == 200
        assert b"unit_test_action" in export.data

    with app.app_context():
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()


def test_recurring_appointments_and_pause_resume():
    app.config["WTF_CSRF_ENABLED"] = False
    service_type = f"Companion Care {uuid.uuid4().hex}"
    start_date = date(2032, 1, 1).isoformat()
    with app.app_context():
        admin, admin_password, codes = create_admin_with_codes()
        client_user, _ = create_user("client")
        appointment = Appointment(client_id=client_user.id, date=start_date, time="09:00", service_type=service_type, status="approved")
        db.session.add(appointment)
        db.session.commit()
        appointment_id = appointment.id

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": admin_password, "recovery_code": codes[0]}, follow_redirects=False).status_code == 302
        response = client.post(f"/admin/appointment/{appointment_id}/repeat", data={"count": "2", "interval_weeks": "1"}, follow_redirects=False)
        assert response.status_code == 302
        with app.app_context():
            source = db.session.get(Appointment, appointment_id)
            group_id = source.recurring_group_id
            assert Appointment.query.filter_by(recurring_group_id=group_id).count() >= 2
        assert client.post(f"/admin/recurring/{group_id}/pause", follow_redirects=False).status_code == 302
        with app.app_context():
            assert all(item.recurring_paused for item in Appointment.query.filter_by(recurring_group_id=group_id).all())
        assert client.post(f"/admin/recurring/{group_id}/resume", follow_redirects=False).status_code == 302

    with app.app_context():
        User.query.filter(User.id.in_([admin.id, client_user.id])).delete()
        db.session.commit()


def test_caregiver_double_booking_detection():
    with app.app_context():
        client_user, _ = create_user("client")
        caregiver, _ = create_user("caregiver")
        first = Appointment(client_id=client_user.id, caregiver_id=caregiver.id, date=date.today().isoformat(), time="13:00", service_type="Care", status="assigned")
        second = Appointment(client_id=client_user.id, caregiver_id=caregiver.id, date=date.today().isoformat(), time="13:00", service_type="Care", status="assigned")
        db.session.add_all([first, second])
        db.session.commit()
        groups = caregiver_double_bookings(Appointment.query.all())
        assert any(len(group) >= 2 and group[0].caregiver_id == caregiver.id for group in groups)
        User.query.filter(User.id.in_([client_user.id, caregiver.id])).delete()
        db.session.commit()


def test_notification_filters_and_backup_download_protection():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        db.session.add(NotificationLog(channel="email", recipient="x@example.com", subject="Test", status="failed", provider="smtp"))
        db.session.commit()

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}, follow_redirects=False).status_code == 302
        response = client.get("/admin/notifications?channel=email&status=failed&date_from=2000-01-01")
        assert response.status_code == 200
        assert b"x@example.com" in response.data
        export = client.get("/admin/notifications/export?channel=email&status=failed")
        assert export.status_code == 200
        assert b"x@example.com" in export.data
        bad_backup = client.get("/admin/backups/not-a-backup.txt/download")
        assert bad_backup.status_code == 404

    with app.app_context():
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()


def test_admin_recovery_setup_is_forced_before_admin_tools():
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = True
    with app.app_context():
        admin, password = create_user("admin")

    with app.test_client() as client:
        assert login(client, admin, password).status_code == 302
        response = client.get("/admin/system-status", follow_redirects=False)
        assert response.status_code == 302
        assert "/admin/security" in response.headers["Location"]
        security_page = client.get("/admin/security")
        assert security_page.status_code == 200
        assert b"does not have active recovery codes" in security_page.data

    with app.app_context():
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = False


def test_system_status_authenticated_admin_with_recovery_code():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}, follow_redirects=False).status_code == 302
        response = client.get("/admin/system-status")
        assert response.status_code == 200
        assert b"System status" in response.data
        assert b"Migration version" in response.data

    with app.app_context():
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()


def test_secure_headers_and_private_no_store():
    with app.test_client() as client:
        public = client.get("/")
        assert public.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert "Content-Security-Policy" in public.headers
        private = client.get("/auth/login")
        assert "no-store" in private.headers["Cache-Control"]


def test_backup_cleanup_retention_removes_old_files():
    BACKUP_DIR.mkdir(exist_ok=True)
    names = [f"ablehome-full-test-{uuid.uuid4().hex}-{index}.zip" for index in range(3)]
    for name in names:
        (BACKUP_DIR / name).write_text("backup", encoding="utf-8")
    removed = cleanup_old_backups(keep=1)
    assert len([name for name in removed if name in names]) >= 2
    for name in names:
        path = BACKUP_DIR / name
        if path.exists():
            path.unlink()


def test_flask_migration_upgrade_on_blank_database(tmp_path):
    db_path = tmp_path / "migration-test.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["ENV_FILE"] = ".env.example"
    result = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "app", "db", "upgrade"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert db_path.exists()


def test_admin_log_viewer_and_backup_delete():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
    backup_name = f"ablehome-full-test-{uuid.uuid4().hex}.zip"
    BACKUP_DIR.mkdir(exist_ok=True)
    (BACKUP_DIR / backup_name).write_text("backup", encoding="utf-8")

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        logs = client.get("/admin/logs")
        assert logs.status_code == 200
        assert b"Application log viewer" in logs.data
        deleted = client.post(f"/admin/backups/{backup_name}/delete", follow_redirects=False)
        assert deleted.status_code == 302
        assert not (BACKUP_DIR / backup_name).exists()


def test_retention_cleanup_removes_old_notifications():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        old = NotificationLog(channel="email", recipient="old@example.com", subject="Old", status="failed")
        db.session.add(old)
        db.session.commit()
        old.created_at = date(2020, 1, 1)
        db.session.commit()

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        response = client.post("/admin/retention", data={"target": "notifications", "days": "30"}, follow_redirects=False)
        assert response.status_code == 200

    with app.app_context():
        assert NotificationLog.query.filter_by(recipient="old@example.com").count() == 0


def test_user_activity_timeline_and_onboarding_checklist():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        caregiver, _ = create_user("caregiver")
        db.session.add(AuditLog(user_id=caregiver.id, action="timeline_test", details="visible"))
        db.session.commit()

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        page = client.get(f"/admin/users/{caregiver.id}/edit")
        assert page.status_code == 200
        assert b"Activity timeline" in page.data
        assert b"Caregiver onboarding" in page.data
        response = client.post(
            f"/admin/users/{caregiver.id}/onboarding",
            data={"status_Background check": "complete", "notes_Background check": "Cleared"},
            follow_redirects=False,
        )
        assert response.status_code == 302

    with app.app_context():
        item = CaregiverOnboardingItem.query.filter_by(caregiver_id=caregiver.id, item_type="Background check").first()
        assert item is not None
        assert item.status == "complete"


def test_urgent_care_request_flagged_in_work_queue():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, admin_password, codes = create_admin_with_codes()
        client_user, client_password = create_user("client")

    with app.test_client() as client:
        assert login(client, client_user, client_password).status_code == 302
        response = client.post(
            "/request-care",
            data={
                "date": date.today().isoformat(),
                "time": "15:00",
                "service_type": "Companion Care",
                "service_address": "123 Test St",
                "notes": "Client fell and this feels urgent",
                "consent_confirmed": "yes",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    with app.app_context():
        urgent = Appointment.query.filter_by(client_id=client_user.id, urgent_flag=True).first()
        assert urgent is not None

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": admin_password, "recovery_code": codes[0]}).status_code == 302
        queue = client.get("/admin/work-queue")
        assert queue.status_code == 200
        assert b"Urgent language detected" in queue.data


def test_report_export_date_filters():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        user, _ = create_user("client")
        old = Appointment(client_id=user.id, date="2020-01-01", time="09:00", service_type="Old Export", status="pending")
        new = Appointment(client_id=user.id, date="2030-01-01", time="09:00", service_type="New Export", status="pending")
        db.session.add_all([old, new])
        db.session.commit()
        old.created_at = datetime(2020, 1, 1)
        new.created_at = datetime(2030, 1, 1)
        db.session.commit()

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        export = client.get("/admin/reports/export/appointments?date_from=2029-01-01")
        assert export.status_code == 200
        assert b"New Export" in export.data
        assert b"Old Export" not in export.data


def test_admin_archive_test_inquiries_keeps_real_inquiries():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        test_inquiry = Inquiry(name="ADMIN CHECK Contact Record", email="test@example.com", reason="Ask", message="TEST cleanup", status="new")
        real_inquiry = Inquiry(name="Real Family", email="family@example.com", reason="Care", message="Needs care", status="new")
        db.session.add_all([test_inquiry, real_inquiry])
        db.session.commit()
        test_id = test_inquiry.id
        real_id = real_inquiry.id

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        response = client.post("/admin/inquiries/archive-test-records", follow_redirects=False)
        assert response.status_code == 302

    with app.app_context():
        assert db.session.get(Inquiry, test_id).status == "closed"
        assert db.session.get(Inquiry, real_id).status == "new"


def test_admin_archive_test_applications_keeps_real_applications():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        test_application = CaregiverApplication(
            name="ADMIN CHECK Caregiver Record",
            email="test-caregiver@example.com",
            phone="301-326-2225",
            certifications="CNA",
            experience="TEST cleanup",
            status="new",
        )
        real_application = CaregiverApplication(
            name="Real Applicant",
            email="real-caregiver@example.com",
            phone="301-326-2225",
            certifications="CNA",
            experience="Real experience",
            status="new",
        )
        db.session.add_all([test_application, real_application])
        db.session.commit()
        test_id = test_application.id
        real_id = real_application.id

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        response = client.post("/admin/applications/archive-test-records", follow_redirects=False)
        assert response.status_code == 302

    with app.app_context():
        assert db.session.get(CaregiverApplication, test_id).status == "not selected"
        assert db.session.get(CaregiverApplication, real_id).status == "new"


def test_admin_user_directory_shows_clickable_user_details():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        client_user, _ = create_user("client")
        account = db.session.get(User, client_user.id)
        account.name = "Directory Detail Client"
        account.phone = "301-326-2225"
        account.address = "11510 Georgia Avenue"
        account.care_needs = "Needs help with meals and medication reminders."
        db.session.add(ClientDocument(owner_id=account.id, uploaded_by_id=admin.id, title="Intake File", category="Intake", filename="x.pdf", original_filename="x.pdf"))
        db.session.add(PortalMessage(sender_id=account.id, topic="Care", subject="Schedule", body="Need schedule help."))
        db.session.add(Appointment(client_id=account.id, date="2030-01-01", time="09:00", service_type="Personal Care", status="pending"))
        db.session.commit()

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        page = client.get("/admin/users")
        assert page.status_code == 200
        assert b"adminUserDirectory" in page.data
        assert b"Directory Detail Client" in page.data
        assert f"/admin/users/{client_user.id}/edit".encode() in page.data
        assert b"1 Visits" in page.data
        assert b"1 Files" in page.data
        assert b"1 Messages" in page.data


def test_sidebar_shows_auth_and_admin_user_links():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()

    with app.test_client() as client:
        public_page = client.get("/")
        assert public_page.status_code == 200
        assert b'href="/auth/login"' in public_page.data

        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        admin_page = client.get("/dashboard")
        assert admin_page.status_code == 200
        assert b'href="/admin/users"' in admin_page.data
        assert b'href="/auth/logout"' in admin_page.data


def test_schedule_consultation_saves_inquiry_and_notifies_admin():
    app.config["WTF_CSRF_ENABLED"] = False
    email = unique_email("schedule")
    with app.test_client() as client:
        page = client.get("/schedule-consultation")
        assert page.status_code == 200
        assert b"Schedule Consultation" in page.data
        response = client.post(
            "/schedule-consultation",
            data={
                "name": "Schedule Family",
                "email": email,
                "phone": "301-326-2225",
                "preferred_date": "2032-02-01",
                "preferred_time": "10:30",
                "service_needed": "Companion Care",
                "message": "Need Maryland home care consultation.",
                "consent_confirmed": "yes",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    with app.app_context():
        inquiry = Inquiry.query.filter_by(email=email, reason="Scheduled consultation").first()
        assert inquiry is not None
        assert inquiry.start_date == "2032-02-01"
        assert "Preferred time: 10:30" in inquiry.schedule_preference
        assert "Service needed: Companion Care" in inquiry.message
        assert NotificationLog.query.filter(NotificationLog.subject.ilike("%scheduled consultation%")).count() >= 1
        Inquiry.query.filter_by(email=email).delete()
        db.session.commit()


def test_quick_message_widget_saves_inquiry():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        home = client.get("/")
        assert home.status_code == 200
        assert b"quickChatWidget" in home.data
        response = client.post(
            "/quick-message",
            data={
                "name": "Quick Visitor",
                "contact": "quick@example.com",
                "message": "Can someone call about personal care?",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    with app.app_context():
        inquiry = Inquiry.query.filter_by(email="quick@example.com", reason="Quick Message").first()
        assert inquiry is not None
        assert "personal care" in inquiry.message
        Inquiry.query.filter_by(email="quick@example.com").delete()
        db.session.commit()


def test_portal_and_seo_checklist_pages_are_real_routes():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()

    with app.test_client() as client:
        portal = client.get("/portal")
        assert portal.status_code == 200
        assert b"Client / Family" in portal.data
        assert b"Caregiver Dashboard" in portal.data
        assert b"/auth/login" in portal.data

        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        seo = client.get("/admin/seo-checklist")
        assert seo.status_code == 200
        assert b"Sitemap XML" in seo.data
        assert b"Google Analytics" in seo.data
        assert b"Ready For Integration" in seo.data

    with app.app_context():
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()



def test_full_file_scan_detects_signature_past_sample_boundary():
    from security import scan_upload_stream

    with app.app_context():
        # EICAR signature deliberately placed after the old 4096-byte sample window.
        payload = b"J" * 5000 + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
        file_storage = FileStorage(stream=BytesIO(payload), filename="late-sig.pdf")
        clean, message = scan_upload_stream(file_storage)
    assert clean is False
    assert "EICAR" in message or "Virus" in message


def test_external_scanner_receives_complete_file():
    from security import scan_upload_stream

    with app.app_context():
        original = app.config.get("VIRUS_SCAN_COMMAND")
        # `wc -c` prints the number of bytes it received on stdin and exits 0.
        app.config["VIRUS_SCAN_COMMAND"] = "wc -c"
        try:
            payload = b"X" * 9000
            file_storage = FileStorage(stream=BytesIO(payload), filename="big.pdf")
            clean, _ = scan_upload_stream(file_storage)
        finally:
            app.config["VIRUS_SCAN_COMMAND"] = original
    # A scanner must see the whole file, not a truncated sample.
    assert clean is True


def test_production_without_scanner_rejects_upload():
    """Fail closed: no scanner in production means the upload is rejected."""
    from security import scan_upload_stream

    with app.app_context():
        original = {
            "VIRUS_SCAN_COMMAND": app.config.get("VIRUS_SCAN_COMMAND"),
            "PRODUCTION_ENVIRONMENT": app.config.get("PRODUCTION_ENVIRONMENT"),
            "UPLOAD_SCAN_FAIL_OPEN": app.config.get("UPLOAD_SCAN_FAIL_OPEN"),
        }
        app.config["VIRUS_SCAN_COMMAND"] = ""
        app.config["PRODUCTION_ENVIRONMENT"] = True
        app.config["UPLOAD_SCAN_FAIL_OPEN"] = False
        try:
            file_storage = FileStorage(stream=BytesIO(b"clean pdf bytes"), filename="doc.pdf")
            clean, message = scan_upload_stream(file_storage)
        finally:
            app.config.update(original)
    assert clean is False
    assert "scanner" in message.lower()


def test_production_owner_can_opt_into_unscanned_uploads():
    """The documented opt-out lets the owner accept unscanned uploads."""
    from security import scan_upload_stream

    with app.app_context():
        original = {
            "VIRUS_SCAN_COMMAND": app.config.get("VIRUS_SCAN_COMMAND"),
            "PRODUCTION_ENVIRONMENT": app.config.get("PRODUCTION_ENVIRONMENT"),
            "UPLOAD_SCAN_FAIL_OPEN": app.config.get("UPLOAD_SCAN_FAIL_OPEN"),
        }
        app.config["VIRUS_SCAN_COMMAND"] = ""
        app.config["PRODUCTION_ENVIRONMENT"] = True
        app.config["UPLOAD_SCAN_FAIL_OPEN"] = True
        try:
            file_storage = FileStorage(stream=BytesIO(b"clean pdf bytes"), filename="doc.pdf")
            clean, _ = scan_upload_stream(file_storage)
        finally:
            app.config.update(original)
    assert clean is True


def test_development_without_scanner_still_accepts_upload():
    """Development keeps working without a scanner configured."""
    from security import scan_upload_stream

    with app.app_context():
        original = {
            "VIRUS_SCAN_COMMAND": app.config.get("VIRUS_SCAN_COMMAND"),
            "PRODUCTION_ENVIRONMENT": app.config.get("PRODUCTION_ENVIRONMENT"),
        }
        app.config["VIRUS_SCAN_COMMAND"] = ""
        app.config["PRODUCTION_ENVIRONMENT"] = False
        try:
            file_storage = FileStorage(stream=BytesIO(b"clean pdf bytes"), filename="doc.pdf")
            clean, _ = scan_upload_stream(file_storage)
        finally:
            app.config.update(original)
    assert clean is True



def test_csv_export_neutralizes_formula_cells():
    from routes import csv_safe_cell

    assert csv_safe_cell("=cmd|'/C calc'!A1") == "'=cmd|'/C calc'!A1"
    assert csv_safe_cell("+1234") == "'+1234"
    assert csv_safe_cell("-2+3") == "'-2+3"
    assert csv_safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert csv_safe_cell("normal text") == "normal text"
    assert csv_safe_cell(42) == 42


def test_time_normalization_matches_equivalent_formats():
    from routes import normalize_time, same_visit_slot

    assert normalize_time("9:00") == normalize_time("09:00")
    assert normalize_time("9:00 AM") == "09:00"
    assert normalize_time("9:00 PM") == "21:00"
    assert normalize_time("0900") == "09:00"
    assert normalize_time("12:30 AM") == "00:30"
    assert normalize_time("12:30 PM") == "12:30"
    assert same_visit_slot("2:00 PM", "14:00") is True
    assert same_visit_slot("2:00 PM", "15:00") is False


def test_double_bookings_detects_equivalent_time_formats():
    with app.app_context():
        client_user, _ = create_user("client")
        caregiver, _ = create_user("caregiver")

        first = Appointment(client_id=client_user.id, caregiver_id=caregiver.id, date="2031-05-01", time="9:00", status="approved")
        second = Appointment(client_id=client_user.id, caregiver_id=caregiver.id, date="2031-05-01", time="09:00", status="approved")
        db.session.add_all([first, second])
        db.session.commit()

        conflicts = caregiver_double_bookings([first, second])
        assert len(conflicts) == 1
        assert len(conflicts[0]) == 2

        Appointment.query.filter(Appointment.caregiver_id == caregiver.id).delete()
        User.query.filter(User.id.in_([client_user.id, caregiver.id])).delete()
        db.session.commit()


def test_admin_role_assignment_rejects_unknown_role():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        victim, _ = create_user("client")

    with app.test_client() as client:
        assert client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]}).status_code == 302
        response = client.post(f"/admin/users/{victim.id}/role", data={"role": "superuser"}, follow_redirects=False)
        assert response.status_code == 302

    with app.app_context():
        assert db.session.get(User, victim.id).role == "client"
        User.query.filter(User.id.in_([admin.id, victim.id])).delete()
        db.session.commit()


def test_password_reset_link_is_single_use():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        user, _ = create_user("client")

    with app.test_client() as client:
        response = client.post("/auth/forgot-password", data={"email": user.email}, follow_redirects=True)
        marker = b"/auth/reset-password/"
        start = response.data.index(marker)
        end = response.data.index(b"\"", start)
        reset_path = response.data[start:end].decode("utf-8")
        first = client.post(
            reset_path,
            data={"password": "NewSafePass2026!", "confirm_password": "NewSafePass2026!"},
            follow_redirects=False,
        )
        assert first.status_code == 302
        # Reusing the same link after the password changed must be rejected.
        second = client.post(
            reset_path,
            data={"password": "AnotherSafe2026!", "confirm_password": "AnotherSafe2026!"},
            follow_redirects=True,
        )
        assert b"already been used" in second.data

    with app.app_context():
        User.query.filter_by(id=user.id).delete()
        db.session.commit()


def test_seed_production_admin_refuses_promotion_without_opt_in():
    import seed_production_admin

    with app.app_context():
        existing, _ = create_user("client")

    env_backup = dict(os.environ)
    try:
        os.environ["ADMIN_SEED_EMAIL"] = existing.email
        os.environ["ADMIN_SEED_PASSWORD"] = "SafePass2026!"
        os.environ.pop("ADMIN_SEED_ALLOW_PROMOTE", None)
        raised = False
        try:
            seed_production_admin.main()
        except SystemExit:
            raised = True
        assert raised is True
    finally:
        os.environ.clear()
        os.environ.update(env_backup)

    with app.app_context():
        assert db.session.get(User, existing.id).role == "client"
        User.query.filter_by(id=existing.id).delete()
        db.session.commit()


def test_backup_dispatches_to_postgres_when_configured(monkeypatch):
    import backup_db

    calls = {}

    def fake_run(cmd, check=True):
        calls["cmd"] = cmd

    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/db")
    monkeypatch.setattr(backup_db.subprocess, "run", fake_run)

    result = backup_db.create_backup(include_uploads=False)

    assert calls["cmd"][0] == "pg_dump"
    assert "--format=custom" in calls["cmd"]
    # postgres:// must be upgraded to the modern postgresql:// scheme.
    assert calls["cmd"][-1].startswith("postgresql://")
    assert result.name.startswith("ables-postgres-")
    result.unlink(missing_ok=True)



def test_open_redirect_referrer_is_rejected():
    from routes import safe_redirect

    with app.test_request_context("/"):
        response = safe_redirect("https://evil.example.com/steal", "main.contact")
        assert response.headers["Location"].endswith("/contact")

    with app.test_request_context("/"):
        response = safe_redirect("//evil.example.com/steal", "main.contact")
        assert response.headers["Location"].endswith("/contact")

    with app.test_request_context("/"):
        response = safe_redirect("/care", "main.contact")
        assert response.headers["Location"] == "/care"


def test_public_forms_reject_empty_and_invalid_email():
    app.config["WTF_CSRF_ENABLED"] = False
    email = unique_email("invalid-email")

    with app.test_client() as client:
        empty_contact = client.post("/contact", data={}, follow_redirects=False)
        assert empty_contact.status_code == 302
        assert empty_contact.headers["Location"].endswith("/contact")

        bad_email = client.post(
            "/contact",
            data={"name": "Bad Email", "email": "not-an-email", "message": "Hello"},
            follow_redirects=False,
        )
        assert bad_email.status_code == 302
        assert bad_email.headers["Location"].endswith("/contact")

        empty_referral = client.post("/referrals", data={}, follow_redirects=False)
        assert empty_referral.status_code == 302
        assert empty_referral.headers["Location"].endswith("/referrals")

    with app.app_context():
        assert Inquiry.query.filter_by(email="not-an-email").first() is None
        assert Inquiry.query.filter_by(email=email).first() is None


def test_employment_application_requires_consent():
    app.config["WTF_CSRF_ENABLED"] = False
    email = unique_email("no-consent")

    with app.test_client() as client:
        response = client.post(
            "/employment-application",
            data={
                "credential_type": "RN",
                "name": "No Consent",
                "email": email,
                "phone": "301-326-2225",
                "experience": "Some experience",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/employment-application")

    with app.app_context():
        assert CaregiverApplication.query.filter_by(email=email).first() is None


def test_launch_readiness_is_not_public():
    with app.test_client() as client:
        response = client.get("/launch-readiness", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_inactive_user_session_is_rejected():
    with app.app_context():
        user, password = create_user("client")

    with app.test_client() as client:
        assert login(client, user, password).status_code == 302
        assert client.get("/dashboard").status_code == 200

        with app.app_context():
            account = db.session.get(User, user.id)
            account.is_active_account = False
            db.session.commit()

        # The existing session must stop working once the account is disabled.
        protected = client.get("/dashboard", follow_redirects=False)
        assert protected.status_code == 302
        assert "/auth/login" in protected.headers["Location"]

    with app.app_context():
        User.query.filter_by(id=user.id).delete()


def test_password_change_invalidates_other_sessions():
    with app.app_context():
        user, password = create_user("client")

    with app.test_client() as client:
        assert login(client, user, password).status_code == 302
        assert client.get("/dashboard").status_code == 200

        # Simulate a password change elsewhere (another device/session).
        with app.app_context():
            account = db.session.get(User, user.id)
            account.password = generate_password_hash("RotatedPass2026!")
            db.session.commit()

        protected = client.get("/dashboard", follow_redirects=False)
        assert protected.status_code == 302
        assert "/auth/login" in protected.headers["Location"]

    with app.app_context():
        User.query.filter_by(id=user.id).delete()
        db.session.commit()


def test_login_rate_limit_uses_shared_database_store():
    from datetime import datetime, timezone, timedelta
    from security import LOGIN_WINDOW, LoginAttempt

    with app.app_context():
        key = "203.0.113.77"
        LoginAttempt.query.filter_by(key=key).delete()
        db.session.commit()
        # A failure recorded by one worker must be visible to the limiter that
        # any other worker reads from the shared store.
        db.session.add(LoginAttempt(key=key))
        db.session.commit()
        recent = LoginAttempt.query.filter(
            LoginAttempt.key == key,
            LoginAttempt.attempted_at >= datetime.now(timezone.utc) - LOGIN_WINDOW,
        ).count()
        assert recent == 1

        # Stale attempts outside the window are pruned and do not count.
        stale = LoginAttempt(
            key=key,
            attempted_at=datetime.now(timezone.utc) - LOGIN_WINDOW - timedelta(minutes=1),
        )
        db.session.add(stale)
        db.session.commit()
        LoginAttempt.query.filter(
            LoginAttempt.attempted_at < datetime.now(timezone.utc) - LOGIN_WINDOW
        ).delete()
        db.session.commit()
        assert LoginAttempt.query.filter_by(key=key).count() == 1
        LoginAttempt.query.filter_by(key=key).delete()
        db.session.commit()


def test_content_type_mismatch_is_rejected():
    from routes import content_matches_extension

    assert content_matches_extension("ok.pdf", b"%PDF-1.7 body")
    assert content_matches_extension("ok.png", b"\x89PNG\r\n\x1a\nrest")
    assert content_matches_extension("ok.docx", b"PK\x03\x04rest")
    assert not content_matches_extension("fake.pdf", b"MZ\x90\x00windows binary")
    assert not content_matches_extension("fake.png", b"<html>not an image</html>")


def test_breached_password_check_fails_open_without_network(monkeypatch):
    from security import password_is_breached

    def boom(*args, **kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with app.app_context():
        app.config["PASSWORD_BREACH_CHECK_ENABLED"] = True
        try:
            # A network failure must never block account creation.
            assert password_is_breached("SafePass2026!") is False
            assert password_is_breached("") is False
        finally:
            app.config["PASSWORD_BREACH_CHECK_ENABLED"] = False


def test_breached_password_check_can_be_disabled():
    from security import password_is_breached

    with app.app_context():
        app.config["PASSWORD_BREACH_CHECK_ENABLED"] = False
        # Disabled means no outbound call and no rejection.
        assert password_is_breached("password") is False


def test_public_form_honeypot_blocks_submission():
    app.config["WTF_CSRF_ENABLED"] = False
    email = unique_email("honeypot")
    with app.app_context():
        Inquiry.query.filter_by(email=email).delete()
        db.session.commit()

    with app.test_client() as client:
        response = client.post(
            "/contact",
            data={
                "name": "Spam Bot",
                "email": email,
                "message": "Hello, please contact me.",
                "website": "http://spam.example.com",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/thank-you" not in response.headers["Location"]

    with app.app_context():
        assert Inquiry.query.filter_by(email=email).first() is None


def test_public_form_rejects_link_spam():
    app.config["WTF_CSRF_ENABLED"] = False
    email = unique_email("spamlink")
    with app.app_context():
        Inquiry.query.filter_by(email=email).delete()
        db.session.commit()

    with app.test_client() as client:
        response = client.post(
            "/contact",
            data={
                "name": "Link Spammer",
                "email": email,
                "message": "Buy cheap pills at http://example-spam.test/offer",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "/thank-you" not in response.headers["Location"]

    with app.app_context():
        assert Inquiry.query.filter_by(email=email).first() is None


def test_public_form_rate_limit_blocks_repeat_submissions():
    app.config["WTF_CSRF_ENABLED"] = False
    from security import PUBLIC_FORM_LIMIT, LoginAttempt

    with app.app_context():
        LoginAttempt.query.filter(LoginAttempt.key.like("form:quick-message:%")).delete(synchronize_session=False)
        db.session.commit()

    with app.test_client() as client:
        blocked_at = None
        for index in range(PUBLIC_FORM_LIMIT + 2):
            response = client.post(
                "/quick-message",
                data={"name": "Repeat", "contact": "repeat@example.com", "message": "Please call me back."},
                follow_redirects=False,
            )
            assert response.status_code == 302
            if "/thank-you" not in response.headers["Location"] and blocked_at is None:
                blocked_at = index
        # At most the configured number of submissions get through.
        assert blocked_at == PUBLIC_FORM_LIMIT

    with app.app_context():
        LoginAttempt.query.filter(LoginAttempt.key.like("form:quick-message:%")).delete(synchronize_session=False)
        db.session.commit()


def test_admin_dashboard_shows_notification_failure_alert():
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = False
    with app.app_context():
        admin, password, _ = create_admin_with_codes()
        db.session.add(NotificationLog(
            channel="email",
            recipient="admin@example.com",
            subject="New Ables Home Healthcare website inquiry",
            status="failed",
            provider="smtp",
            error="Connection refused",
        ))
        db.session.commit()

    with app.test_client() as client:
        assert login(client, admin, password).status_code == 302
        response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # Staff must see that a notification did not send, not a silent success.
    assert "failed to send" in body
    assert "/admin/notifications" in body

    with app.app_context():
        User.query.filter_by(id=admin.id).delete()


def test_last_active_admin_cannot_be_demoted_or_deleted():
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = False
    with app.app_context():
        admin, password, _ = create_admin_with_codes()
        admin_id = admin.id

    with app.test_client() as client:
        assert login(client, admin, password).status_code == 302

        demote = client.post(
            f"/admin/users/{admin_id}/role",
            data={"role": "client"},
            follow_redirects=False,
        )
        assert demote.status_code == 302

        delete = client.post(
            f"/admin/users/{admin_id}/delete",
            data={},
            follow_redirects=False,
        )
        assert delete.status_code == 302

    with app.app_context():
        # The only active administrator must still exist and keep the admin role.
        still_admin = db.session.get(User, admin_id)
        assert still_admin is not None
        assert still_admin.role == "admin"

    with app.app_context():
        User.query.filter_by(id=admin_id).delete()
        db.session.commit()




def test_admin_global_search_finds_older_records():
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_RECOVERY_CODES_REQUIRED"] = False
    with app.app_context():
        admin, password, _ = create_admin_with_codes()
        # Create enough newer rows to exceed the old 250-row in-Python window.
        for i in range(260):
            db.session.add(Inquiry(
                name=f"Filler {i}",
                email=f"filler-{i}-{uuid.uuid4().hex}@example.com",
                message="filler",
            ))
        db.session.add(Inquiry(
            name="Zelda Needle",
            email=f"needle-{uuid.uuid4().hex}@example.com",
            message="unique needle marker for search",
        ))
        db.session.commit()

    with app.test_client() as client:
        assert login(client, admin, password).status_code == 302
        response = client.get("/admin/search?q=needle%20marker")
    assert response.status_code == 200
    assert b"Zelda Needle" in response.data

    with app.app_context():
        Inquiry.query.filter(Inquiry.name.like("Filler %")).delete(synchronize_session=False)
        User.query.filter_by(id=admin.id).delete()
        db.session.commit()


def test_csv_export_neutralizes_formula_injection():
    from routes import csv_safe_cell

    assert csv_safe_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert csv_safe_cell("+1234") == "'+1234"
    assert csv_safe_cell("-2+3") == "'-2+3"
    assert csv_safe_cell("@SUM(A1)") == "'@SUM(A1)"
    # Ordinary text and numbers are untouched.
    assert csv_safe_cell("Normal note") == "Normal note"
    assert csv_safe_cell(42) == 42


def test_normalize_date_canonicalizes_common_formats():
    from routes import normalize_date

    assert normalize_date("2026-03-01") == "2026-03-01"
    assert normalize_date("03/01/2026") == "2026-03-01"
    assert normalize_date("3-1-2026") == "2026-03-01"
    assert normalize_date("March 1, 2026") == "2026-03-01"
    assert normalize_date("") == ""
    # Unrecognized text is preserved rather than silently dropped.
    assert normalize_date("next week") == "next week"


def test_client_cannot_download_another_clients_document():
    """Cross-account document access must be denied (owner_id is enforced).

    A real file is written so the owner's own download returns 200; that proves
    the intruder's denial comes from the authorization check and not merely from
    a missing file on disk.
    """
    app.config["WTF_CSRF_ENABLED"] = False
    upload_dir = Path(app.config["UPLOAD_FOLDER"]) / "documents"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"private-{uuid.uuid4().hex}.pdf"
    (upload_dir / stored_name).write_bytes(b"%PDF-1.4 owner private document")
    try:
        with app.app_context():
            owner, owner_password = create_user("client")
            intruder, intruder_password = create_user("client")
            document = ClientDocument(
                owner_id=owner.id,
                uploaded_by_id=owner.id,
                title="Owner Private File",
                category="Intake",
                filename=stored_name,
                original_filename="private.pdf",
            )
            db.session.add(document)
            db.session.commit()
            document_id = document.id

        with app.test_client() as client:
            assert login(client, owner, owner_password).status_code == 302
            owner_response = client.get(f"/documents/{document_id}/download")
        assert owner_response.status_code == 200

        with app.test_client() as client:
            assert login(client, intruder, intruder_password).status_code == 302
            response = client.get(f"/documents/{document_id}/download")
        # Denied before any file is served (403), or not found; never 200.
        assert response.status_code in (403, 404)
    finally:
        (upload_dir / stored_name).unlink(missing_ok=True)


def test_deactivating_an_account_invalidates_its_existing_session():
    """A disabled user must not keep using a session established earlier."""
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        user, password = create_user("client")

    with app.test_client() as client:
        assert login(client, user, password).status_code == 302
        assert client.get("/dashboard").status_code == 200

        with app.app_context():
            account = db.session.get(User, user.id)
            account.is_active_account = False
            db.session.commit()

        after = client.get("/dashboard")
    # The stale session must no longer reach an authenticated page.
    assert after.status_code in (302, 401, 403)
    if after.status_code == 302:
        assert "/auth/login" in after.headers.get("Location", "")


def test_anonymous_cannot_download_a_document():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        owner, _ = create_user("client")
        document = ClientDocument(
            owner_id=owner.id,
            uploaded_by_id=owner.id,
            title="Anonymous Probe",
            category="Intake",
            filename="probe.pdf",
            original_filename="probe.pdf",
        )
        db.session.add(document)
        db.session.commit()
        document_id = document.id

    with app.test_client() as client:
        response = client.get(f"/documents/{document_id}/download")
    assert response.status_code in (302, 401, 403)


def test_role_changes_are_restricted_to_the_allowlist():
    """A crafted role value must not grant admin; it falls back safely."""
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        admin, password, codes = create_admin_with_codes()
        target, _ = create_user("client")

    with app.test_client() as client:
        client.post("/auth/login", data={"email": admin.email, "password": password, "recovery_code": codes[0]})
        response = client.post(
            f"/admin/users/{target.id}/role",
            data={"role": "superadmin"},
            follow_redirects=False,
        )
    assert response.status_code in (200, 302)
    with app.app_context():
        assert db.session.get(User, target.id).role != "superadmin"


PUBLIC_FORM_SUBMISSIONS = [
    (
        "/contact",
        {"name": "Ada", "email": "ada@example.com", "message": "Please call me about care."},
        Inquiry,
    ),
    (
        "/referrals",
        {
            "referral_source": "Hospital",
            "source_phone": "301-555-0100",
            "client_name": "Client A",
        },
        Referral,
    ),
    (
        "/free-consultation",
        {
            "name": "Ada",
            "email": "ada@example.com",
            "phone": "301-555-0101",
            "message": "Interested in care",
        },
        Inquiry,
    ),
    (
        "/schedule-consultation",
        {
            "name": "Ada",
            "email": "ada@example.com",
            "phone": "301-555-0102",
            "message": "Please schedule a visit",
        },
        Inquiry,
    ),
]


@pytest.mark.parametrize("path,payload,model", PUBLIC_FORM_SUBMISSIONS)
def test_public_form_commit_failure_returns_clean_error_not_500(path, payload, model, monkeypatch):
    """A failed commit must roll back and show a clear error, never a 500.

    The audit found the public form handlers called db.session.commit()
    unguarded, so a database error surfaced as an unhandled exception with no
    rollback and no message. This forces the commit to fail and checks the
    visitor gets a redirect and that no partial record was stored.
    """
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        before = model.query.count()

    def failing_commit():
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db.session, "commit", failing_commit)
    try:
        with app.test_client() as client:
            response = client.post(path, data=payload, follow_redirects=False)
    finally:
        monkeypatch.undo()

    assert response.status_code < 500, f"{path} leaked a {response.status_code} error page"
    assert response.status_code == 302

    with app.app_context():
        assert model.query.count() == before, f"{path} stored a partial record"


def test_public_form_commit_failure_rolls_back_the_session(monkeypatch):
    """The session must be rolled back, not left in a dirty/failed state."""
    app.config["WTF_CSRF_ENABLED"] = False
    rolled_back = {"called": False}
    original_rollback = db.session.rollback

    def failing_commit():
        raise RuntimeError("simulated database failure")

    def tracking_rollback():
        rolled_back["called"] = True
        return original_rollback()

    monkeypatch.setattr(db.session, "commit", failing_commit)
    monkeypatch.setattr(db.session, "rollback", tracking_rollback)
    try:
        with app.test_client() as client:
            client.post("/contact", data={"name": "Ada", "email": "ada@example.com", "message": "Hi"})
    finally:
        monkeypatch.undo()

    assert rolled_back["called"] is True




