from extensions import db, login_manager
from flask_login import UserMixin
from datetime import datetime, timezone


# ---------------- USER ----------------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

    role = db.Column(db.String(20), default="client")
    phone = db.Column(db.String(50))
    address = db.Column(db.String(200))
    emergency_contact_name = db.Column(db.String(120))
    emergency_contact_phone = db.Column(db.String(50))
    care_needs = db.Column(db.Text)
    preferred_schedule = db.Column(db.String(200))
    is_active_account = db.Column(db.Boolean, default=True)
    failed_login_count = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    password_changed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------- APPOINTMENT ----------------
class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    client_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id")
    )

    caregiver_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True
    )

    date = db.Column(db.String(50))
    time = db.Column(db.String(50))
    service_type = db.Column(db.String(100))
    service_address = db.Column(db.String(220))
    schedule_preference = db.Column(db.String(220))
    mobility_needs = db.Column(db.Text)
    emergency_contact = db.Column(db.String(220))
    preferred_language = db.Column(db.String(80))
    consent_confirmed = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    caregiver_notes = db.Column(db.Text)
    recurring_group_id = db.Column(db.String(80))
    recurring_interval_weeks = db.Column(db.Integer)
    recurring_paused = db.Column(db.Boolean, default=False)
    reminder_sent_for = db.Column(db.String(40))
    urgent_flag = db.Column(db.Boolean, default=False)
    urgent_reason = db.Column(db.String(220))

    status = db.Column(
        db.String(20),
        default="pending"
    )
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    client = db.relationship(
        "User",
        foreign_keys=[client_id]
    )

    caregiver = db.relationship(
        "User",
        foreign_keys=[caregiver_id]
    )


class Inquiry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50))
    reason = db.Column(db.String(100))
    message = db.Column(db.Text, nullable=False)
    care_recipient = db.Column(db.String(120))
    service_address = db.Column(db.String(220))
    city = db.Column(db.String(120))
    start_date = db.Column(db.String(50))
    start_timeline = db.Column(db.String(120))
    services_needed = db.Column(db.String(220))
    schedule_preference = db.Column(db.String(220))
    mobility_needs = db.Column(db.Text)
    diagnosis_notes = db.Column(db.Text)
    emergency_contact = db.Column(db.String(220))
    preferred_language = db.Column(db.String(80))
    consent_confirmed = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(30), default="new")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Referral(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    referral_source = db.Column(db.String(120), nullable=False)
    source_phone = db.Column(db.String(50), nullable=False)
    client_name = db.Column(db.String(120), nullable=False)
    client_phone = db.Column(db.String(50))
    care_needed = db.Column(db.String(120))
    follow_up = db.Column(db.String(120))
    notes = db.Column(db.Text)
    status = db.Column(db.String(30), default="new")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class CarePlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inquiry_id = db.Column(db.Integer, db.ForeignKey("inquiry.id"), nullable=True)
    referral_id = db.Column(db.Integer, db.ForeignKey("referral.id"), nullable=True)
    client_name = db.Column(db.String(120), nullable=False)
    client_phone = db.Column(db.String(50))
    service_address = db.Column(db.String(220))
    service_type = db.Column(db.String(160))
    schedule = db.Column(db.String(220))
    tasks = db.Column(db.Text)
    risks = db.Column(db.Text)
    emergency_contacts = db.Column(db.Text)
    caregiver_preferences = db.Column(db.Text)
    notes = db.Column(db.Text)
    status = db.Column(db.String(40), default="draft")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    inquiry = db.relationship("Inquiry", foreign_keys=[inquiry_id])
    referral = db.relationship("Referral", foreign_keys=[referral_id])


class CaregiverApplication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    address = db.Column(db.String(220))
    availability = db.Column(db.String(220))
    weekday_availability = db.Column(db.String(220))
    weekend_availability = db.Column(db.String(220))
    certifications = db.Column(db.String(220))
    years_experience = db.Column(db.String(80))
    transportation = db.Column(db.String(120))
    preferred_service_areas = db.Column(db.String(220))
    applicant_references = db.Column(db.Text)
    background_check_consent = db.Column(db.Boolean, default=False)
    requirements_checklist = db.Column(db.Text)
    resume_filename = db.Column(db.String(220))
    experience = db.Column(db.Text)
    status = db.Column(db.String(40), default="new")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    entity_type = db.Column(db.String(80))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", foreign_keys=[user_id])


class AdminRecoveryCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    code_hash = db.Column(db.String(255), nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    admin = db.relationship("User", foreign_keys=[admin_id])


class NotificationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(20), nullable=False)
    recipient = db.Column(db.String(160))
    subject = db.Column(db.String(220))
    status = db.Column(db.String(30), nullable=False)
    provider = db.Column(db.String(80))
    related_type = db.Column(db.String(80))
    related_id = db.Column(db.Integer)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class PortalMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    audience = db.Column(db.String(40), default="office")
    topic = db.Column(db.String(80), nullable=False)
    subject = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(30), default="normal")
    status = db.Column(db.String(30), default="new")
    admin_reply = db.Column(db.Text)
    replied_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    admin_seen = db.Column(db.Boolean, default=False)
    sender_seen_reply = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    sender = db.relationship("User", foreign_keys=[sender_id])
    replied_by = db.relationship("User", foreign_keys=[replied_by_id])


class ClientDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    category = db.Column(db.String(80), default="General")
    filename = db.Column(db.String(220), nullable=False)
    original_filename = db.Column(db.String(220), nullable=False)
    notes = db.Column(db.Text)
    archived_at = db.Column(db.DateTime)
    archived_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    owner = db.relationship("User", foreign_keys=[owner_id])
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])
    archived_by = db.relationship("User", foreign_keys=[archived_by_id])


class AdminNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    note = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    subject_user = db.relationship("User", foreign_keys=[subject_user_id])
    admin = db.relationship("User", foreign_keys=[admin_id])


class CaregiverAvailability(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caregiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    weekday = db.Column(db.String(20), nullable=False)
    start_time = db.Column(db.String(20), nullable=False)
    end_time = db.Column(db.String(20), nullable=False)
    service_area = db.Column(db.String(220))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    caregiver = db.relationship("User", foreign_keys=[caregiver_id])


class CaregiverOnboardingItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caregiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    item_type = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(40), default="pending")
    notes = db.Column(db.Text)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    caregiver = db.relationship("User", foreign_keys=[caregiver_id])


# ---------------- USER LOADER ----------------
@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    # Reject deactivated accounts on every request so an existing session
    # cannot outlive an administrator disabling the account.
    if user is None or not user.is_active_account:
        return None
    return user


