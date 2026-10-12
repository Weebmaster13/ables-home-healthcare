from datetime import date, datetime, timedelta
import csv
import mimetypes
from io import StringIO
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, abort, current_app, jsonify, make_response, render_template, redirect, send_from_directory, url_for, request, flash
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash
from sqlalchemy import or_, text

from extensions import db
from models import AdminNote, AdminRecoveryCode, Appointment, AuditLog, CarePlan, CaregiverApplication, CaregiverAvailability, CaregiverOnboardingItem, ClientDocument, Inquiry, NotificationLog, PortalMessage, Referral, User
from notifications import send_admin_notification, send_email_notification, send_sms_notification
from security import audit, generate_recovery_codes, password_feedback, password_is_strong, require_permission, require_role, scan_upload_stream
from backup_db import BACKUP_DIR, cleanup_old_backups, create_backup

# ---------------- MAIN BLUEPRINT ----------------
main_bp = Blueprint("main", __name__)

DOCUMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx"}
DOCUMENT_MIME_PREFIXES = {"image/"}
DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
DOCUMENT_CATEGORIES = ["Intake", "Insurance", "Consent", "Care plan", "Medical note", "Other"]
RESUME_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt"}
RESUME_MIME_TYPES = DOCUMENT_MIME_TYPES | {"text/plain"}
BACKUP_EXTENSIONS = {".zip", ".db"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
REMINDER_OFFSETS = {"today": 0, "tomorrow": 1}
URGENT_KEYWORDS = ["fall", "fell", "chest pain", "stroke", "911", "emergency", "urgent", "not breathing", "bleeding", "hospital"]
ONBOARDING_ITEMS = ["Background check", "References", "CPR/First Aid", "Orientation", "Policy acknowledgment", "Required documents"]


def allowed_document(filename):
    return Path(filename).suffix.lower() in DOCUMENT_EXTENSIONS


def allowed_resume(filename):
    return Path(filename).suffix.lower() in RESUME_EXTENSIONS


def allowed_mime(filename, allowed_types, allowed_prefixes=None):
    guessed_type = mimetypes.guess_type(filename)[0] or ""
    allowed_prefixes = allowed_prefixes or set()
    return guessed_type in allowed_types or any(guessed_type.startswith(prefix) for prefix in allowed_prefixes)


def get_record_or_404(model, record_id):
    record = db.session.get(model, record_id)
    if not record:
        abort(404)
    return record


def parse_date_filter(value, end_of_day=False):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed


def has_urgent_language(*values):
    text_value = " ".join(str(value or "") for value in values).lower()
    hits = [word for word in URGENT_KEYWORDS if word in text_value]
    return hits


def latest_migration_revision():
    versions_dir = Path(current_app.root_path) / "migrations" / "versions"
    revisions = []
    if versions_dir.exists():
        for path in versions_dir.glob("*.py"):
            if path.name.startswith("__"):
                continue
            revisions.append(path.stem.split("_", 1)[0])
    return sorted(revisions)[-1] if revisions else ""


def current_migration_revision():
    try:
        return db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() or ""
    except Exception:
        return ""


def apply_created_at_filters(query, model):
    date_from = parse_date_filter(request.args.get("date_from", "").strip())
    date_to = parse_date_filter(request.args.get("date_to", "").strip(), end_of_day=True)
    if date_from is not None:
        query = query.filter(model.created_at >= date_from)
    if date_to is not None:
        query = query.filter(model.created_at <= date_to)
    return query


def mask_email_address(email):
    if not email or "@" not in email:
        return ""
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked_name = name[:1] + "*"
    else:
        masked_name = name[:2] + "***" + name[-1:]
    return f"{masked_name}@{domain}"


def short_status_error(error):
    if not error:
        return ""
    return " ".join(str(error).split())[:180]


def looks_like_test_record(*values):
    text = " ".join(str(value or "") for value in values).upper()
    return "TEST" in text or "ADMIN CHECK" in text


def safe_original_filename(filename, fallback="upload"):
    cleaned = secure_filename(filename or "")
    return cleaned or fallback


def save_private_upload(file_storage, folder, owner_prefix, allowed_checker):
    if not file_storage or not file_storage.filename:
        return "", ""

    original_filename = safe_original_filename(file_storage.filename)
    if not allowed_checker(original_filename):
        return "", original_filename
    if allowed_checker is allowed_document and not allowed_mime(original_filename, DOCUMENT_MIME_TYPES, DOCUMENT_MIME_PREFIXES):
        return "", original_filename
    if allowed_checker is allowed_resume and not allowed_mime(original_filename, RESUME_MIME_TYPES):
        return "", original_filename

    clean, scan_message = scan_upload_stream(file_storage)
    if not clean:
        current_app.logger.warning("Upload rejected by scan: %s", scan_message)
        audit("reject_upload_scan", "Upload", None, f"{original_filename}: {scan_message}")
        return "", original_filename

    extension = Path(original_filename).suffix.lower()
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"]) / folder
    upload_folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{owner_prefix}-{uuid4().hex}{extension}"
    file_storage.save(upload_folder / stored_name)
    return stored_name, original_filename


def csv_response(filename, headers, rows):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    response = make_response(buffer.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


def short_date(value):
    return value.strftime("%Y-%m-%d") if value else ""


def appointment_reminders(appointments):
    today_iso = date.today().isoformat()
    tomorrow_iso = date.fromordinal(date.today().toordinal() + 1).isoformat()
    active = [item for item in appointments if item.status not in ["completed", "cancelled", "rejected"]]
    return {
        "today": [item for item in active if item.date == today_iso],
        "tomorrow": [item for item in active if item.date == tomorrow_iso],
        "overdue": [item for item in active if item.date and item.date < today_iso],
    }


def appointment_weekday(date_value):
    try:
        return date.fromisoformat(date_value).strftime("%A")
    except (TypeError, ValueError):
        return ""


def caregiver_match_details(appointment, caregiver, all_appointments):
    score = 40
    reasons = []
    warnings = []
    availability = (caregiver.preferred_schedule or "").lower()
    address_text = " ".join([
        appointment.service_address or "",
        appointment.notes or "",
        appointment.schedule_preference or "",
    ]).lower()
    caregiver_area = (caregiver.address or "").lower()
    weekday = appointment_weekday(appointment.date)
    structured_slots = CaregiverAvailability.query.filter_by(
        caregiver_id=caregiver.id,
        weekday=weekday,
    ).all() if weekday else []

    conflicts = [
        item for item in all_appointments
        if item.id != appointment.id
        and item.caregiver_id == caregiver.id
        and item.date == appointment.date
        and item.time == appointment.time
        and item.status not in ["completed", "cancelled", "rejected"]
    ]
    if conflicts:
        score -= 45
        warnings.append("Possible schedule conflict")
    else:
        score += 15
        reasons.append("No exact visit conflict")

    if weekday and weekday.lower() in availability:
        score += 20
        reasons.append(f"Available text mentions {weekday}")

    if appointment.time and appointment.time.lower() in availability:
        score += 10
        reasons.append("Available text mentions this time")

    if structured_slots:
        score += 15
        reasons.append(f"Availability slot for {weekday}")
        if appointment.time:
            if any(slot.start_time <= appointment.time <= slot.end_time for slot in structured_slots):
                score += 20
                reasons.append("Structured time slot matches")
            else:
                score -= 15
                warnings.append("Outside entered time slots")

    area_tokens = [token for token in caregiver_area.replace(",", " ").split() if len(token) >= 4]
    if area_tokens and any(token in address_text for token in area_tokens):
        score += 20
        reasons.append("Service area looks similar")

    slot_area_text = " ".join(slot.service_area or "" for slot in structured_slots).lower()
    slot_area_tokens = [token for token in slot_area_text.replace(",", " ").split() if len(token) >= 4]
    if slot_area_tokens and any(token in address_text for token in slot_area_tokens):
        score += 20
        reasons.append("Availability area matches")

    care_text = " ".join([caregiver.care_needs or "", caregiver.preferred_schedule or ""]).lower()
    service_words = [word for word in (appointment.service_type or "").lower().split() if len(word) >= 4]
    if service_words and any(word in care_text for word in service_words):
        score += 10
        reasons.append("Profile mentions related service")

    if caregiver.phone:
        score += 5
        reasons.append("Phone on file")
    else:
        warnings.append("Missing caregiver phone")

    if not caregiver.preferred_schedule and not structured_slots:
        warnings.append("Availability not entered")

    return {
        "caregiver": caregiver,
        "score": max(0, min(score, 100)),
        "reasons": reasons[:3] or ["General caregiver account"],
        "warnings": warnings,
    }


def caregiver_matches_for_appointment(appointment, caregivers, all_appointments):
    matches = [
        caregiver_match_details(appointment, caregiver, all_appointments)
        for caregiver in caregivers
    ]
    return sorted(matches, key=lambda item: item["score"], reverse=True)


def caregiver_assignment_conflicts(appointment, caregiver_id):
    return Appointment.query.filter(
        Appointment.id != appointment.id,
        Appointment.caregiver_id == caregiver_id,
        Appointment.date == appointment.date,
        Appointment.time == appointment.time,
        Appointment.status.notin_(["completed", "cancelled", "rejected"]),
    ).all()


def appointment_date_or_none(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def active_appointment_query():
    return Appointment.query.filter(Appointment.status.notin_(["completed", "cancelled", "rejected", "paused"]))


def reminder_key(label):
    return f"{date.today().isoformat()}:{label}"


def send_visit_reminder(appointment, label):
    recipients = []
    sms_recipients = []
    if appointment.client and appointment.client.email:
        recipients.append(appointment.client.email)
    if appointment.client and appointment.client.phone:
        sms_recipients.append(appointment.client.phone)
    if appointment.caregiver and appointment.caregiver.email:
        recipients.append(appointment.caregiver.email)
    if appointment.caregiver and appointment.caregiver.phone:
        sms_recipients.append(appointment.caregiver.phone)

    if not recipients and not sms_recipients:
        return False

    body = "\n".join([
        f"Reminder: {appointment.service_type}",
        f"When: {appointment.date} at {appointment.time}",
        f"Client: {appointment.client.name if appointment.client else 'Unknown'}",
        f"Caregiver: {appointment.caregiver.name if appointment.caregiver else 'Not assigned'}",
        f"Address: {appointment.service_address or 'Not added'}",
        "",
        "For emergencies, call 911 first. For scheduling changes, contact Ables Home Healthcare.",
    ])
    sent = False
    for email in recipients:
        sent = send_email_notification(email, f"Ables visit reminder: {label}", body) or sent
    sms_body = f"Ables reminder: {appointment.service_type} {label}, {appointment.date} at {appointment.time}. Call office for schedule changes."
    for phone in sms_recipients:
        sent = send_sms_notification(phone, sms_body) or sent
    return sent


FOLLOW_UP_STATUSES = [
    "new",
    "reviewed",
    "contacted",
    "follow-up needed",
    "assessment scheduled",
    "care plan drafted",
    "caregiver assigned",
    "closed",
]

APPLICATION_STATUSES = [
    "new",
    "reviewed",
    "screening",
    "follow-up needed",
    "interview scheduled",
    "references pending",
    "approved",
    "not selected",
]

CARE_PLAN_STATUSES = [
    "draft",
    "reviewing",
    "ready for caregiver match",
    "active",
    "paused",
    "closed",
]

SERVICES = [
    {
        "slug": "personal-care",
        "name": "Personal Care",
        "summary": "Hands-on assistance with daily routines, hygiene, mobility, and comfort at home.",
        "details": [
            "Bathing, dressing, grooming, and toileting support",
            "Mobility assistance and fall-risk awareness",
            "Meal reminders, hydration support, and routine check-ins",
            "Respectful care that protects privacy and dignity",
        ],
    },
    {
        "slug": "companion-care",
        "name": "Companion Care",
        "summary": "Friendly support that reduces isolation and helps clients stay connected.",
        "details": [
            "Conversation, reading, games, and meaningful activities",
            "Walks, errands, social outings, and appointment companionship",
            "Wellness check-ins and family communication support",
            "Encouragement for safe daily routines",
        ],
    },
    {
        "slug": "respite-care",
        "name": "Respite Care",
        "summary": "Temporary relief for family caregivers who need rest, work time, or backup care.",
        "details": [
            "Short-term visits or recurring support",
            "Caregiver relief during errands, travel, or recovery",
            "Help maintaining the loved one's normal routine",
            "Flexible scheduling based on family needs",
        ],
    },
    {
        "slug": "dementia-alzheimers-care",
        "name": "Dementia and Alzheimer's Support",
        "summary": "Patient, routine-based support for clients living with memory changes.",
        "details": [
            "Calm companionship and routine reinforcement",
            "Safety awareness around wandering, meals, and daily activities",
            "Family updates and observation notes",
            "Supportive redirection and comfort-focused care",
        ],
    },
    {
        "slug": "post-hospital-support",
        "name": "Post-Hospital Support",
        "summary": "Non-medical help after discharge so clients can settle safely at home.",
        "details": [
            "Meal preparation, light housekeeping, and errands",
            "Medication reminders and appointment transportation support",
            "Mobility support around the home",
            "Observation of changes that families may need to know about",
        ],
    },
    {
        "slug": "live-in-24-hour-care",
        "name": "Live-In and 24-Hour Care",
        "summary": "Extended support for clients who need more frequent help throughout the day or night.",
        "details": [
            "Daytime, evening, overnight, and weekend support options",
            "Help with routines, meals, safety checks, and companionship",
            "Consistent scheduling for families needing higher-touch care",
            "Care coordination through the client and admin portal",
        ],
    },
]

SERVICE_AREAS = [
    "Anne Arundel",
    "Baltimore",
    "Calvert",
    "Caroline",
    "Carroll",
    "Charles",
    "Frederick",
    "Harford",
    "Howard",
    "Kent",
    "Montgomery County",
    "Prince Georges",
    "Queen Anne's",
    "St. Mary's",
    "Talbot",
    "Baltimore City",
]

PUBLIC_STATIC_ROUTES = [
    "main.home",
    "main.family_guide",
    "main.provider_partners",
    "main.about",
    "main.why_ables",
    "main.services",
    "main.service_areas",
    "main.care_process",
    "main.free_consultation",
    "main.schedule_consultation",
    "main.portal",
    "main.referrals",
    "main.care_standards",
    "main.caregivers",
    "main.pricing_payment",
    "main.testimonials",
    "main.care_programs",
    "main.resources",
    "main.administrator",
    "main.careers",
    "main.contact",
    "main.care",
    "main.faq",
    "main.privacy",
    "main.terms",
]

TRUST_POINTS = [
    {
        "title": "Personalized care planning",
        "body": "Care requests are reviewed around the client's daily routines, preferences, safety needs, and family expectations.",
    },
    {
        "title": "Clear communication",
        "body": "Families, clients, administrators, and caregivers all have a clearer path for scheduling and updates.",
    },
    {
        "title": "Respectful daily support",
        "body": "Care focuses on dignity, independence, comfort, and practical help in the home.",
    },
    {
        "title": "Local administrator contact",
        "body": "The office team is listed as the contact for service coordination and follow-up.",
    },
]

TESTIMONIALS = [
    {
        "quote": "This home healthcare group has been a godsend. Ables Homecare has quality staff who can provide assistance, respect, flexibility, and calming reassurance when families need help at home.",
        "name": "Carole C.",
        "context": "Maryland family",
    },
    {
        "quote": "Having help with daily routines gave our loved one more confidence at home.",
        "name": "Client family",
        "context": "Montgomery County",
    },
    {
        "quote": "The scheduling process made it easier to know what visits were requested and what came next.",
        "name": "Referral partner",
        "context": "Community care provider",
    },
]

CARE_PROGRAMS = [
    {
        "name": "Family Caregiver Relief",
        "summary": "Short visits, recurring respite, and backup support for families carrying the daily care load.",
    },
    {
        "name": "Aging in Place Support",
        "summary": "Assistance with routines that helps clients remain comfortable and safer in familiar surroundings.",
    },
    {
        "name": "After-Discharge Home Support",
        "summary": "Non-medical help after hospital or rehab discharge with meals, errands, reminders, and supervision.",
    },
]

TRUST_BADGES = [
    "Background Checked Caregivers",
    "Flexible Scheduling",
    "Family Feedback Follow-Up",
    "Maryland Service Areas",
    "Skilled and Non-Skilled Care",
    "Simple Client Intake",
]

FAMILY_EXPERIENCE = [
    {
        "title": "Calm during sudden need",
        "body": "Home care needs often appear suddenly. Families need a coordinator who can calm the waters and make the next step feel possible.",
    },
    {
        "title": "Respectful caregiver behavior",
        "body": "Families notice whether the caregiver greets the client, speaks respectfully, and treats the person as an adult with preferences.",
    },
    {
        "title": "Clean, focused care habits",
        "body": "Hand hygiene, attention during visit time, and avoiding idle phone use all raise confidence inside the home.",
    },
    {
        "title": "Flexible follow-through",
        "body": "Short-notice care, family feedback, and coordinator follow-up help families feel supported instead of alone.",
    },
]

CARE_PLAN_PREVIEW = [
    "Preferred days, times, and visit length",
    "Personal care tasks and routine support",
    "Meal, hydration, and medication reminder preferences",
    "Emergency contacts and family communication notes",
    "Caregiver observations and visit notes",
    "Follow-up changes when needs shift",
]

COMPANY_METRICS = [
    {
        "value": "Local",
        "label": "Maryland Office",
        "body": "Care coordination is centered around Maryland families and referral partners.",
    },
    {
        "value": "6",
        "label": "Core care services",
        "body": "Families can request personal care, companion care, respite, dementia support, post-hospital help, and extended care.",
    },
    {
        "value": "4-step",
        "label": "Guided intake process",
        "body": "Each request moves through consultation, needs review, care planning, and caregiver follow-up.",
    },
]

AUDIENCE_PATHS = [
    {
        "title": "Families exploring care",
        "body": "Learn what to prepare, which questions to ask, and how the first consultation works.",
        "href": "/family-guide",
        "action": "View Family Guide",
    },
    {
        "title": "Referral partners",
        "body": "Hospitals, social workers, care managers, and community organizations can send referrals quickly.",
        "href": "/provider-partners",
        "action": "Partner With Us",
    },
    {
        "title": "Caregiver applicants",
        "body": "Dependable caregivers can learn about expectations and submit an employment application.",
        "href": "/careers",
        "action": "View Careers",
    },
]

FAMILY_GUIDE_ITEMS = [
    {
        "title": "Before you call",
        "body": "Write down the client's address, daily routine, mobility concerns, preferred schedule, emergency contacts, and what changed recently.",
    },
    {
        "title": "During consultation",
        "body": "Talk through personal care, companionship, respite, dementia support, reminders, errands, and family communication needs.",
    },
    {
        "title": "After care begins",
        "body": "Review caregiver fit, visit notes, schedule changes, family feedback, and whether the plan still matches the client's routine.",
    },
]

PARTNER_TYPES = [
    "Hospital discharge planners",
    "Rehabilitation centers",
    "Physician offices",
    "Social workers",
    "Care managers",
    "Senior living communities",
    "Faith and community organizations",
    "Family caregivers",
]

PARTNER_PROMISES = [
    {
        "title": "Simple referral handoff",
        "body": "Partners can submit key client details, care needs, preferred follow-up time, and contact information in one place.",
    },
    {
        "title": "Clear follow-up path",
        "body": "The administrator can review referral details and contact the family or referral source about availability.",
    },
    {
        "title": "Non-medical scope clarity",
        "body": "The website keeps service language focused on daily living support, companionship, respite, reminders, and home routines.",
    },
]

READINESS_ITEMS = [
    {
        "title": "Real licensed photos",
        "status": "Needs business review",
        "body": "Current images are local development assets. Replace with licensed photos or real client-safe business photography before selling or launching.",
    },
    {
        "title": "SMTP email delivery",
        "status": "Configure in hosting",
        "body": "Set MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER, and ADMIN_EMAIL so forms notify the office.",
    },
    {
        "title": "Domain and HTTPS",
        "status": "Deployment step",
        "body": "Connect the production domain, force HTTPS, and set secure environment variables before using logins or forms with real families.",
    },
    {
        "title": "Legal and privacy review",
        "status": "Required before real client data",
        "body": "Have privacy, terms, service scope, testimonials, and health-related language reviewed by qualified professionals.",
    },
    {
        "title": "Production database and backups",
        "status": "Deployment step",
        "body": "Use PostgreSQL or another managed production database with scheduled encrypted backups and migration tooling.",
    },
    {
        "title": "Confirmed business details",
        "status": "Needs owner confirmation",
        "body": "Confirm licensing language, exact service scope, payment options, service areas, hours, and testimonial permission.",
    },
]

CAREGIVER_PROMISES = [
    {
        "title": "Screening and background checks",
        "body": "Families should expect caregivers to be screened and reviewed before entering a client's home.",
    },
    {
        "title": "Training and professionalism",
        "body": "Caregivers should understand respectful communication, hygiene, safety awareness, and non-medical care boundaries.",
    },
    {
        "title": "Caregiver matching",
        "body": "Matching should consider schedule, personality fit, care needs, location, and family preferences.",
    },
    {
        "title": "Supervision and follow-up",
        "body": "Administrator follow-up helps families share feedback and request adjustments when needed.",
    },
]

HERO_SLIDES = [
    {
        "theme": "hero-family-room",
        "image": "img/care/hero-family-room-v2.png",
        "eyebrow": "Skilled and Non-Skilled Nursing Care",
        "title": "Ables Home Healthcare",
        "body": "Skilled and Non-Skilled Nursing Care Provider for infants, children, adults, and older adults.",
    },
    {
        "theme": "hero-consultation",
        "image": "img/care/hero-consultation-v2.png",
        "eyebrow": "For Clients",
        "title": "Looking For Care?",
        "body": "Great Professional Service. Compassionate Care. On Time and Reliable Service.",
    },
    {
        "theme": "hero-care-standards",
        "image": "img/care/hero-care-standards-v2.png",
        "eyebrow": "Looking For Work?",
        "title": "CNA, LPN, and RN caregivers can apply online.",
        "body": "CNA, LPN, or RN? Apply to work with Ables Home Healthcare. Applications are sent to admin@ableshomehealthcare.com.",
    },
    {
        "theme": "hero-local-support",
        "image": "img/care/home-services-v2.png",
        "eyebrow": "Flexible local support",
        "title": "Call us at 301-455-3030.",
        "body": "Fax: 301-326-2270. Email: admin@ableshomehealthcare.com.",
    },
]

CONSULTATION_STEPS = [
    {
        "number": "01",
        "title": "Schedule a free conversation",
        "body": "Tell us who needs care, where care is needed, and what changed recently.",
    },
    {
        "number": "02",
        "title": "Review routines and safety",
        "body": "We discuss daily activities, mobility, supervision, meals, reminders, and family concerns.",
    },
    {
        "number": "03",
        "title": "Build a non-medical care plan",
        "body": "The plan outlines service type, schedule preferences, caregiver fit, and next steps.",
    },
    {
        "number": "04",
        "title": "Match and follow up",
        "body": "A caregiver is assigned when available, and the administrator follows up for feedback.",
    },
]

CARE_STANDARDS = [
    "Respectful greeting at the start of every visit",
    "Hand hygiene and clean, professional care habits",
    "No idle personal phone use during care time",
    "No talking down to clients or ignoring client preferences",
    "Punctuality, flexibility, and clear scheduling communication",
    "Caregiver screening, background checks, and training review",
    "Caregiver matching based on needs, schedule, and personality fit",
    "Administrator follow-up to ask families about provider progress",
]

PAYMENT_FAQS = [
    {
        "question": "How much does non-medical home care cost?",
        "answer": "Cost depends on care needs, visit length, frequency, schedule, and service type. Ables should provide a custom quote after a consultation.",
    },
    {
        "question": "Does Medicare cover this care?",
        "answer": "Medicare and standard health insurance usually do not cover non-medical help with activities of daily living. Clinical home health is different and may have separate coverage rules.",
    },
    {
        "question": "Can long-term care insurance help?",
        "answer": "Some long-term care insurance policies may reimburse home care. Families should review policy terms and ask the insurer about non-medical home care benefits.",
    },
    {
        "question": "Are veterans benefits or Medicaid waiver options available?",
        "answer": "Some families may qualify for veterans benefits or Medicaid waiver support. Eligibility varies, so benefits should be verified before care begins.",
    },
]

LOCAL_SEO_PAGES = [
    {
        "slug": "home-care-maryland-service-areas",
        "title": "Home Care In Maryland Service Areas",
        "city": "Maryland",
        "service": "home care",
        "body": "Ables Home Healthcare serves approved Maryland counties and Baltimore City with skilled and non-skilled nursing care support.",
        "local_detail": "Call 301-455-3030 to confirm availability for the client's exact address, schedule, and care needs.",
    },
    {
        "slug": "personal-care-montgomery-county-md",
        "title": "Personal Care In Montgomery County, MD",
        "city": "Montgomery County",
        "service": "personal care",
        "body": "Families in Montgomery County can request help with bathing, grooming, dressing, mobility, meals, reminders, and daily routines.",
        "local_detail": "Availability depends on schedule, caregiver match, and assessment.",
    },
    {
        "slug": "home-care-baltimore-city-md",
        "title": "Home Care In Baltimore City, MD",
        "city": "Baltimore City",
        "service": "home care",
        "body": "Baltimore City families can request skilled or non-skilled nursing care support based on client needs and staffing availability.",
        "local_detail": "Send the client address, diagnosis, weekly hours, payment method, and services required.",
    },
    {
        "slug": "respite-care-howard-county-md",
        "title": "Respite Care In Howard County, MD",
        "city": "Howard County",
        "service": "respite care",
        "body": "Howard County families can request planned relief while a caregiver supports the client at home.",
        "local_detail": "Recurring and flexible schedules depend on availability and care plan review.",
    },
    {
        "slug": "caregiver-support-anne-arundel-county-md",
        "title": "Caregiver Support In Anne Arundel County, MD",
        "city": "Anne Arundel County",
        "service": "caregiver support",
        "body": "Anne Arundel families can request caregiver support for daily living needs, companionship, and care coordination.",
        "local_detail": "Call today to confirm whether care can begin for the requested address and schedule.",
    },
    {
        "slug": "home-care-prince-georges-county-md",
        "title": "Home Care In Prince Georges County, MD",
        "city": "Prince Georges County",
        "service": "home care",
        "body": "Prince Georges County families can request skilled and non-skilled nursing care support through Ables Home Healthcare.",
        "local_detail": "Care options depend on assessment, caregiver availability, and service needs.",
    },
]

RESOURCE_ARTICLES = [
    {
        "slug": "when-is-it-time-for-home-care",
        "title": "When Is It Time for Home Care?",
        "summary": "Common signs that daily routines, safety, or caregiver stress may require extra help at home.",
        "sections": [
            "Home care may be helpful when meals, hygiene, errands, mobility, or supervision become harder to manage alone.",
            "Families often call after a fall, hospitalization, new diagnosis, caregiver burnout, or increased isolation.",
            "A consultation helps identify whether companion care, personal care, respite, or extended support is the right starting point.",
        ],
    },
    {
        "slug": "home-care-vs-home-health-care",
        "title": "Home Care vs Home Health Care",
        "summary": "Understand the difference between non-medical daily support and clinical care ordered by a medical provider.",
        "sections": [
            "Non-medical home care focuses on daily living support such as bathing, dressing, meals, errands, companionship, and reminders.",
            "Home health care is clinical care, such as nursing or therapy, often ordered by a physician for a specific medical need.",
            "Ables Home Healthcare keeps care conversations clear by distinguishing daily living support from physician-ordered clinical services.",
        ],
    },
    {
        "slug": "prepare-home-for-caregiver",
        "title": "How to Prepare the Home for a Caregiver",
        "summary": "Simple steps families can take before the first visit.",
        "sections": [
            "Clear walkways, improve lighting, organize supplies, and write down daily routines.",
            "Share emergency contacts, medication reminder routines, dietary preferences, mobility concerns, and household rules.",
            "Prepare a quiet conversation about preferences so the client feels respected and included.",
        ],
    },
    {
        "slug": "questions-before-hiring-home-care",
        "title": "Questions to Ask Before Hiring a Home Care Agency",
        "summary": "Use these questions to compare agencies and choose support with confidence.",
        "sections": [
            "Ask about caregiver screening, background checks, training, scheduling, backup care, communication, and supervision.",
            "Ask how care plans are created and how quickly service can begin after consultation.",
            "Ask how feedback is handled when a family wants schedule changes or caregiver adjustments.",
        ],
    },
    {
        "slug": "respite-care-family-caregivers",
        "title": "Respite Care for Family Caregivers",
        "summary": "Why temporary relief can help families keep loved ones at home longer.",
        "sections": [
            "Family caregivers often need rest, appointments, work time, or emotional breathing room.",
            "Respite care can be short-term, recurring, or used during transitions after illness or hospitalization.",
            "A flexible plan helps families avoid burnout while keeping the client supported in familiar surroundings.",
        ],
    },
]


# ---------------- HOME ----------------
@main_bp.route("/")
def home():
    return render_template(
        "home.html",
        services=SERVICES,
        service_areas=SERVICE_AREAS,
        trust_points=TRUST_POINTS,
        testimonials=TESTIMONIALS,
        care_programs=CARE_PROGRAMS,
        hero_slides=HERO_SLIDES,
        trust_badges=TRUST_BADGES,
        consultation_steps=CONSULTATION_STEPS,
        family_experience=FAMILY_EXPERIENCE,
        care_plan_preview=CARE_PLAN_PREVIEW,
        company_metrics=COMPANY_METRICS,
        audience_paths=AUDIENCE_PATHS,
    )


@main_bp.route("/care")
def care():
    return render_template(
        "care.html",
        services=SERVICES,
        service_areas=SERVICE_AREAS,
        consultation_steps=CONSULTATION_STEPS,
    )


@main_bp.route("/brochure")
def brochure():
    return render_template("brochure.html")


def build_absolute_url(path):
    base_url = current_app.config.get("SITE_BASE_URL", "").rstrip("/")
    return f"{base_url}{path}"


def public_sitemap_urls():
    urls = []

    for endpoint in PUBLIC_STATIC_ROUTES:
        urls.append(url_for(endpoint))

    for service in SERVICES:
        urls.append(url_for("main.service_detail", slug=service["slug"]))

    for article in RESOURCE_ARTICLES:
        urls.append(url_for("main.resource_article", slug=article["slug"]))

    for page in LOCAL_SEO_PAGES:
        urls.append(url_for("main.local_seo_page", slug=page["slug"]))

    return sorted(set(urls))


@main_bp.route("/robots.txt")
def robots_txt():
    content = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /caregiver/",
        "Disallow: /dashboard",
        "Disallow: /profile",
        "Disallow: /request-care",
        f"Sitemap: {build_absolute_url('/sitemap.xml')}",
        "",
    ])
    response = make_response(content)
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    return response


@main_bp.route("/sitemap.xml")
def sitemap_xml():
    today = date.today().isoformat()
    url_entries = "\n".join([
        "  <url>\n"
        f"    <loc>{build_absolute_url(path)}</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>0.8</priority>\n"
        "  </url>"
        for path in public_sitemap_urls()
    ])
    content = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
        f"{url_entries}\n"
        "</urlset>\n"
    )
    response = make_response(content)
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    return response


@main_bp.route("/about")
def about():
    return render_template("about.html", trust_points=TRUST_POINTS, family_experience=FAMILY_EXPERIENCE)


@main_bp.route("/why-ables")
def why_ables():
    return render_template(
        "why_ables.html",
        trust_badges=TRUST_BADGES,
        family_experience=FAMILY_EXPERIENCE,
    )


@main_bp.route("/services")
def services():
    return render_template("services.html", services=SERVICES)


@main_bp.route("/services/<slug>")
def service_detail(slug):
    service = next((item for item in SERVICES if item["slug"] == slug), None)

    if not service:
        abort(404)

    return render_template("service_detail.html", service=service, services=SERVICES)


@main_bp.route("/service-areas")
def service_areas():
    return render_template("service_areas.html", service_areas=SERVICE_AREAS, local_pages=LOCAL_SEO_PAGES)


@main_bp.route("/care-process")
def care_process():
    return render_template("care_process.html", consultation_steps=CONSULTATION_STEPS)


def create_consultation_inquiry(reason="Scheduled consultation"):
    preferred_date = request.form.get("preferred_date", "").strip() or request.form.get("start_date", "").strip()
    preferred_time = request.form.get("preferred_time", "").strip()
    service_needed = request.form.get("service_needed", "").strip() or request.form.get("services_needed", "").strip()
    schedule_preference = request.form.get("schedule_preference", "").strip()
    if preferred_time:
        schedule_preference = f"Preferred time: {preferred_time}. {schedule_preference}".strip()

    inquiry = Inquiry(
        name=request.form.get("name", "").strip(),
        email=request.form.get("email", "").strip(),
        phone=request.form.get("phone", "").strip(),
        reason=reason,
        care_recipient=request.form.get("care_recipient", "").strip(),
        service_address=request.form.get("service_address", "").strip(),
        city=request.form.get("city", "").strip(),
        start_date=preferred_date,
        start_timeline=request.form.get("start_timeline", "").strip(),
        services_needed=service_needed,
        schedule_preference=schedule_preference,
        mobility_needs=request.form.get("mobility_needs", "").strip(),
        diagnosis_notes=request.form.get("diagnosis_notes", "").strip(),
        emergency_contact=request.form.get("emergency_contact", "").strip(),
        preferred_language=request.form.get("preferred_language", "").strip(),
        consent_confirmed=request.form.get("consent_confirmed") == "yes",
        message="\n".join([
            f"Preferred date: {preferred_date}",
            f"Preferred time: {preferred_time}",
            f"Service needed: {service_needed}",
            f"Person needing care: {request.form.get('care_recipient', '').strip()}",
            f"Service address: {request.form.get('service_address', '').strip()}",
            f"City: {request.form.get('city', '').strip()}",
            f"Desired start date: {request.form.get('start_date', '').strip()}",
            f"Skilled or non-skilled: {request.form.get('care_level', '').strip()}",
            f"Specific needs: {request.form.get('specific_needs', '').strip()}",
            f"Services needed: {request.form.get('services_needed', '').strip()}",
            f"Hours per week: {request.form.get('hours_per_week', '').strip()}",
            f"From what time to what time: {request.form.get('schedule_preference', '').strip()}",
            f"How they will pay: {request.form.get('payment_method', '').strip()}",
            f"Mobility needs: {request.form.get('mobility_needs', '').strip()}",
            f"Diagnosis or condition notes: {request.form.get('diagnosis_notes', '').strip()}",
            f"Emergency contact: {request.form.get('emergency_contact', '').strip()}",
            f"Preferred language: {request.form.get('preferred_language', '').strip()}",
            f"Consent confirmed: {'Yes' if request.form.get('consent_confirmed') == 'yes' else 'No'}",
            f"Start timeline: {request.form.get('start_timeline', '').strip()}",
            f"Notes: {request.form.get('message', '').strip()}",
        ]),
    )
    db.session.add(inquiry)
    db.session.commit()
    send_admin_notification(
        f"New {reason.lower()} request",
        f"Name: {inquiry.name}\nEmail: {inquiry.email}\nPhone: {inquiry.phone}\n{inquiry.message}",
        reply_to=inquiry.email,
    )
    return inquiry


@main_bp.route("/free-consultation", methods=["GET", "POST"])
def free_consultation():
    if request.method == "POST":
        create_consultation_inquiry("Free consultation")
        return redirect(url_for("main.thank_you", reason="consultation"))

    return render_template(
        "free_consultation.html",
        consultation_steps=CONSULTATION_STEPS,
        services=SERVICES,
        trust_badges=TRUST_BADGES,
        care_plan_preview=CARE_PLAN_PREVIEW,
    )


@main_bp.route("/schedule-consultation", methods=["GET", "POST"])
def schedule_consultation():
    if request.method == "POST":
        create_consultation_inquiry("Scheduled consultation")
        flash("Your consultation request was sent. Staff will follow up as soon as possible.", "success")
        return redirect(url_for("main.thank_you", reason="consultation"))

    return render_template(
        "schedule_consultation.html",
        services=SERVICES,
        service_areas=SERVICE_AREAS,
        consultation_steps=CONSULTATION_STEPS,
    )


@main_bp.route("/quick-message", methods=["POST"])
def quick_message():
    contact = request.form.get("contact", "").strip()
    inquiry = Inquiry(
        name=request.form.get("name", "").strip() or "Quick Message Visitor",
        email=contact if "@" in contact else "",
        phone=contact if "@" not in contact else "",
        reason="Quick Message",
        message=request.form.get("message", "").strip(),
    )
    if not inquiry.message:
        flash("Please enter a message before sending.", "danger")
        return redirect(request.referrer or url_for("main.contact"))

    db.session.add(inquiry)
    db.session.commit()
    send_admin_notification(
        "New quick message from website chat",
        "\n".join([
            f"Name: {inquiry.name}",
            f"Contact: {contact}",
            f"Message: {inquiry.message}",
        ]),
        reply_to=inquiry.email or None,
    )
    flash("Your quick message was sent. Staff will respond as soon as possible.", "success")
    return redirect(request.referrer or url_for("main.thank_you", reason="message"))


@main_bp.route("/portal")
def portal():
    return render_template("portal.html")


@main_bp.route("/care-standards")
def care_standards():
    return render_template("care_standards.html", standards=CARE_STANDARDS, caregiver_promises=CAREGIVER_PROMISES)


@main_bp.route("/caregivers")
def caregivers():
    return render_template(
        "caregivers.html",
        caregiver_promises=CAREGIVER_PROMISES,
        standards=CARE_STANDARDS,
    )


@main_bp.route("/pricing-payment")
def pricing_payment():
    return render_template("pricing_payment.html", payment_faqs=PAYMENT_FAQS)


@main_bp.route("/why-choose-us")
def why_choose_us():
    return render_template("why_choose_us.html", trust_points=TRUST_POINTS, testimonials=TESTIMONIALS)


@main_bp.route("/testimonials")
def testimonials():
    return render_template("testimonials.html", testimonials=TESTIMONIALS)


@main_bp.route("/care-programs")
def care_programs():
    return render_template("care_programs.html", care_programs=CARE_PROGRAMS)


@main_bp.route("/resources")
def resources():
    return render_template("resources.html", articles=RESOURCE_ARTICLES)


@main_bp.route("/launch-readiness")
def launch_readiness():
    return render_template(
        "launch_readiness.html",
        readiness_items=READINESS_ITEMS,
        launch_status=launch_status_items(),
    )


def launch_status_items():
    secret_key = current_app.config.get("SECRET_KEY", "")
    development_secret_keys = {
        "dev-secret-change-later",
        "dev-change-this-before-production",
    }
    placeholder_markers = (
        "REPLACE_",
        "replace-with",
        "example.com",
        "user:password",
    )

    def configured(value):
        text = str(value or "")
        return bool(text) and not any(marker in text for marker in placeholder_markers)

    database_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    site_base_url = current_app.config.get("SITE_BASE_URL", "")
    mail_server = current_app.config.get("MAIL_SERVER", "")
    mail_username = current_app.config.get("MAIL_USERNAME", "")
    mail_password = current_app.config.get("MAIL_PASSWORD", "")
    admin_email = current_app.config.get("ADMIN_EMAIL", "")
    is_production = current_app.config.get("SESSION_COOKIE_SECURE") is True
    business_confirmed = current_app.config.get("BUSINESS_DETAILS_CONFIRMED") is True
    legal_scope_confirmed = current_app.config.get("LEGAL_SCOPE_CONFIRMED") is True
    approved_photos_ready = current_app.config.get("APPROVED_PHOTOS_READY") is True

    return [
        {
            "label": "Production mode",
            "status": "Ready" if is_production else "Needs setup",
            "body": "Secure cookie settings are active." if is_production else "Set FLASK_ENV=production in hosting so secure cookie settings activate.",
            "ready": is_production,
        },
        {
            "label": "Secret key",
            "status": "Ready" if configured(secret_key) and secret_key not in development_secret_keys else "Needs setup",
            "body": "A custom SECRET_KEY is configured." if configured(secret_key) and secret_key not in development_secret_keys else "Replace the development SECRET_KEY with a long random production value.",
            "ready": bool(configured(secret_key) and secret_key not in development_secret_keys),
        },
        {
            "label": "Public site URL",
            "status": "Ready" if site_base_url.startswith("https://") else "Needs setup",
            "body": f"SITE_BASE_URL is set to {site_base_url}." if site_base_url.startswith("https://") else "Set SITE_BASE_URL to the final HTTPS domain for sitemap and search engine signals.",
            "ready": site_base_url.startswith("https://"),
        },
        {
            "label": "Production database",
            "status": "Ready" if configured(database_url) and not database_url.startswith("sqlite:///") else "Needs setup",
            "body": "A non-SQLite production database is configured." if configured(database_url) and not database_url.startswith("sqlite:///") else "Use PostgreSQL or another managed production database before collecting real client data.",
            "ready": bool(configured(database_url) and not database_url.startswith("sqlite:///")),
        },
        {
            "label": "Email notifications",
            "status": "Ready" if configured(mail_server) and configured(mail_username) and configured(mail_password) and configured(admin_email) else "Needs setup",
            "body": "SMTP and ADMIN_EMAIL are configured for office notifications." if configured(mail_server) and configured(mail_username) and configured(mail_password) and configured(admin_email) else "Configure real MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD, and ADMIN_EMAIL so forms notify the office.",
            "ready": bool(configured(mail_server) and configured(mail_username) and configured(mail_password) and configured(admin_email)),
        },
        {
            "label": "Business details",
            "status": "Confirmed" if business_confirmed else "Needs owner review",
            "body": "Business facts have been confirmed by the owner." if business_confirmed else "Confirm business name, address, phone, hours, administrator, payment options, and service areas.",
            "ready": business_confirmed,
        },
        {
            "label": "Service scope",
            "status": "Confirmed" if legal_scope_confirmed else "Needs review",
            "body": "Service and licensing language has been confirmed." if legal_scope_confirmed else "Confirm whether the company is non-medical only or licensed for any clinical/skilled services.",
            "ready": legal_scope_confirmed,
        },
        {
            "label": "Approved photos",
            "status": "Ready" if approved_photos_ready else "Needs replacement",
            "body": "Approved business or licensed photos are ready." if approved_photos_ready else "Replace development images with approved business photos or licensed stock before launch.",
            "ready": approved_photos_ready,
        },
    ]


def seo_checklist_items():
    endpoints = {rule.endpoint for rule in current_app.url_map.iter_rules()}
    public_urls = public_sitemap_urls()
    has_metadata = True
    analytics_id = current_app.config.get("GOOGLE_ANALYTICS_ID", "")
    search_console = current_app.config.get("GOOGLE_SITE_VERIFICATION", "")
    return [
        {
            "label": "Sitemap XML",
            "ready": "main.sitemap_xml" in endpoints,
            "detail": f"{len(public_urls)} public URLs are included in /sitemap.xml.",
            "href": url_for("main.sitemap_xml"),
        },
        {
            "label": "Robots TXT",
            "ready": "main.robots_txt" in endpoints,
            "detail": "robots.txt allows public pages and blocks private/admin routes.",
            "href": url_for("main.robots_txt"),
        },
        {
            "label": "Metadata and Open Graph",
            "ready": has_metadata,
            "detail": "Base templates include title, description, canonical URL, Open Graph tags, and LocalBusiness schema.",
            "href": url_for("main.home"),
        },
        {
            "label": "Google Analytics",
            "ready": bool(analytics_id),
            "detail": "GOOGLE_ANALYTICS_ID is configured." if analytics_id else "Ready for integration. Add GOOGLE_ANALYTICS_ID in hosting environment variables.",
            "href": url_for("main.admin_system_status"),
        },
        {
            "label": "Google Search Console",
            "ready": bool(search_console),
            "detail": "GOOGLE_SITE_VERIFICATION is configured." if search_console else "Ready for integration. Add GOOGLE_SITE_VERIFICATION after verifying the domain in Google Search Console.",
            "href": url_for("main.admin_system_status"),
        },
        {
            "label": "Local SEO Service Areas",
            "ready": "main.service_areas" in endpoints and len(LOCAL_SEO_PAGES) > 0,
            "detail": f"{len(SERVICE_AREAS)} service areas and {len(LOCAL_SEO_PAGES)} local SEO pages are available.",
            "href": url_for("main.service_areas"),
        },
        {
            "label": "SEO-Friendly URLs",
            "ready": bool(public_urls),
            "detail": "Public pages use readable URLs for services, resources, service areas, scheduling, and portal access.",
            "href": url_for("main.admin_seo_checklist"),
        },
        {
            "label": "Directory Listing Readiness",
            "ready": current_app.config.get("SITE_BASE_URL", "").startswith("https://"),
            "detail": "NAP details are consistent: Ables Home Healthcare, 301-455-3030, 11510 Georgia Avenue Suite 230 Silver Spring MD 20902.",
            "href": url_for("main.contact"),
        },
    ]


@main_bp.route("/admin/seo-checklist")
@login_required
def admin_seo_checklist():
    require_permission("admin:reports")
    items = seo_checklist_items()
    return render_template(
        "admin/seo_checklist.html",
        items=items,
        ready_count=sum(1 for item in items if item["ready"]),
        user=current_user,
    )


@main_bp.route("/resources/<slug>")
def resource_article(slug):
    article = next((item for item in RESOURCE_ARTICLES if item["slug"] == slug), None)

    if not article:
        abort(404)

    return render_template("resource_article.html", article=article, articles=RESOURCE_ARTICLES)


@main_bp.route("/<slug>")
def local_seo_page(slug):
    page = next((item for item in LOCAL_SEO_PAGES if item["slug"] == slug), None)

    if not page:
        abort(404)

    return render_template(
        "local_seo.html",
        page=page,
        services=SERVICES,
        consultation_steps=CONSULTATION_STEPS,
    )


@main_bp.route("/referrals", methods=["GET", "POST"])
def referrals():
    if request.method == "POST":
        referral = Referral(
            referral_source=request.form.get("referral_source", "").strip(),
            source_phone=request.form.get("source_phone", "").strip(),
            client_name=request.form.get("client_name", "").strip(),
            client_phone=request.form.get("client_phone", "").strip(),
            care_needed=request.form.get("care_needed", "").strip(),
            follow_up=request.form.get("follow_up", "").strip(),
            notes=request.form.get("notes", "").strip(),
        )

        db.session.add(referral)
        db.session.commit()

        send_admin_notification(
            "New Ables Home Healthcare referral",
            "\n".join([
                f"Referral source: {referral.referral_source}",
                f"Source phone: {referral.source_phone}",
                f"Client name: {referral.client_name}",
                f"Client phone: {referral.client_phone}",
                f"Care needed: {referral.care_needed}",
                f"Preferred follow-up: {referral.follow_up}",
                f"Notes: {referral.notes}",
            ]),
        )

        return redirect(url_for("main.thank_you", reason="referral"))

    return render_template("referrals.html")


@main_bp.route("/family-guide")
def family_guide():
    return render_template(
        "family_guide.html",
        family_guide_items=FAMILY_GUIDE_ITEMS,
        services=SERVICES,
        consultation_steps=CONSULTATION_STEPS,
        care_plan_preview=CARE_PLAN_PREVIEW,
    )


@main_bp.route("/provider-partners")
def provider_partners():
    return render_template(
        "provider_partners.html",
        partner_types=PARTNER_TYPES,
        partner_promises=PARTNER_PROMISES,
        consultation_steps=CONSULTATION_STEPS,
    )


@main_bp.route("/administrator")
def administrator():
    return render_template("administrator.html")


@main_bp.route("/careers")
def careers():
    return render_template("careers.html")


@main_bp.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        inquiry = Inquiry(
            name=request.form.get("name", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            reason=request.form.get("reason", "").strip(),
            message=request.form.get("message", "").strip(),
        )

        db.session.add(inquiry)
        db.session.commit()

        send_admin_notification(
            "New Ables Home Healthcare website inquiry",
            "\n".join([
                f"Name: {inquiry.name}",
                f"Email: {inquiry.email}",
                f"Phone: {inquiry.phone}",
                f"Reason: {inquiry.reason}",
                f"Message: {inquiry.message}",
            ]),
            reply_to=inquiry.email,
        )

        return redirect(url_for("main.thank_you", reason="contact"))

    return render_template("contact.html")


@main_bp.route("/thank-you")
def thank_you():
    reason = request.args.get("reason", "message")
    return render_template("thank_you.html", reason=reason)


@main_bp.route("/employment-application", methods=["GET", "POST"])
def employment_application():
    if request.method == "POST":
        credential_type = request.form.get("credential_type", "").strip().upper()
        if credential_type not in {"CNA", "LPN", "RN"}:
            send_admin_notification(
                "Caregiver applicant asked to call office",
                "\n".join([
                    f"Name: {request.form.get('name', '').strip()}",
                    f"Email: {request.form.get('email', '').strip()}",
                    f"Phone: {request.form.get('phone', '').strip()}",
                    f"Credential response: {request.form.get('credential_type', '').strip()}",
                    "Applicant was not allowed to submit the full online caregiver application and was told to call the office.",
                ]),
                reply_to=request.form.get("email", "").strip(),
            )
            flash("This application is for CNA, LPN, and RN caregivers right now. Please call the office at 301-455-3030.", "warning")
            return render_template("employment_application.html", blocked_application=True)

        resume_filename = save_application_upload(request.files.get("resume"))
        requirements = request.form.getlist("requirements")
        requirements_summary = "\n".join(f"- {item}" for item in requirements) if requirements else "No requirements checked yet"
        application = CaregiverApplication(
            name=request.form.get("name", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            address=request.form.get("address", "").strip(),
            availability=request.form.get("availability", "").strip(),
            weekday_availability=request.form.get("weekday_availability", "").strip(),
            weekend_availability=request.form.get("weekend_availability", "").strip(),
            certifications=f"{credential_type} | {request.form.get('certifications', '').strip()}",
            years_experience=request.form.get("years_experience", "").strip(),
            transportation=request.form.get("transportation", "").strip(),
            preferred_service_areas=request.form.get("preferred_service_areas", "").strip(),
            applicant_references=request.form.get("applicant_references", "").strip(),
            background_check_consent=request.form.get("background_check_consent") == "yes",
            requirements_checklist=requirements_summary,
            resume_filename=resume_filename,
            experience=request.form.get("experience", "").strip(),
        )
        inquiry = Inquiry(
            name=request.form.get("name", "").strip(),
            email=request.form.get("email", "").strip(),
            phone=request.form.get("phone", "").strip(),
            reason="Caregiver application",
            message="\n".join([
                f"Experience: {request.form.get('experience', '').strip()}",
                f"Availability: {request.form.get('availability', '').strip()}",
                f"Weekday availability: {request.form.get('weekday_availability', '').strip()}",
                f"Weekend availability: {request.form.get('weekend_availability', '').strip()}",
                f"Credential type: {credential_type}",
                f"Certifications: {request.form.get('certifications', '').strip()}",
                f"Years experience: {request.form.get('years_experience', '').strip()}",
                f"Transportation: {request.form.get('transportation', '').strip()}",
                f"Preferred service areas: {request.form.get('preferred_service_areas', '').strip()}",
                f"References: {request.form.get('applicant_references', '').strip()}",
                f"Requirements checked:\n{requirements_summary}",
                f"Background check consent: {'Yes' if request.form.get('background_check_consent') == 'yes' else 'No'}",
                f"Resume file: {resume_filename or 'Not uploaded'}",
            ]),
        )
        db.session.add(application)
        db.session.add(inquiry)
        db.session.commit()

        send_admin_notification(
            "New caregiver employment application",
            f"Applicant: {inquiry.name}\nEmail: {inquiry.email}\nPhone: {inquiry.phone}\n{inquiry.message}",
            reply_to=inquiry.email,
        )

        return redirect(url_for("main.thank_you", reason="application"))

    return render_template("employment_application.html")


def save_application_upload(file_storage):
    if not file_storage or not file_storage.filename:
        return ""

    stored_name, original_filename = save_private_upload(
        file_storage,
        "applications",
        "caregiver-application",
        allowed_resume,
    )
    if not stored_name:
        flash("Resume upload must be a PDF, DOC, DOCX, or TXT file.", "danger")
        return ""

    audit("upload_caregiver_resume", "CaregiverApplication", None, original_filename)
    return stored_name


@main_bp.route("/download-referral-form")
def download_referral_form():
    content = "\n".join([
        "ABLES HOME HEALTHCARE REFERRAL FORM",
        "",
        "Referral Source Name:",
        "Referral Source Phone:",
        "Referral Source Email:",
        "",
        "Client Name:",
        "Client Phone:",
        "Client Address:",
        "",
        "Care Needed:",
        "Preferred Follow-Up:",
        "",
        "Notes:",
        "",
        "Submit to: admin@ableshomehealthcare.com",
        "Phone: (301) 455-3030",
    ])
    response = make_response(content)
    response.headers["Content-Type"] = "text/plain"
    response.headers["Content-Disposition"] = "attachment; filename=ables-referral-form.txt"
    return response


@main_bp.route("/faq")
def faq():
    return render_template("faq.html")


@main_bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@main_bp.route("/terms")
def terms():
    return render_template("terms.html")


@main_bp.route("/healthz")
def healthz():
    checks = {}
    database_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    checks["database_backend"] = "postgresql" if database_uri.startswith("postgresql") else "sqlite" if database_uri.startswith("sqlite") else "other"
    try:
        db.session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        current_app.logger.exception("Health check database failure")
        checks["database"] = f"error: {exc}"

    try:
        db.session.execute(text("SELECT COUNT(*) FROM \"user\""))
        db.session.execute(text("SELECT COUNT(*) FROM inquiry"))
        db.session.execute(text("SELECT COUNT(*) FROM caregiver_application"))
        db.session.execute(text("SELECT COUNT(*) FROM notification_log"))
        checks["database_schema"] = "ok"
    except Exception as exc:
        current_app.logger.exception("Health check database schema failure")
        checks["database_schema"] = f"error: {exc}"

    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    try:
        upload_folder.mkdir(parents=True, exist_ok=True)
        probe = upload_folder / ".healthz"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["uploads"] = "ok"
    except Exception as exc:
        current_app.logger.exception("Health check upload folder failure")
        checks["uploads"] = f"error: {exc}"

    checks["site_base_url"] = "ok" if current_app.config["SITE_BASE_URL"].startswith("https://") or current_app.config["SITE_BASE_URL"].startswith("http://127.0.0.1") else "warning"
    mail_keys = {
        "MAIL_SERVER": current_app.config.get("MAIL_SERVER"),
        "MAIL_USERNAME": current_app.config.get("MAIL_USERNAME"),
        "MAIL_PASSWORD": current_app.config.get("MAIL_PASSWORD"),
        "MAIL_DEFAULT_SENDER": current_app.config.get("MAIL_DEFAULT_SENDER"),
        "ADMIN_EMAIL": current_app.config.get("ADMIN_EMAIL"),
    }
    checks["smtp"] = "ok" if all(mail_keys.values()) else "not_configured"
    checks["smtp_keys"] = {
        key: "present" if value else "missing"
        for key, value in mail_keys.items()
    }
    try:
        latest_email = NotificationLog.query.filter_by(channel="email").order_by(NotificationLog.created_at.desc()).first()
        checks["email_delivery"] = {
            "sent": NotificationLog.query.filter_by(channel="email", status="sent").count(),
            "failed": NotificationLog.query.filter_by(channel="email", status="failed").count(),
            "skipped": NotificationLog.query.filter_by(channel="email", status="skipped").count(),
            "latest_status": latest_email.status if latest_email else "none",
            "latest_recipient": mask_email_address(latest_email.recipient) if latest_email else "",
            "latest_error": short_status_error(latest_email.error) if latest_email else "",
        }
    except Exception as exc:
        current_app.logger.exception("Health check email delivery summary failure")
        checks["email_delivery"] = {"error": short_status_error(exc)}
    checks["production_mode"] = "ok" if current_app.config.get("SESSION_COOKIE_SECURE") else "development"

    ok = checks["database"] == "ok" and checks["database_schema"] == "ok" and checks["uploads"] == "ok"
    return jsonify({"status": "ok" if ok else "error", "checks": checks}), 200 if ok else 503


def health_status_data():
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    try:
        db.session.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        database_ok = False

    return {
        "database_ok": database_ok,
        "postgresql_configured": not current_app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"),
        "smtp_configured": bool(current_app.config.get("MAIL_SERVER")),
        "virus_scanner_configured": bool(current_app.config.get("VIRUS_SCAN_COMMAND")),
        "upload_folder": str(upload_folder),
        "upload_folder_exists": upload_folder.exists(),
        "site_base_url": current_app.config["SITE_BASE_URL"],
        "production_mode": bool(current_app.config.get("SESSION_COOKIE_SECURE")),
    }


# ---------------- DASHBOARD ----------------
@main_bp.route("/dashboard")
@login_required
def dashboard():

    if current_user.role == "admin":
        email_ready = bool(app_config_value("MAIL_SERVER") and app_config_value("ADMIN_EMAIL"))
        stats = {
            "users": User.query.count(),
            "appointments": Appointment.query.count(),
            "pending": Appointment.query.filter_by(status="pending").count(),
            "inquiries": Inquiry.query.count(),
            "referrals": Referral.query.count(),
            "care_plans": CarePlan.query.count(),
            "applications": CaregiverApplication.query.count(),
            "messages": PortalMessage.query.filter(PortalMessage.status.in_(["new", "in review"])).count(),
            "open_work": (
                Inquiry.query.filter(Inquiry.status.in_(["new", "contacted"])).count()
                + Referral.query.filter(Referral.status.in_(["new", "contacted"])).count()
                + CarePlan.query.filter(CarePlan.status.in_(["draft", "reviewing", "ready for caregiver match"])).count()
                + Appointment.query.filter(Appointment.status.in_(["pending", "approved"])).count()
                + CaregiverApplication.query.filter(CaregiverApplication.status.in_(["new", "screening", "interview scheduled", "references pending"])).count()
                + PortalMessage.query.filter(PortalMessage.status.in_(["new", "in review"])).count()
            ),
        }
        recent_inquiries = Inquiry.query.order_by(Inquiry.created_at.desc()).limit(5).all()
        recent_applications = CaregiverApplication.query.order_by(CaregiverApplication.created_at.desc()).limit(5).all()
        recent_users = User.query.order_by(User.created_at.desc()).limit(6).all()
        return render_template(
            "admin/dashboard.html",
            user=current_user,
            stats=stats,
            recent_inquiries=recent_inquiries,
            recent_applications=recent_applications,
            recent_users=recent_users,
            email_ready=email_ready,
            readiness_items=READINESS_ITEMS,
        )

    elif current_user.role == "caregiver":
        today_iso = date.today().isoformat()
        assigned_appointments = Appointment.query.filter_by(
            caregiver_id=current_user.id
        ).order_by(Appointment.date.asc(), Appointment.time.asc()).all()
        active_appointments = [
            item for item in assigned_appointments
            if item.status not in ["completed", "cancelled"]
        ]
        today_appointments = [
            item for item in active_appointments
            if item.date == today_iso
        ]
        upcoming_appointments = [
            item for item in active_appointments
            if (item.date or "") >= today_iso
        ]
        caregiver_stats = {
            "today": len(today_appointments),
            "upcoming": len(upcoming_appointments),
            "completed": len([item for item in assigned_appointments if item.status == "completed"]),
            "needs_notes": len([
                item for item in active_appointments
                if not (item.caregiver_notes or "").strip()
            ]),
        }
        return render_template(
            "caregiver/dashboard.html",
            user=current_user,
            appointments=assigned_appointments,
            today_appointments=today_appointments,
            upcoming_appointments=upcoming_appointments[:6],
            caregiver_stats=caregiver_stats,
            reminders=appointment_reminders(assigned_appointments),
        )

    today_iso = date.today().isoformat()
    appointments = Appointment.query.filter_by(
        client_id=current_user.id
    ).order_by(Appointment.date.asc(), Appointment.time.asc()).all()
    upcoming_appointments = [
        item for item in appointments
        if (item.date or "") >= today_iso and item.status not in ["completed", "cancelled", "rejected"]
    ]
    next_appointment = upcoming_appointments[0] if upcoming_appointments else None
    profile_fields = [
        current_user.phone,
        current_user.address,
        current_user.emergency_contact_name,
        current_user.emergency_contact_phone,
        current_user.care_needs,
        current_user.preferred_schedule,
    ]
    client_stats = {
        "upcoming": len(upcoming_appointments),
        "pending": len([item for item in appointments if item.status == "pending"]),
        "completed": len([item for item in appointments if item.status == "completed"]),
        "profile_missing": len([value for value in profile_fields if not value]),
    }

    return render_template(
        "client/dashboard.html",
        user=current_user,
        appointments=appointments,
        upcoming_appointments=upcoming_appointments[:5],
        next_appointment=next_appointment,
        client_stats=client_stats,
        reminders=appointment_reminders(appointments),
    )


@main_bp.route("/admin/security")
@login_required
def admin_security():
    require_permission("admin:security")

    locked_users = User.query.filter(User.locked_until.isnot(None)).order_by(User.locked_until.desc()).all()
    recent_events = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(80).all()
    failed_users = User.query.filter(User.failed_login_count > 0).order_by(User.failed_login_count.desc()).all()
    active_recovery_codes = AdminRecoveryCode.query.filter_by(admin_id=current_user.id, used_at=None).count()
    used_recovery_codes = AdminRecoveryCode.query.filter(
        AdminRecoveryCode.admin_id == current_user.id,
        AdminRecoveryCode.used_at.isnot(None),
    ).count()

    return render_template(
        "admin/security.html",
        locked_users=locked_users,
        recent_events=recent_events,
        failed_users=failed_users,
        active_recovery_codes=active_recovery_codes,
        used_recovery_codes=used_recovery_codes,
        recovery_codes_required=current_app.config.get("ADMIN_RECOVERY_CODES_REQUIRED"),
        new_recovery_codes=None,
        user=current_user,
    )


@main_bp.route("/admin/security/recovery-codes", methods=["POST"])
@login_required
def admin_generate_recovery_codes():
    require_permission("admin:security")
    codes = generate_recovery_codes(current_user)
    db.session.commit()
    flash("New admin recovery codes generated. Save them now; they will not be shown again.", "success")
    locked_users = User.query.filter(User.locked_until.isnot(None)).order_by(User.locked_until.desc()).all()
    recent_events = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(80).all()
    failed_users = User.query.filter(User.failed_login_count > 0).order_by(User.failed_login_count.desc()).all()
    return render_template(
        "admin/security.html",
        locked_users=locked_users,
        recent_events=recent_events,
        failed_users=failed_users,
        active_recovery_codes=len(codes),
        used_recovery_codes=0,
        recovery_codes_required=current_app.config.get("ADMIN_RECOVERY_CODES_REQUIRED"),
        new_recovery_codes=codes,
        user=current_user,
    )


@main_bp.route("/admin/security/users/<int:user_id>/unlock", methods=["POST"])
@login_required
def admin_unlock_user(user_id):
    require_permission("admin:security")

    account = get_record_or_404(User, user_id)
    account.failed_login_count = 0
    account.locked_until = None
    audit("unlock_user", "User", account.id, f"Unlocked {account.email}")
    db.session.commit()

    flash("Account lockout reset.", "success")
    return redirect(url_for("main.admin_security"))


def filtered_audit_query():
    query = AuditLog.query
    action = request.args.get("action", "").strip()
    user_email = request.args.get("user", "").strip()
    entity_type = request.args.get("entity_type", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))
    if entity_type:
        query = query.filter(AuditLog.entity_type.ilike(f"%{entity_type}%"))
    if user_email:
        query = query.join(User, AuditLog.user_id == User.id).filter(User.email.ilike(f"%{user_email}%"))
    if date_from:
        query = query.filter(AuditLog.created_at >= date_from)
    if date_to:
        query = query.filter(AuditLog.created_at <= f"{date_to} 23:59:59")

    return query.order_by(AuditLog.created_at.desc())


@main_bp.route("/admin/audit")
@login_required
def admin_audit():
    require_permission("admin:audit")
    events = filtered_audit_query().limit(500).all()
    return render_template("admin/audit.html", events=events, user=current_user)


@main_bp.route("/admin/audit/export")
@login_required
def admin_audit_export():
    require_permission("admin:audit")
    audit("export_filtered_audit", "AuditLog", None, str(dict(request.args)))
    rows = [
        [
            item.id,
            item.user.email if item.user else "",
            item.action,
            item.entity_type,
            item.entity_id,
            item.details,
            short_date(item.created_at),
        ]
        for item in filtered_audit_query().limit(5000).all()
    ]
    return csv_response(
        "ables-filtered-audit.csv",
        ["id", "user", "action", "entity_type", "entity_id", "details", "created_at"],
        rows,
    )


def contains_query(*values, query):
    combined = " ".join(str(value or "") for value in values).lower()
    return query in combined


MESSAGE_TOPICS = [
    "Scheduling",
    "Care plan question",
    "Billing or payment",
    "Caregiver update",
    "Client update",
    "Safety concern",
    "General question",
]

MESSAGE_STATUSES = [
    "new",
    "in review",
    "replied",
    "closed",
]


@main_bp.route("/messages", methods=["GET", "POST"])
@login_required
def portal_messages():
    if current_user.role == "admin":
        return redirect(url_for("main.admin_messages"))

    if request.method == "POST":
        topic = request.form.get("topic", "").strip()
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        priority = request.form.get("priority", "normal").strip()

        if topic not in MESSAGE_TOPICS:
            flash("Choose a message topic.", "danger")
            return redirect(url_for("main.portal_messages"))

        if not subject or not body:
            flash("Add a subject and message before sending.", "danger")
            return redirect(url_for("main.portal_messages"))

        message = PortalMessage(
            sender_id=current_user.id,
            audience=current_user.role,
            topic=topic,
            subject=subject[:160],
            body=body,
            priority=priority if priority in ["normal", "high", "urgent"] else "normal",
            status="new",
            admin_seen=False,
            sender_seen_reply=True,
        )
        db.session.add(message)
        audit("create_portal_message", "PortalMessage", None, f"{current_user.email}: {topic}")
        db.session.commit()
        send_admin_notification(
            f"Portal message: {message.subject}",
            "\n".join([
                f"From: {current_user.name} ({current_user.email})",
                f"Role: {current_user.role}",
                f"Topic: {message.topic}",
                f"Priority: {message.priority}",
                "",
                message.body,
            ]),
            reply_to=current_user.email,
        )

        flash("Message sent to the office. The team can review it from the admin inbox.", "success")
        return redirect(url_for("main.portal_messages"))

    messages = PortalMessage.query.filter_by(
        sender_id=current_user.id
    ).order_by(PortalMessage.updated_at.desc()).all()
    for message in messages:
        if message.sender_seen_reply is False:
            message.sender_seen_reply = True
    db.session.commit()

    return render_template(
        "messages.html",
        messages=messages,
        message_topics=MESSAGE_TOPICS,
        user=current_user,
    )


@main_bp.route("/admin/messages")
@login_required
def admin_messages():
    require_role("admin")

    status = request.args.get("status", "open")
    query = request.args.get("q", "").strip().lower()
    messages_query = PortalMessage.query

    if status == "open":
        messages_query = messages_query.filter(PortalMessage.status.in_(["new", "in review"]))
    elif status in MESSAGE_STATUSES:
        messages_query = messages_query.filter_by(status=status)

    messages = messages_query.order_by(PortalMessage.updated_at.desc()).all()
    if query:
        messages = [
            item for item in messages
            if contains_query(
                item.sender.name if item.sender else "",
                item.sender.email if item.sender else "",
                item.topic,
                item.subject,
                item.body,
                item.admin_reply,
                item.status,
                query=query,
            )
        ]
    for message in messages:
        if message.admin_seen is False:
            message.admin_seen = True
    db.session.commit()

    return render_template(
        "admin/messages.html",
        messages=messages,
        message_statuses=MESSAGE_STATUSES,
        current_status=status,
        query=query,
        user=current_user,
    )


@main_bp.route("/admin/messages/<int:message_id>/reply", methods=["POST"])
@login_required
def admin_reply_message(message_id):
    require_role("admin")

    message = get_record_or_404(PortalMessage, message_id)
    reply = request.form.get("admin_reply", "").strip()
    status = request.form.get("status", "replied").strip()

    if status not in MESSAGE_STATUSES:
        status = "replied"

    if status == "replied" and not reply:
        flash("Write a reply before marking the message replied.", "danger")
        return redirect(url_for("main.admin_messages"))

    message.admin_reply = reply or message.admin_reply
    message.status = status
    message.replied_by_id = current_user.id
    if reply:
        message.sender_seen_reply = False
    audit("reply_portal_message", "PortalMessage", message.id, f"Status: {status}")
    db.session.commit()
    if reply and message.sender and message.sender.email:
        send_email_notification(
            message.sender.email,
            f"Ables portal reply: {message.subject}",
            "\n".join([
                f"Hello {message.sender.name},",
                "",
                "The Ables Home Healthcare office replied to your portal message.",
                "",
                reply,
                "",
                "Sign in to the portal to review your message history.",
            ]),
        )

    flash("Portal message updated.", "success")
    return redirect(url_for("main.admin_messages"))


@main_bp.route("/admin/search")
@login_required
def admin_search():
    require_role("admin")

    query = request.args.get("q", "").strip().lower()
    results = {
        "users": [],
        "inquiries": [],
        "referrals": [],
        "applications": [],
        "care_plans": [],
        "appointments": [],
        "messages": [],
        "documents": [],
        "admin_notes": [],
    }

    if query:
        results["users"] = [
            item for item in User.query.order_by(User.created_at.desc()).limit(250).all()
            if contains_query(item.name, item.email, item.phone, item.role, query=query)
        ][:25]
        results["inquiries"] = [
            item for item in Inquiry.query.order_by(Inquiry.created_at.desc()).limit(250).all()
            if contains_query(item.name, item.email, item.phone, item.reason, item.message, item.status, query=query)
        ][:25]
        results["referrals"] = [
            item for item in Referral.query.order_by(Referral.created_at.desc()).limit(250).all()
            if contains_query(item.referral_source, item.client_name, item.client_phone, item.care_needed, item.status, query=query)
        ][:25]
        results["applications"] = [
            item for item in CaregiverApplication.query.order_by(CaregiverApplication.created_at.desc()).limit(250).all()
            if contains_query(item.name, item.email, item.phone, item.availability, item.certifications, item.status, query=query)
        ][:25]
        results["care_plans"] = [
            item for item in CarePlan.query.order_by(CarePlan.updated_at.desc()).limit(250).all()
            if contains_query(item.client_name, item.client_phone, item.service_type, item.schedule, item.status, query=query)
        ][:25]
        results["appointments"] = [
            item for item in Appointment.query.order_by(Appointment.created_at.desc()).limit(250).all()
            if contains_query(item.client.name if item.client else "", item.service_type, item.service_address, item.notes, item.status, query=query)
        ][:25]
        results["messages"] = [
            item for item in PortalMessage.query.order_by(PortalMessage.updated_at.desc()).limit(250).all()
            if contains_query(item.sender.name if item.sender else "", item.sender.email if item.sender else "", item.topic, item.subject, item.body, item.status, query=query)
        ][:25]
        results["documents"] = [
            item for item in ClientDocument.query.order_by(ClientDocument.created_at.desc()).limit(250).all()
            if contains_query(item.owner.name if item.owner else "", item.owner.email if item.owner else "", item.title, item.category, item.original_filename, item.notes, query=query)
        ][:25]
        results["admin_notes"] = [
            item for item in AdminNote.query.order_by(AdminNote.created_at.desc()).limit(250).all()
            if contains_query(item.subject_user.name if item.subject_user else "", item.subject_user.email if item.subject_user else "", item.note, query=query)
        ][:25]

    total_results = sum(len(items) for items in results.values())
    return render_template(
        "admin/search.html",
        query=query,
        results=results,
        total_results=total_results,
        user=current_user,
    )


@main_bp.route("/admin/work-queue")
@login_required
def admin_work_queue():
    require_role("admin")

    queues = {
        "new_inquiries": Inquiry.query.filter(Inquiry.status.in_(["new", "contacted"])).order_by(Inquiry.created_at.desc()).all(),
        "new_referrals": Referral.query.filter(Referral.status.in_(["new", "contacted"])).order_by(Referral.created_at.desc()).all(),
        "care_plans": CarePlan.query.filter(CarePlan.status.in_(["draft", "reviewing", "ready for caregiver match"])).order_by(CarePlan.updated_at.desc()).all(),
        "pending_appointments": Appointment.query.filter(Appointment.status.in_(["pending", "approved"])).order_by(Appointment.created_at.desc()).all(),
        "applications": CaregiverApplication.query.filter(CaregiverApplication.status.in_(["new", "screening", "interview scheduled", "references pending"])).order_by(CaregiverApplication.created_at.desc()).all(),
        "messages": PortalMessage.query.filter(PortalMessage.status.in_(["new", "in review"])).order_by(PortalMessage.updated_at.desc()).all(),
        "urgent_appointments": Appointment.query.filter_by(urgent_flag=True).filter(Appointment.status.notin_(["completed", "cancelled", "rejected"])).order_by(Appointment.created_at.desc()).all(),
    }
    reminders = appointment_reminders(Appointment.query.order_by(Appointment.date.asc(), Appointment.time.asc()).all())

    total_open = sum(len(items) for items in queues.values())
    return render_template(
        "admin/work_queue.html",
        queues=queues,
        reminders=reminders,
        total_open=total_open,
        user=current_user,
    )


@main_bp.route("/admin/system-status")
@login_required
def admin_system_status():
    require_permission("admin:reports")
    failed_notifications = NotificationLog.query.filter_by(status="failed").order_by(NotificationLog.created_at.desc()).limit(10).all()
    failed_logins = AuditLog.query.filter(AuditLog.action.in_(["login_failure", "admin_recovery_code_failure", "account_locked"])).order_by(AuditLog.created_at.desc()).limit(10).all()
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = [path for path in BACKUP_DIR.iterdir() if path.is_file()]
    log_path = Path(current_app.instance_path) / "logs" / "app.log"
    migration_version = current_migration_revision() or "Unavailable"
    latest_revision = latest_migration_revision()
    return render_template(
        "admin/system_status.html",
        status=health_status_data(),
        backup_count=len(backups),
        latest_backup=max(backups, key=lambda path: path.stat().st_mtime) if backups else None,
        migration_version=migration_version,
        latest_revision=latest_revision,
        migration_current=bool(migration_version and latest_revision and migration_version == latest_revision),
        log_path=log_path,
        log_size=log_path.stat().st_size if log_path.exists() else 0,
        failed_notifications=failed_notifications,
        failed_logins=failed_logins,
        user=current_user,
    )


@main_bp.route("/admin/launch-checklist")
@login_required
def admin_launch_checklist():
    require_permission("admin:reports")
    health = health_status_data()
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = [path for path in BACKUP_DIR.iterdir() if path.is_file()]
    test_inquiries = Inquiry.query.filter(
        or_(
            Inquiry.email.ilike("%test%"),
            Inquiry.name.ilike("%test%"),
            Inquiry.message.ilike("%test%"),
        )
    ).count()
    test_applications = CaregiverApplication.query.filter(
        or_(
            CaregiverApplication.email.ilike("%test%"),
            CaregiverApplication.name.ilike("%test%"),
            CaregiverApplication.experience.ilike("%test%"),
        )
    ).count()
    checklist = [
        {
            "label": "Test records cleaned",
            "ready": test_inquiries == 0 and test_applications == 0,
            "detail": f"{test_inquiries} possible test inquiry record(s), {test_applications} possible test caregiver application record(s).",
            "href": url_for("main.admin_inquiries"),
        },
        {
            "label": "SMTP tested",
            "ready": health["checks"].get("smtp") == "ok",
            "detail": "Email settings are configured." if health["checks"].get("smtp") == "ok" else "Configure and test SMTP before launch.",
            "href": url_for("main.admin_system_status"),
        },
        {
            "label": "PostgreSQL active",
            "ready": health["checks"].get("database_backend") == "postgresql",
            "detail": f"Database backend: {health['checks'].get('database_backend', 'unknown')}.",
            "href": url_for("main.admin_system_status"),
        },
        {
            "label": "Backups confirmed",
            "ready": len(backups) > 0,
            "detail": f"{len(backups)} local backup file(s). Also verify Render PostgreSQL backups in Render.",
            "href": url_for("main.admin_backups"),
        },
        {
            "label": "Privacy reviewed",
            "ready": current_app.config.get("LEGAL_PRIVACY_REVIEW_CONFIRMED") is True,
            "detail": "Set LEGAL_PRIVACY_REVIEW_CONFIRMED=true after owner/legal review.",
            "href": url_for("main.privacy"),
        },
        {
            "label": "Domain connected",
            "ready": current_app.config.get("SITE_BASE_URL", "").startswith("https://") and "onrender.com" not in current_app.config.get("SITE_BASE_URL", ""),
            "detail": current_app.config.get("SITE_BASE_URL", "SITE_BASE_URL not set."),
            "href": url_for("main.admin_system_status"),
        },
    ]
    return render_template(
        "admin/launch_checklist.html",
        checklist=checklist,
        ready_count=sum(1 for item in checklist if item["ready"]),
        user=current_user,
    )


@main_bp.route("/admin/maintenance")
@login_required
def admin_maintenance():
    require_permission("admin:reports")
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = sorted([path for path in BACKUP_DIR.iterdir() if path.is_file()], key=lambda path: path.stat().st_mtime, reverse=True)
    log_path = Path(current_app.instance_path) / "logs" / "app.log"
    deploy_items = [
        ("PostgreSQL", not current_app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///")),
        ("SMTP", bool(current_app.config.get("MAIL_SERVER"))),
        ("HTTPS production mode", bool(current_app.config.get("SESSION_COOKIE_SECURE") and current_app.config.get("FORCE_HTTPS"))),
        ("Virus scanner", bool(current_app.config.get("VIRUS_SCAN_COMMAND"))),
    ]
    migration_version = current_migration_revision() or "Unavailable"
    latest_revision = latest_migration_revision()
    return render_template(
        "admin/maintenance.html",
        backups=backups,
        latest_backup=backups[0] if backups else None,
        log_path=log_path,
        log_size=log_path.stat().st_size if log_path.exists() else 0,
        migration_version=migration_version,
        latest_revision=latest_revision,
        migration_current=bool(migration_version and latest_revision and migration_version == latest_revision),
        deploy_items=deploy_items,
        user=current_user,
    )


@main_bp.route("/admin/logs")
@login_required
def admin_logs():
    require_permission("admin:reports")
    log_path = Path(current_app.instance_path) / "logs" / "app.log"
    try:
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        limit = 200
    limit = max(20, min(limit, 1000))
    lines = []
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    return render_template("admin/logs.html", lines=lines, log_path=log_path, limit=limit, user=current_user)


@main_bp.route("/admin/retention", methods=["GET", "POST"])
@login_required
def admin_retention():
    require_permission("admin:reports")
    removed_count = None
    if request.method == "POST":
        try:
            days = int(request.form.get("days", "365"))
        except ValueError:
            days = 365
        days = max(30, min(days, 3650))
        cutoff = datetime.now() - timedelta(days=days)
        target = request.form.get("target", "")
        removed_count = 0
        if target == "notifications":
            removed_count = NotificationLog.query.filter(NotificationLog.created_at < cutoff).delete()
        elif target == "closed_messages":
            removed_count = PortalMessage.query.filter(PortalMessage.status == "closed", PortalMessage.updated_at < cutoff).delete()
        elif target == "audit_logs":
            removed_count = AuditLog.query.filter(AuditLog.created_at < cutoff).delete()
        elif target == "backups":
            removed_count = len(cleanup_old_backups(keep=int(request.form.get("keep", "10") or 10)))
        else:
            flash("Choose a valid retention target.", "danger")
            return redirect(url_for("main.admin_retention"))
        audit("run_retention_cleanup", "Retention", None, f"{target}: removed {removed_count}")
        db.session.commit()
        flash(f"Retention cleanup complete. Removed {removed_count} record(s).", "success")
    counts = {
        "notifications": NotificationLog.query.count(),
        "closed_messages": PortalMessage.query.filter_by(status="closed").count(),
        "audit_logs": AuditLog.query.count(),
        "backups": len([path for path in BACKUP_DIR.iterdir() if path.is_file()]) if BACKUP_DIR.exists() else 0,
    }
    return render_template("admin/retention.html", counts=counts, removed_count=removed_count, user=current_user)


def filtered_notification_query():
    query = NotificationLog.query
    channel = request.args.get("channel", "").strip()
    status = request.args.get("status", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    if channel:
        query = query.filter_by(channel=channel)
    if status:
        query = query.filter_by(status=status)
    if date_from:
        query = query.filter(NotificationLog.created_at >= date_from)
    if date_to:
        query = query.filter(NotificationLog.created_at <= f"{date_to} 23:59:59")
    return query.order_by(NotificationLog.created_at.desc())


@main_bp.route("/admin/notifications")
@login_required
def admin_notifications():
    require_permission("admin:reports")
    logs = filtered_notification_query().limit(500).all()
    return render_template("admin/notifications.html", logs=logs, user=current_user)


@main_bp.route("/admin/notifications/export")
@login_required
def admin_notifications_export():
    require_permission("admin:reports")
    rows = [
        [item.id, item.channel, item.recipient, item.subject, item.status, item.provider, item.error, short_date(item.created_at)]
        for item in filtered_notification_query().limit(5000).all()
    ]
    audit("export_filtered_notifications", "NotificationLog", None, str(dict(request.args)))
    return csv_response(
        "ables-filtered-notifications.csv",
        ["id", "channel", "recipient", "subject", "status", "provider", "error", "created_at"],
        rows,
    )


@main_bp.route("/admin/reports")
@login_required
def admin_reports():
    require_role("admin")

    appointments = Appointment.query.all()
    messages = PortalMessage.query.all()
    documents = ClientDocument.query.all()
    availability = CaregiverAvailability.query.all()
    notification_logs = NotificationLog.query.order_by(NotificationLog.created_at.desc()).limit(20).all()
    audit_events = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(10).all()
    report_cards = [
        {
            "title": "Client inquiries",
            "count": Inquiry.query.count(),
            "description": "Public contact, consultation, and care-request submissions.",
            "url": "/admin/inquiries/export",
        },
        {
            "title": "Caregiver applications",
            "count": CaregiverApplication.query.count(),
            "description": "Applicant contact details, availability, requirements, references, and status.",
            "url": "/admin/applications/export",
        },
        {
            "title": "Appointments",
            "count": len(appointments),
            "description": "Visit requests, status, assigned caregivers, and service details.",
            "url": "/admin/reports/export/appointments",
        },
        {
            "title": "Portal messages",
            "count": len(messages),
            "description": "Client and caregiver questions, priorities, replies, and status.",
            "url": "/admin/reports/export/messages",
        },
        {
            "title": "Documents",
            "count": len(documents),
            "description": "Uploaded files, categories, owners, and upload dates.",
            "url": "/admin/reports/export/documents",
        },
        {
            "title": "Caregiver availability",
            "count": len(availability),
            "description": "Structured days, times, service areas, and caregiver notes.",
            "url": "/admin/reports/export/availability",
        },
        {
            "title": "Audit activity",
            "count": AuditLog.query.count(),
            "description": "Security and operational actions recorded by the system.",
            "url": "/admin/reports/export/audit",
        },
        {
            "title": "Notification delivery",
            "count": NotificationLog.query.count(),
            "description": "Email and SMS reminder attempts, skipped sends, and failures.",
            "url": "/admin/reports/export/notifications",
        },
    ]

    return render_template(
        "admin/reports.html",
        user=current_user,
        report_cards=report_cards,
        reminders=appointment_reminders(appointments),
        open_messages=len([item for item in messages if item.status in ["new", "in review"]]),
        unassigned=len([
            item for item in appointments
            if not item.caregiver_id and item.status not in ["completed", "cancelled", "rejected"]
        ]),
        recent_events=audit_events,
        notification_logs=notification_logs,
    )


@main_bp.route("/admin/reports/export/<report_type>")
@login_required
def admin_export_report(report_type):
    require_role("admin")

    audit("export_report", "Report", None, report_type)

    if report_type == "appointments":
        query = apply_created_at_filters(Appointment.query, Appointment)
        rows = [
            [
                item.id,
                item.client.name if item.client else "",
                item.caregiver.name if item.caregiver else "",
                item.date,
                item.time,
                item.service_type,
                item.service_address,
                item.status,
                item.notes,
                item.caregiver_notes,
                short_date(item.created_at),
            ]
            for item in query.order_by(Appointment.date.asc(), Appointment.time.asc()).all()
        ]
        return csv_response(
            "ables-appointments.csv",
            ["id", "client", "caregiver", "date", "time", "service", "address", "status", "client_notes", "caregiver_notes", "created_at"],
            rows,
        )

    if report_type == "messages":
        query = apply_created_at_filters(PortalMessage.query, PortalMessage)
        rows = [
            [
                item.id,
                item.sender.name if item.sender else "",
                item.sender.email if item.sender else "",
                item.sender.role if item.sender else "",
                item.topic,
                item.subject,
                item.priority,
                item.status,
                item.body,
                item.admin_reply,
                short_date(item.created_at),
                short_date(item.updated_at),
            ]
            for item in query.order_by(PortalMessage.updated_at.desc()).all()
        ]
        return csv_response(
            "ables-portal-messages.csv",
            ["id", "sender", "email", "role", "topic", "subject", "priority", "status", "body", "admin_reply", "created_at", "updated_at"],
            rows,
        )

    if report_type == "documents":
        query = apply_created_at_filters(ClientDocument.query, ClientDocument)
        rows = [
            [
                item.id,
                item.owner.name if item.owner else "",
                item.uploaded_by.name if item.uploaded_by else "",
                item.title,
                item.category,
                item.original_filename,
                item.notes,
                short_date(item.created_at),
            ]
            for item in query.order_by(ClientDocument.created_at.desc()).all()
        ]
        return csv_response(
            "ables-documents.csv",
            ["id", "owner", "uploaded_by", "title", "category", "filename", "notes", "created_at"],
            rows,
        )

    if report_type == "availability":
        rows = [
            [
                item.id,
                item.caregiver.name if item.caregiver else "",
                item.caregiver.email if item.caregiver else "",
                item.weekday,
                item.start_time,
                item.end_time,
                item.service_area,
                item.notes,
                short_date(item.created_at),
            ]
            for item in CaregiverAvailability.query.order_by(CaregiverAvailability.weekday.asc(), CaregiverAvailability.start_time.asc()).all()
        ]
        return csv_response(
            "ables-caregiver-availability.csv",
            ["id", "caregiver", "email", "weekday", "start_time", "end_time", "service_area", "notes", "created_at"],
            rows,
        )

    if report_type == "audit":
        rows = [
            [
                item.id,
                item.user.email if item.user else "",
                item.action,
                item.entity_type,
                item.entity_id,
                item.details,
                short_date(item.created_at),
            ]
            for item in AuditLog.query.order_by(AuditLog.created_at.desc()).limit(1000).all()
        ]
        return csv_response(
            "ables-audit-activity.csv",
            ["id", "user", "action", "entity_type", "entity_id", "details", "created_at"],
            rows,
        )

    if report_type == "notifications":
        rows = [
            [
                item.id,
                item.channel,
                item.recipient,
                item.subject,
                item.status,
                item.provider,
                item.related_type,
                item.related_id,
                item.error,
                short_date(item.created_at),
            ]
            for item in NotificationLog.query.order_by(NotificationLog.created_at.desc()).limit(5000).all()
        ]
        return csv_response(
            "ables-notification-delivery.csv",
            ["id", "channel", "recipient", "subject", "status", "provider", "related_type", "related_id", "error", "created_at"],
            rows,
        )

    flash("Unknown report type.", "danger")
    return redirect(url_for("main.admin_reports"))


@main_bp.route("/admin/reminders/send/<label>", methods=["POST"])
@login_required
def admin_send_reminders(label):
    require_role("admin")
    if label not in REMINDER_OFFSETS:
        flash("Unknown reminder type.", "danger")
        return redirect(url_for("main.admin_reports"))

    target_date = (date.today() + timedelta(days=REMINDER_OFFSETS[label])).isoformat()
    key = reminder_key(label)
    appointments = active_appointment_query().filter_by(date=target_date).all()
    sent_count = 0
    skipped_count = 0
    for appointment in appointments:
        if appointment.reminder_sent_for == key:
            skipped_count += 1
            continue
        if send_visit_reminder(appointment, label):
            sent_count += 1
        appointment.reminder_sent_for = key

    audit("send_visit_reminders", "Appointment", None, f"{label}: sent {sent_count}, skipped {skipped_count}")
    db.session.commit()
    flash(f"Reminder run complete. Sent {sent_count}; skipped {skipped_count} already sent.", "success")
    return redirect(request.referrer or url_for("main.admin_reports"))


@main_bp.route("/admin/backups")
@login_required
def admin_backups():
    require_role("admin")
    BACKUP_DIR.mkdir(exist_ok=True)
    backups = sorted(
        [path for path in BACKUP_DIR.iterdir() if path.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return render_template("admin/backups.html", backups=backups, user=current_user)


@main_bp.route("/admin/backups/create", methods=["POST"])
@login_required
def admin_create_backup():
    require_role("admin")
    backup_path = create_backup(include_uploads=True)
    audit("create_backup", "Backup", None, backup_path.name)
    flash(f"Backup created: {backup_path.name}", "success")
    return redirect(url_for("main.admin_backups"))


@main_bp.route("/admin/backups/cleanup", methods=["POST"])
@login_required
def admin_cleanup_backups():
    require_role("admin")
    try:
        keep = int(request.form.get("keep", "10"))
    except ValueError:
        keep = 10
    keep = max(1, min(keep, 50))
    removed = cleanup_old_backups(keep=keep)
    audit("cleanup_backups", "Backup", None, f"Kept {keep}; removed {len(removed)}")
    db.session.commit()
    flash(f"Backup cleanup complete. Removed {len(removed)} old backup(s).", "success")
    return redirect(request.referrer or url_for("main.admin_backups"))


@main_bp.route("/admin/backups/<path:filename>/delete", methods=["POST"])
@login_required
def admin_delete_backup(filename):
    require_role("admin")
    safe_name = Path(filename).name
    backup_path = BACKUP_DIR / safe_name
    if (
        Path(safe_name).suffix.lower() not in BACKUP_EXTENSIONS
        or not safe_name.startswith(("ablehome-", "ablehome-full-"))
        or not backup_path.is_file()
    ):
        abort(404)
    backup_path.unlink()
    audit("delete_backup", "Backup", None, safe_name)
    db.session.commit()
    flash(f"Backup deleted: {safe_name}", "success")
    return redirect(url_for("main.admin_backups"))


@main_bp.route("/admin/backups/<path:filename>/download")
@login_required
def admin_download_backup(filename):
    require_role("admin")
    safe_name = Path(filename).name
    if (
        Path(safe_name).suffix.lower() not in BACKUP_EXTENSIONS
        or not safe_name.startswith(("ablehome-", "ablehome-full-"))
        or not (BACKUP_DIR / safe_name).is_file()
    ):
        abort(404)

    audit("download_backup", "Backup", None, safe_name)
    return send_from_directory(BACKUP_DIR, safe_name, as_attachment=True)


@main_bp.route("/admin/users")
@login_required
def admin_users():
    require_role("admin")

    users = User.query.order_by(User.role, User.name).all()
    user_summaries = {}
    for account in users:
        user_summaries[account.id] = {
            "appointments": Appointment.query.filter(
                (Appointment.client_id == account.id) | (Appointment.caregiver_id == account.id)
            ).count(),
            "documents": ClientDocument.query.filter_by(owner_id=account.id, archived_at=None).count(),
            "messages": PortalMessage.query.filter_by(sender_id=account.id).count(),
            "notes": AdminNote.query.filter_by(subject_user_id=account.id).count(),
        }
    role_counts = {
        "admin": sum(1 for account in users if account.role == "admin"),
        "client": sum(1 for account in users if account.role == "client"),
        "caregiver": sum(1 for account in users if account.role == "caregiver"),
    }
    return render_template(
        "admin/users.html",
        users=users,
        user_summaries=user_summaries,
        role_counts=role_counts,
        user=current_user,
    )


@main_bp.route("/admin/users/create", methods=["GET", "POST"])
@login_required
def admin_create_user():
    require_role("admin")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if User.query.filter_by(email=email).first():
            flash("A user with that email already exists.", "danger")
            return redirect(url_for("main.admin_create_user"))

        password = request.form.get("password", "")
        if not password_is_strong(password):
            feedback = password_feedback(password)
            flash("Password is not safe enough. Add " + ", ".join(feedback["missing"]) + ".", "danger")
            return redirect(url_for("main.admin_create_user"))

        user = User(
            name=request.form.get("name", "").strip(),
            email=email,
            phone=request.form.get("phone", "").strip(),
            address=request.form.get("address", "").strip(),
            role=request.form.get("role", "client"),
            emergency_contact_name=request.form.get("emergency_contact_name", "").strip(),
            emergency_contact_phone=request.form.get("emergency_contact_phone", "").strip(),
            care_needs=request.form.get("care_needs", "").strip(),
            preferred_schedule=request.form.get("preferred_schedule", "").strip(),
            password=generate_password_hash(password),
        )

        db.session.add(user)
        audit("create_user", "User", None, f"Created {email} as {user.role}")
        db.session.commit()

        flash("User created successfully.", "success")
        return redirect(url_for("main.admin_users"))

    return render_template("admin/user_form.html", user=current_user)


@main_bp.route("/admin/users/<int:user_id>/role", methods=["POST"])
@login_required
def admin_update_user_role(user_id):
    require_role("admin")

    user = get_record_or_404(User, user_id)
    user.role = request.form.get("role", user.role)
    audit("update_user_role", "User", user.id, f"Role changed to {user.role}")
    db.session.commit()

    flash("User role updated.", "success")
    return redirect(url_for("main.admin_users"))


@main_bp.route("/admin/inquiries")
@login_required
def admin_inquiries():
    require_role("admin")

    inquiries = Inquiry.query.order_by(Inquiry.created_at.desc()).all()
    referrals = Referral.query.order_by(Referral.created_at.desc()).all()

    return render_template(
        "admin/inquiries.html",
        inquiries=inquiries,
        referrals=referrals,
        follow_up_statuses=FOLLOW_UP_STATUSES,
        user=current_user,
    )


@main_bp.route("/admin/inquiries/export")
@login_required
def admin_inquiries_export():
    require_role("admin")
    rows = [
        [
            item.id,
            item.name,
            item.email,
            item.phone,
            item.reason,
            item.care_recipient,
            item.service_address,
            item.city,
            item.start_date,
            item.start_timeline,
            item.services_needed,
            item.schedule_preference,
            item.mobility_needs,
            item.diagnosis_notes,
            item.emergency_contact,
            item.preferred_language,
            "yes" if item.consent_confirmed else "no",
            item.status,
            item.message,
            short_date(item.created_at),
        ]
        for item in Inquiry.query.order_by(Inquiry.created_at.desc()).all()
    ]
    audit("export_inquiries", "Inquiry", None, f"{len(rows)} row(s)")
    return csv_response(
        "ables-client-inquiries.csv",
        [
            "id",
            "name",
            "email",
            "phone",
            "reason",
            "care_recipient",
            "service_address",
            "city",
            "start_date",
            "start_timeline",
            "services_needed",
            "schedule_preference",
            "mobility_needs",
            "diagnosis_notes",
            "emergency_contact",
            "preferred_language",
            "consent_confirmed",
            "status",
            "message",
            "created_at",
        ],
        rows,
    )


@main_bp.route("/admin/referrals/export")
@login_required
def admin_referrals_export():
    require_role("admin")
    rows = [
        [
            item.id,
            item.referral_source,
            item.source_phone,
            item.client_name,
            item.client_phone,
            item.care_needed,
            item.follow_up,
            item.notes,
            item.status,
            short_date(item.created_at),
        ]
        for item in Referral.query.order_by(Referral.created_at.desc()).all()
    ]
    audit("export_referrals", "Referral", None, f"{len(rows)} row(s)")
    return csv_response(
        "ables-referrals.csv",
        ["id", "referral_source", "source_phone", "client_name", "client_phone", "care_needed", "follow_up", "notes", "status", "created_at"],
        rows,
    )


@main_bp.route("/admin/care-plans")
@login_required
def admin_care_plans():
    require_role("admin")

    care_plans = CarePlan.query.order_by(CarePlan.updated_at.desc()).all()
    inquiries = Inquiry.query.order_by(Inquiry.created_at.desc()).limit(50).all()
    referrals = Referral.query.order_by(Referral.created_at.desc()).limit(50).all()

    return render_template(
        "admin/care_plans.html",
        care_plans=care_plans,
        inquiries=inquiries,
        referrals=referrals,
        care_plan_statuses=CARE_PLAN_STATUSES,
        user=current_user,
    )


@main_bp.route("/admin/care-plans/create", methods=["POST"])
@login_required
def admin_create_care_plan():
    require_role("admin")

    care_plan = CarePlan(
        inquiry_id=request.form.get("inquiry_id") or None,
        referral_id=request.form.get("referral_id") or None,
        client_name=request.form.get("client_name", "").strip(),
        client_phone=request.form.get("client_phone", "").strip(),
        service_address=request.form.get("service_address", "").strip(),
        service_type=request.form.get("service_type", "").strip(),
        schedule=request.form.get("schedule", "").strip(),
        tasks=request.form.get("tasks", "").strip(),
        risks=request.form.get("risks", "").strip(),
        emergency_contacts=request.form.get("emergency_contacts", "").strip(),
        caregiver_preferences=request.form.get("caregiver_preferences", "").strip(),
        notes=request.form.get("notes", "").strip(),
        status=request.form.get("status", "draft"),
    )

    db.session.add(care_plan)
    audit("create_care_plan", "CarePlan", None, care_plan.client_name)
    db.session.commit()

    flash("Care plan created.", "success")
    return redirect(url_for("main.admin_care_plans"))


@main_bp.route("/admin/care-plans/<int:care_plan_id>/status", methods=["POST"])
@login_required
def admin_update_care_plan_status(care_plan_id):
    require_role("admin")

    care_plan = get_record_or_404(CarePlan, care_plan_id)
    care_plan.status = request.form.get("status", care_plan.status)
    audit("update_care_plan_status", "CarePlan", care_plan.id, care_plan.status)
    db.session.commit()

    flash("Care plan status updated.", "success")
    return redirect(url_for("main.admin_care_plans"))


@main_bp.route("/admin/applications")
@login_required
def admin_applications():
    require_role("admin")

    applications = CaregiverApplication.query.order_by(CaregiverApplication.created_at.desc()).all()
    return render_template(
        "admin/applications.html",
        applications=applications,
        application_statuses=APPLICATION_STATUSES,
        user=current_user,
    )


@main_bp.route("/admin/applications/export")
@login_required
def admin_applications_export():
    require_role("admin")
    rows = [
        [
            item.id,
            item.name,
            item.email,
            item.phone,
            item.address,
            item.availability,
            item.weekday_availability,
            item.weekend_availability,
            item.certifications,
            item.years_experience,
            item.transportation,
            item.preferred_service_areas,
            item.applicant_references,
            "yes" if item.background_check_consent else "no",
            item.requirements_checklist,
            item.resume_filename,
            item.experience,
            item.status,
            short_date(item.created_at),
            short_date(item.updated_at),
        ]
        for item in CaregiverApplication.query.order_by(CaregiverApplication.created_at.desc()).all()
    ]
    audit("export_caregiver_applications", "CaregiverApplication", None, f"{len(rows)} row(s)")
    return csv_response(
        "ables-caregiver-applications.csv",
        [
            "id",
            "name",
            "email",
            "phone",
            "address",
            "availability",
            "weekday_availability",
            "weekend_availability",
            "certifications",
            "years_experience",
            "transportation",
            "preferred_service_areas",
            "references",
            "background_check_consent",
            "requirements_checklist",
            "resume_filename",
            "experience",
            "status",
            "created_at",
            "updated_at",
        ],
        rows,
    )


@main_bp.route("/admin/applications/<int:application_id>/status", methods=["POST"])
@login_required
def admin_update_application_status(application_id):
    require_role("admin")

    application = get_record_or_404(CaregiverApplication, application_id)
    application.status = request.form.get("status", application.status)
    audit("update_application_status", "CaregiverApplication", application.id, application.status)
    db.session.commit()

    flash("Application status updated.", "success")
    return redirect(url_for("main.admin_applications"))


@main_bp.route("/admin/applications/archive-test-records", methods=["POST"])
@login_required
def admin_archive_test_applications():
    require_role("admin")

    applications = CaregiverApplication.query.all()
    archived_count = 0
    for application in applications:
        if looks_like_test_record(application.name, application.email, application.experience, application.applicant_references):
            application.status = "not selected"
            archived_count += 1

    audit("archive_test_applications", "CaregiverApplication", None, f"Archived {archived_count} test applications")
    db.session.commit()
    flash(f"Archived {archived_count} test caregiver application record(s).", "success")
    return redirect(url_for("main.admin_applications"))


@main_bp.route("/admin/users/<int:user_id>/onboarding", methods=["POST"])
@login_required
def admin_update_onboarding(user_id):
    require_role("admin")
    caregiver = get_record_or_404(User, user_id)
    if caregiver.role != "caregiver":
        flash("Onboarding checklist is only available for caregiver accounts.", "danger")
        return redirect(url_for("main.admin_edit_user", user_id=caregiver.id))

    for item_type in ONBOARDING_ITEMS:
        status = request.form.get(f"status_{item_type}", "pending")
        notes = request.form.get(f"notes_{item_type}", "").strip()
        item = CaregiverOnboardingItem.query.filter_by(caregiver_id=caregiver.id, item_type=item_type).first()
        if not item:
            item = CaregiverOnboardingItem(caregiver_id=caregiver.id, item_type=item_type)
            db.session.add(item)
        item.status = status
        item.notes = notes
        item.completed_at = datetime.now() if status == "complete" and not item.completed_at else (None if status != "complete" else item.completed_at)
    audit("update_caregiver_onboarding", "User", caregiver.id, "Checklist updated")
    db.session.commit()
    flash("Caregiver onboarding checklist updated.", "success")
    return redirect(url_for("main.admin_edit_user", user_id=caregiver.id))


@main_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def admin_edit_user(user_id):
    require_role("admin")
    account = get_record_or_404(User, user_id)

    if request.method == "POST":
        account.name = request.form.get("name", "").strip()
        account.email = request.form.get("email", "").strip().lower()
        account.phone = request.form.get("phone", "").strip()
        account.address = request.form.get("address", "").strip()
        account.role = request.form.get("role", account.role)
        account.emergency_contact_name = request.form.get("emergency_contact_name", "").strip()
        account.emergency_contact_phone = request.form.get("emergency_contact_phone", "").strip()
        account.care_needs = request.form.get("care_needs", "").strip()
        account.preferred_schedule = request.form.get("preferred_schedule", "").strip()
        audit("edit_user", "User", account.id, f"Updated {account.email}")
        db.session.commit()

        flash("User updated.", "success")
        return redirect(url_for("main.admin_users"))

    notes = AdminNote.query.filter_by(subject_user_id=account.id).order_by(AdminNote.created_at.desc()).all()
    documents = ClientDocument.query.filter_by(owner_id=account.id, archived_at=None).order_by(ClientDocument.created_at.desc()).all()
    archived_documents = ClientDocument.query.filter(
        ClientDocument.owner_id == account.id,
        ClientDocument.archived_at.isnot(None),
    ).order_by(ClientDocument.archived_at.desc()).all()
    activity = {
        "audit_events": AuditLog.query.filter_by(user_id=account.id).order_by(AuditLog.created_at.desc()).limit(25).all(),
        "appointments": Appointment.query.filter(
            (Appointment.client_id == account.id) | (Appointment.caregiver_id == account.id)
        ).order_by(Appointment.created_at.desc()).limit(25).all(),
        "messages": PortalMessage.query.filter_by(sender_id=account.id).order_by(PortalMessage.created_at.desc()).limit(25).all(),
        "documents": ClientDocument.query.filter_by(owner_id=account.id).order_by(ClientDocument.created_at.desc()).limit(25).all(),
    }
    onboarding_items = []
    if account.role == "caregiver":
        existing_items = {
            item.item_type: item
            for item in CaregiverOnboardingItem.query.filter_by(caregiver_id=account.id).all()
        }
        onboarding_items = [
            existing_items.get(item_type) or CaregiverOnboardingItem(caregiver_id=account.id, item_type=item_type, status="pending")
            for item_type in ONBOARDING_ITEMS
        ]
    return render_template(
        "admin/user_edit.html",
        account=account,
        admin_notes=notes,
        documents=documents,
        archived_documents=archived_documents,
        activity=activity,
        onboarding_items=onboarding_items,
        document_categories=DOCUMENT_CATEGORIES,
        user=current_user,
    )


@main_bp.route("/admin/users/<int:user_id>/notes", methods=["POST"])
@login_required
def admin_add_user_note(user_id):
    require_role("admin")
    account = get_record_or_404(User, user_id)
    note_text = request.form.get("note", "").strip()
    if not note_text:
        flash("Add a note before saving.", "danger")
        return redirect(url_for("main.admin_edit_user", user_id=account.id))

    note = AdminNote(subject_user_id=account.id, admin_id=current_user.id, note=note_text)
    db.session.add(note)
    audit("add_admin_note", "User", account.id, "Private admin note added")
    db.session.commit()
    flash("Private admin note saved.", "success")
    return redirect(url_for("main.admin_edit_user", user_id=account.id))


@main_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    require_role("admin")
    account = get_record_or_404(User, user_id)

    if account.id == current_user.id:
        flash("You cannot delete your own admin account.", "danger")
        return redirect(url_for("main.admin_users"))

    audit("delete_user", "User", account.id, f"Deleted {account.email}")
    db.session.delete(account)
    db.session.commit()

    flash("User deleted.", "success")
    return redirect(url_for("main.admin_users"))


@main_bp.route("/admin/inquiry/<int:inquiry_id>/status", methods=["POST"])
@login_required
def admin_update_inquiry_status(inquiry_id):
    require_role("admin")
    inquiry = get_record_or_404(Inquiry, inquiry_id)
    inquiry.status = request.form.get("status", inquiry.status)
    audit("update_inquiry_status", "Inquiry", inquiry.id, inquiry.status)
    db.session.commit()

    flash("Inquiry status updated.", "success")
    return redirect(url_for("main.admin_inquiries"))


@main_bp.route("/admin/inquiries/archive-test-records", methods=["POST"])
@login_required
def admin_archive_test_inquiries():
    require_role("admin")

    inquiries = Inquiry.query.all()
    archived_count = 0
    for inquiry in inquiries:
        if looks_like_test_record(inquiry.name, inquiry.email, inquiry.reason, inquiry.message):
            inquiry.status = "closed"
            archived_count += 1

    audit("archive_test_inquiries", "Inquiry", None, f"Archived {archived_count} test inquiries")
    db.session.commit()
    flash(f"Archived {archived_count} test inquiry record(s).", "success")
    return redirect(url_for("main.admin_inquiries"))


@main_bp.route("/admin/referral/<int:referral_id>/status", methods=["POST"])
@login_required
def admin_update_referral_status(referral_id):
    require_role("admin")
    referral = get_record_or_404(Referral, referral_id)
    referral.status = request.form.get("status", referral.status)
    audit("update_referral_status", "Referral", referral.id, referral.status)
    db.session.commit()

    flash("Referral status updated.", "success")
    return redirect(url_for("main.admin_inquiries"))


@main_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.phone = request.form.get("phone", "").strip()
        current_user.address = request.form.get("address", "").strip()
        current_user.emergency_contact_name = request.form.get("emergency_contact_name", "").strip()
        current_user.emergency_contact_phone = request.form.get("emergency_contact_phone", "").strip()
        current_user.care_needs = request.form.get("care_needs", "").strip()
        current_user.preferred_schedule = request.form.get("preferred_schedule", "").strip()
        audit("update_profile", "User", current_user.id, "Profile updated")
        db.session.commit()

        flash("Profile updated.", "success")
        return redirect(url_for("main.profile"))

    return render_template("profile.html", user=current_user)


@main_bp.route("/caregiver/availability", methods=["GET", "POST"])
@login_required
def caregiver_availability():
    if current_user.role != "caregiver":
        return "Unauthorized", 403

    if request.method == "POST":
        current_user.preferred_schedule = request.form.get("preferred_schedule", "").strip()
        current_user.address = request.form.get("address", "").strip()
        current_user.phone = request.form.get("phone", "").strip()
        CaregiverAvailability.query.filter_by(caregiver_id=current_user.id).delete()
        for weekday in WEEKDAYS:
            if request.form.get(f"available_{weekday}") != "yes":
                continue

            start_time = request.form.get(f"start_{weekday}", "").strip()
            end_time = request.form.get(f"end_{weekday}", "").strip()
            if not start_time or not end_time:
                continue

            db.session.add(CaregiverAvailability(
                caregiver_id=current_user.id,
                weekday=weekday,
                start_time=start_time,
                end_time=end_time,
                service_area=request.form.get(f"area_{weekday}", "").strip(),
                notes=request.form.get(f"notes_{weekday}", "").strip(),
            ))
        audit("update_caregiver_availability", "User", current_user.id, "Availability updated")
        db.session.commit()
        flash("Availability updated.", "success")
        return redirect(url_for("main.caregiver_availability"))

    availability_rows = {
        item.weekday: item
        for item in CaregiverAvailability.query.filter_by(caregiver_id=current_user.id).all()
    }
    return render_template(
        "caregiver/availability.html",
        user=current_user,
        weekdays=WEEKDAYS,
        availability_rows=availability_rows,
    )


@main_bp.route("/documents", methods=["GET", "POST"])
@login_required
def documents():
    if current_user.role == "admin":
        return redirect(url_for("main.admin_users"))

    if request.method == "POST":
        file = request.files.get("document")
        if not file or not file.filename:
            flash("Choose a document to upload.", "danger")
            return redirect(url_for("main.documents"))

        if not allowed_document(file.filename):
            flash("Allowed file types: PDF, PNG, JPG, DOC, DOCX.", "danger")
            return redirect(url_for("main.documents"))

        if request.form.get("document_consent") != "yes":
            flash("Please confirm the document upload privacy acknowledgement.", "danger")
            return redirect(url_for("main.documents"))

        stored_filename, original_filename = save_private_upload(
            file,
            "documents",
            f"user-{current_user.id}",
            allowed_document,
        )
        if not stored_filename:
            flash("Document upload was blocked by the safety scan.", "danger")
            return redirect(url_for("main.documents"))

        document = ClientDocument(
            owner_id=current_user.id,
            uploaded_by_id=current_user.id,
            title=request.form.get("title", "").strip() or original_filename,
            category=request.form.get("category", "Other").strip(),
            filename=stored_filename,
            original_filename=original_filename,
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(document)
        audit("upload_document", "ClientDocument", None, document.title)
        db.session.commit()
        send_admin_notification(
            f"New portal document: {document.title}",
            f"{current_user.name} uploaded {document.original_filename} in category {document.category}.",
            reply_to=current_user.email,
        )
        flash("Document uploaded securely.", "success")
        return redirect(url_for("main.documents"))

    documents = ClientDocument.query.filter_by(owner_id=current_user.id, archived_at=None).order_by(ClientDocument.created_at.desc()).all()
    archived_documents = ClientDocument.query.filter(
        ClientDocument.owner_id == current_user.id,
        ClientDocument.archived_at.isnot(None),
    ).order_by(ClientDocument.archived_at.desc()).all()
    return render_template(
        "documents.html",
        documents=documents,
        archived_documents=archived_documents,
        document_categories=DOCUMENT_CATEGORIES,
        user=current_user,
    )


@main_bp.route("/admin/users/<int:user_id>/documents", methods=["POST"])
@login_required
def admin_upload_user_document(user_id):
    require_role("admin")
    account = get_record_or_404(User, user_id)
    file = request.files.get("document")
    if not file or not file.filename:
        flash("Choose a document to upload.", "danger")
        return redirect(url_for("main.admin_edit_user", user_id=account.id))

    if not allowed_document(file.filename):
        flash("Allowed file types: PDF, PNG, JPG, DOC, DOCX.", "danger")
        return redirect(url_for("main.admin_edit_user", user_id=account.id))

    stored_filename, original_filename = save_private_upload(
        file,
        "documents",
        f"user-{account.id}-admin-{current_user.id}",
        allowed_document,
    )
    if not stored_filename:
        flash("Document upload was blocked by the safety scan.", "danger")
        return redirect(url_for("main.admin_edit_user", user_id=account.id))

    document = ClientDocument(
        owner_id=account.id,
        uploaded_by_id=current_user.id,
        title=request.form.get("title", "").strip() or original_filename,
        category=request.form.get("category", "Other").strip(),
        filename=stored_filename,
        original_filename=original_filename,
        notes=request.form.get("notes", "").strip(),
    )
    db.session.add(document)
    audit("admin_upload_document", "ClientDocument", None, f"{account.email}: {document.title}")
    db.session.commit()
    flash("Document added to user file.", "success")
    return redirect(url_for("main.admin_edit_user", user_id=account.id))


@main_bp.route("/documents/<int:document_id>/download")
@login_required
def download_document(document_id):
    document = get_record_or_404(ClientDocument, document_id)
    if current_user.role != "admin" and document.owner_id != current_user.id:
        return "Unauthorized", 403

    upload_folder = Path(current_app.config["UPLOAD_FOLDER"]) / "documents"
    stored_name = Path(document.filename).name
    if not (upload_folder / stored_name).is_file():
        abort(404)

    audit("download_document", "ClientDocument", document.id, document.title)
    return send_from_directory(
        upload_folder,
        stored_name,
        as_attachment=True,
        download_name=safe_original_filename(document.original_filename, "document"),
    )


@main_bp.route("/documents/<int:document_id>/archive", methods=["POST"])
@login_required
def archive_document(document_id):
    document = get_record_or_404(ClientDocument, document_id)
    if current_user.role != "admin" and document.owner_id != current_user.id:
        return "Unauthorized", 403

    document.archived_at = db.func.now()
    document.archived_by_id = current_user.id
    audit("archive_document", "ClientDocument", document.id, document.title)
    db.session.commit()
    flash("Document archived.", "success")
    return redirect(request.referrer or url_for("main.documents"))


@main_bp.route("/documents/<int:document_id>/restore", methods=["POST"])
@login_required
def restore_document(document_id):
    document = get_record_or_404(ClientDocument, document_id)
    if current_user.role != "admin" and document.owner_id != current_user.id:
        return "Unauthorized", 403

    document.archived_at = None
    document.archived_by_id = None
    audit("restore_document", "ClientDocument", document.id, document.title)
    db.session.commit()
    flash("Document restored.", "success")
    return redirect(request.referrer or url_for("main.documents"))


@main_bp.route("/documents/<int:document_id>/delete", methods=["POST"])
@login_required
def delete_document(document_id):
    document = get_record_or_404(ClientDocument, document_id)
    if current_user.role != "admin" and document.owner_id != current_user.id:
        return "Unauthorized", 403

    upload_folder = Path(current_app.config["UPLOAD_FOLDER"]) / "documents"
    file_path = upload_folder / Path(document.filename).name
    if file_path.exists():
        file_path.unlink()
    audit("delete_document", "ClientDocument", document.id, document.title)
    db.session.delete(document)
    db.session.commit()
    flash("Document permanently deleted.", "success")
    return redirect(request.referrer or url_for("main.documents"))


# ---------------- REQUEST CARE ----------------
@main_bp.route("/request-care", methods=["GET", "POST"])
@login_required
def request_care():

    if current_user.role != "client":
        return "Unauthorized", 403

    if request.method == "POST":

        urgent_hits = has_urgent_language(
            request.form.get("service_type"),
            request.form.get("mobility_needs"),
            request.form.get("emergency_contact"),
            request.form.get("notes"),
        )
        appointment = Appointment(
            client_id=current_user.id,
            date=request.form.get("date"),
            time=request.form.get("time"),
            service_type=request.form.get("service_type"),
            service_address=request.form.get("service_address", "").strip(),
            schedule_preference=request.form.get("schedule_preference", "").strip(),
            mobility_needs=request.form.get("mobility_needs", "").strip(),
            emergency_contact=request.form.get("emergency_contact", "").strip(),
            preferred_language=request.form.get("preferred_language", "").strip(),
            consent_confirmed=request.form.get("consent_confirmed") == "yes",
            notes=request.form.get("notes"),
            urgent_flag=bool(urgent_hits),
            urgent_reason=", ".join(urgent_hits[:5]),
            status="pending"
        )

        db.session.add(appointment)
        audit("request_care", "Appointment", None, appointment.service_type)
        if appointment.urgent_flag:
            audit("urgent_care_request", "Appointment", None, appointment.urgent_reason)
        db.session.commit()

        return redirect(url_for("main.dashboard"))

    return render_template("client/request_care.html")


# ---------------- ADMIN APPOINTMENTS ----------------
@main_bp.route("/admin/appointments")
@login_required
def admin_appointments():

    require_role("admin")

    appointments = Appointment.query.order_by(Appointment.date.asc(), Appointment.time.asc()).all()
    users = User.query.all()
    caregivers = [user for user in users if user.role == "caregiver"]
    match_suggestions = {
        appointment.id: caregiver_matches_for_appointment(appointment, caregivers, appointments)[:3]
        for appointment in appointments
        if appointment.status not in ["completed", "cancelled", "rejected"]
    }

    return render_template(
        "admin/appointments.html",
        user=current_user,
        appointments=appointments,
        users=users,
        match_suggestions=match_suggestions,
    )


@main_bp.route("/admin/schedule")
@login_required
def admin_schedule():
    require_role("admin")

    view = request.args.get("view", "all")
    today = date.today()
    appointments = Appointment.query.order_by(Appointment.date.asc(), Appointment.time.asc()).all()
    if view == "week":
        week_end = today + timedelta(days=6)
        appointments = [
            item for item in appointments
            if appointment_date_or_none(item.date) and today <= appointment_date_or_none(item.date) <= week_end
        ]
    elif view == "month":
        appointments = [
            item for item in appointments
            if appointment_date_or_none(item.date)
            and appointment_date_or_none(item.date).year == today.year
            and appointment_date_or_none(item.date).month == today.month
        ]
    caregivers = User.query.filter_by(role="caregiver").order_by(User.name.asc()).all()
    schedule_days = {}
    for appointment in appointments:
        day = appointment.date or "Unscheduled"
        schedule_days.setdefault(day, []).append(appointment)

    unassigned = [
        appointment for appointment in appointments
        if not appointment.caregiver_id and appointment.status not in ["completed", "cancelled", "rejected"]
    ]
    match_suggestions = {
        appointment.id: caregiver_matches_for_appointment(appointment, caregivers, appointments)[:3]
        for appointment in unassigned
    }
    double_bookings = caregiver_double_bookings(appointments)
    missing_notes = [
        appointment for appointment in appointments
        if appointment.status == "completed" and not (appointment.caregiver_notes or "").strip()
    ]

    return render_template(
        "admin/schedule.html",
        user=current_user,
        schedule_days=schedule_days,
        caregivers=caregivers,
        unassigned=unassigned,
        match_suggestions=match_suggestions,
        reminders=appointment_reminders(appointments),
        current_view=view,
        double_bookings=double_bookings,
        missing_notes=missing_notes,
    )


def caregiver_double_bookings(appointments):
    grouped = {}
    for appointment in appointments:
        if not appointment.caregiver_id or appointment.status in ["completed", "cancelled", "rejected"]:
            continue
        key = (appointment.caregiver_id, appointment.date, appointment.time)
        grouped.setdefault(key, []).append(appointment)
    return [items for items in grouped.values() if len(items) > 1]


@main_bp.route("/admin/caregiver-conflicts")
@login_required
def admin_caregiver_conflicts():
    require_permission("appointments:manage")
    today_iso = date.today().isoformat()
    appointments = Appointment.query.order_by(Appointment.date.asc(), Appointment.time.asc()).all()
    active = [item for item in appointments if item.status not in ["completed", "cancelled", "rejected"]]
    unassigned = [item for item in active if not item.caregiver_id and (item.date or "") >= today_iso]
    missing_notes = [item for item in appointments if item.status == "completed" and not (item.caregiver_notes or "").strip()]
    return render_template(
        "admin/caregiver_conflicts.html",
        double_bookings=caregiver_double_bookings(appointments),
        unassigned=unassigned,
        missing_notes=missing_notes,
        user=current_user,
    )


# ---------------- APPROVE / REJECT ----------------
@main_bp.route("/admin/appointment/<int:appointment_id>/<action>", methods=["POST"])
@login_required
def update_appointment_status(appointment_id, action):

    require_role("admin")

    appointment = get_record_or_404(Appointment, appointment_id)

    if action == "approve":
        appointment.status = "approved"

    elif action == "reject":
        appointment.status = "rejected"

    db.session.commit()

    return redirect(url_for("main.admin_appointments"))


# ---------------- ASSIGN CAREGIVER ----------------
@main_bp.route(
    "/admin/appointment/<int:appointment_id>/assign",
    methods=["POST"]
)
@login_required
def assign_caregiver(appointment_id):

    require_role("admin")

    appointment = get_record_or_404(Appointment, appointment_id)

    caregiver_id = request.form.get("caregiver_id")

    if not caregiver_id:
        flash("Please create or select a caregiver before assigning this appointment.", "danger")
        return redirect(url_for("main.admin_appointments"))

    caregiver_id = int(caregiver_id)
    conflicts = caregiver_assignment_conflicts(appointment, caregiver_id)
    if conflicts and request.form.get("override_conflict") != "yes":
        caregiver = db.session.get(User, caregiver_id)
        flash(
            f"{caregiver.name if caregiver else 'Caregiver'} already has a visit at this date and time. Check Override conflict to assign anyway.",
            "danger",
        )
        return redirect(request.referrer or url_for("main.admin_appointments"))

    appointment.caregiver_id = caregiver_id
    appointment.status = "assigned"
    audit(
        "assign_caregiver",
        "Appointment",
        appointment.id,
        f"Caregiver {caregiver_id}" + (" with conflict override" if conflicts else ""),
    )

    db.session.commit()

    return redirect(request.referrer or url_for("main.admin_appointments"))


@main_bp.route("/admin/appointment/<int:appointment_id>/move", methods=["POST"])
@login_required
def admin_move_appointment(appointment_id):
    require_role("admin")
    appointment = get_record_or_404(Appointment, appointment_id)
    new_date = request.form.get("date", "").strip()
    new_time = request.form.get("time", "").strip()

    if not appointment_date_or_none(new_date) or not new_time:
        flash("Choose a valid date and time.", "danger")
        return redirect(url_for("main.admin_schedule"))

    old_value = f"{appointment.date} {appointment.time}"
    appointment.date = new_date
    appointment.time = new_time
    conflicts = caregiver_assignment_conflicts(appointment, appointment.caregiver_id) if appointment.caregiver_id else []
    conflict_note = f"; conflict warnings: {len(conflicts)}" if conflicts else ""
    audit("move_appointment", "Appointment", appointment.id, f"{old_value} -> {new_date} {new_time}{conflict_note}")
    db.session.commit()
    if conflicts:
        flash("Appointment moved, but the assigned caregiver has a conflict at that date and time.", "warning")
    else:
        flash("Appointment moved.", "success")
    return redirect(url_for("main.admin_schedule"))


@main_bp.route("/admin/appointment/<int:appointment_id>/repeat", methods=["POST"])
@login_required
def create_recurring_appointments(appointment_id):
    require_role("admin")

    source = get_record_or_404(Appointment, appointment_id)
    start_date = appointment_date_or_none(source.date)
    if not start_date:
        flash("Add a valid appointment date before creating recurring visits.", "danger")
        return redirect(request.referrer or url_for("main.admin_appointments"))

    try:
        count = int(request.form.get("count") or request.form.get("weeks", "4"))
    except ValueError:
        count = 4
    count = max(1, min(count, 52))

    try:
        interval_weeks = int(request.form.get("interval_weeks", "1"))
    except ValueError:
        interval_weeks = 1
    interval_weeks = max(1, min(interval_weeks, 4))

    end_date = appointment_date_or_none(request.form.get("end_date"))
    group_id = source.recurring_group_id or f"series-{source.id}"
    source.recurring_group_id = group_id
    source.recurring_interval_weeks = interval_weeks
    source.recurring_paused = False

    created = 0
    skipped = 0
    for offset in range(1, count + 1):
        next_date_value = start_date + timedelta(days=7 * interval_weeks * offset)
        if end_date and next_date_value > end_date:
            break
        next_date = next_date_value.isoformat()
        duplicate = Appointment.query.filter_by(
            client_id=source.client_id,
            date=next_date,
            time=source.time,
            service_type=source.service_type,
        ).first()
        if duplicate:
            skipped += 1
            continue

        appointment = Appointment(
            client_id=source.client_id,
            caregiver_id=source.caregiver_id,
            date=next_date,
            time=source.time,
            service_type=source.service_type,
            service_address=source.service_address,
            schedule_preference=source.schedule_preference,
            mobility_needs=source.mobility_needs,
            emergency_contact=source.emergency_contact,
            preferred_language=source.preferred_language,
            consent_confirmed=source.consent_confirmed,
            notes=source.notes,
            status="assigned" if source.caregiver_id else "approved",
            recurring_group_id=group_id,
            recurring_interval_weeks=interval_weeks,
            recurring_paused=False,
        )
        db.session.add(appointment)
        created += 1

    audit(
        "create_recurring_appointments",
        "Appointment",
        source.id,
        f"Created {created} recurring visits every {interval_weeks} week(s); skipped {skipped}",
    )
    db.session.commit()
    flash(f"Created {created} recurring visit(s). Skipped {skipped} duplicate(s).", "success")
    return redirect(request.referrer or url_for("main.admin_schedule"))


@main_bp.route("/admin/recurring/<group_id>/<action>", methods=["POST"])
@login_required
def admin_update_recurring_series(group_id, action):
    require_role("admin")
    if action not in ["pause", "resume"]:
        flash("Unknown recurring series action.", "danger")
        return redirect(url_for("main.admin_schedule"))

    paused = action == "pause"
    appointments = Appointment.query.filter_by(recurring_group_id=group_id).all()
    for appointment in appointments:
        appointment.recurring_paused = paused
        if paused and appointment.status not in ["completed", "cancelled", "rejected"]:
            appointment.status = "paused"
        elif not paused and appointment.status == "paused":
            appointment.status = "assigned" if appointment.caregiver_id else "approved"

    audit("update_recurring_series", "Appointment", None, f"{group_id}: {action}")
    db.session.commit()
    flash(f"Recurring series {action}d.", "success")
    return redirect(request.referrer or url_for("main.admin_schedule"))


# ---------------- CAREGIVER SCHEDULE ----------------
@main_bp.route("/caregiver/schedule")
@login_required
def caregiver_schedule():

    if current_user.role != "caregiver":
        return "Unauthorized", 403

    appointments = Appointment.query.filter_by(
        caregiver_id=current_user.id
    ).order_by(Appointment.date.asc(), Appointment.time.asc()).all()

    return render_template(
        "caregiver/schedule.html",
        appointments=appointments,
        user=current_user
    )


# ---------------- COMPLETE VISIT ----------------
@main_bp.route("/caregiver/appointment/<int:appointment_id>/complete", methods=["POST"])
@login_required
def complete_appointment(appointment_id):

    if current_user.role != "caregiver":
        return "Unauthorized", 403

    appointment = get_record_or_404(Appointment, appointment_id)

    if appointment.caregiver_id != current_user.id:
        return "Unauthorized", 403

    appointment.caregiver_notes = request.form.get("caregiver_notes", appointment.caregiver_notes)
    appointment.status = "completed"
    audit("complete_appointment", "Appointment", appointment.id, "Caregiver completed visit")

    db.session.commit()

    return redirect(
        url_for("main.caregiver_schedule")
    )


# ---------------- HOME REDIRECT ----------------
@main_bp.route("/home")
def home_redirect():
    return redirect(url_for("main.home"))


def app_config_value(key):
    from flask import current_app

    return current_app.config.get(key)

@main_bp.route("/my-appointments")
@login_required
def my_appointments():

    if current_user.role != "client":
        return "Unauthorized", 403

    appointments = Appointment.query.filter_by(
        client_id=current_user.id
    ).all()

    return render_template(
        "client/my_appointments.html",
        appointments=appointments,
        user=current_user
    )


@main_bp.route("/appointment/<int:appointment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_appointment(appointment_id):
    appointment = get_record_or_404(Appointment, appointment_id)

    if current_user.role == "client" and appointment.client_id != current_user.id:
        return "Unauthorized", 403
    if current_user.role not in ["client", "admin"]:
        return "Unauthorized", 403

    if request.method == "POST":
        appointment.date = request.form.get("date", appointment.date)
        appointment.time = request.form.get("time", appointment.time)
        appointment.service_type = request.form.get("service_type", appointment.service_type)
        appointment.notes = request.form.get("notes", appointment.notes)
        audit("edit_appointment", "Appointment", appointment.id, "Appointment updated")
        db.session.commit()

        flash("Appointment updated.", "success")
        return redirect(url_for("main.my_appointments" if current_user.role == "client" else "main.admin_appointments"))

    return render_template("appointment_edit.html", appointment=appointment, user=current_user)


@main_bp.route("/appointment/<int:appointment_id>/cancel", methods=["POST"])
@login_required
def cancel_appointment(appointment_id):
    appointment = get_record_or_404(Appointment, appointment_id)

    if current_user.role == "client" and appointment.client_id != current_user.id:
        return "Unauthorized", 403
    if current_user.role not in ["client", "admin"]:
        return "Unauthorized", 403

    appointment.status = "cancelled"
    audit("cancel_appointment", "Appointment", appointment.id, "Appointment cancelled")
    db.session.commit()

    flash("Appointment cancelled.", "success")
    return redirect(url_for("main.my_appointments" if current_user.role == "client" else "main.admin_appointments"))


@main_bp.route("/caregiver/appointment/<int:appointment_id>/notes", methods=["POST"])
@login_required
def caregiver_notes(appointment_id):
    if current_user.role != "caregiver":
        return "Unauthorized", 403

    appointment = get_record_or_404(Appointment, appointment_id)

    if appointment.caregiver_id != current_user.id:
        return "Unauthorized", 403

    appointment.caregiver_notes = request.form.get("caregiver_notes", "").strip()
    audit("caregiver_notes", "Appointment", appointment.id, "Caregiver notes updated")
    db.session.commit()

    flash("Visit notes saved.", "success")
    return redirect(url_for("main.caregiver_schedule"))


