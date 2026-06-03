from datetime import date, datetime
from io import BytesIO
import os
import subprocess
import sys
from types import SimpleNamespace
import uuid

from werkzeug.security import generate_password_hash
from werkzeug.datastructures import FileStorage

from app import app
from extensions import db
from models import AdminRecoveryCode, Appointment, AuditLog, CaregiverApplication, CaregiverOnboardingItem, ClientDocument, Inquiry, NotificationLog, PortalMessage, User
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
    assert data["checks"]["database"] == "ok"
    assert data["checks"]["uploads"] == "ok"


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
        good = FileStorage(stream=BytesIO(b"%PDF safe"), filename="safe.pdf")
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
                "document": (BytesIO(b"safe file"), "test-document.pdf"),
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
        assert db.session.get(CaregiverApplication, test_id).status == "not a fit"
        assert db.session.get(CaregiverApplication, real_id).status == "new"

