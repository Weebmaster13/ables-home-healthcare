"""Duplicate-submit behavior: rapid identical POSTs share one record."""
from datetime import datetime, timezone

from app import app
from extensions import db
from models import Inquiry, Referral


def _post(client, path, data):
    return client.post(path, data=data, follow_redirects=False)


def _contact_data(tag, message="Same notes here."):
    return {"name": "Dup Probe", "email": f"dup-{tag}@example.com",
            "phone": "", "reason": "Ask about services", "message": message}


def test_identical_contact_posts_share_one_row():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        first = _post(client, "/contact", _contact_data("a"))
        second = _post(client, "/contact", _contact_data("a"))
        assert first.status_code == 302
        assert second.status_code == 302
        assert "thank-you" in second.headers["Location"]
    with app.app_context():
        assert Inquiry.query.filter(
            Inquiry.email == "dup-a@example.com").count() == 1


def test_changed_message_creates_new_row():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        _post(client, "/contact", _contact_data("b", "First notes."))
        _post(client, "/contact", _contact_data("b", "Different notes."))
    with app.app_context():
        assert Inquiry.query.filter(
            Inquiry.email == "dup-b@example.com").count() == 2


def test_stale_duplicate_outside_window_is_stored():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        old = Inquiry(name="Old", email="dup-c@example.com",
                      message="Same notes here.",
                      created_at=datetime(2020, 1, 1))
        db.session.add(old)
        db.session.commit()
    with app.test_client() as client:
        _post(client, "/contact", _contact_data("c"))
    with app.app_context():
        assert Inquiry.query.filter(
            Inquiry.email == "dup-c@example.com").count() == 2


def test_identical_referral_posts_share_one_row():
    app.config["WTF_CSRF_ENABLED"] = False
    data = {"referral_source": "Dup Partner", "source_phone": "3015550100",
            "client_name": "Dup Client", "client_phone": "",
            "care_needed": "Companion Care", "follow_up": "",
            "notes": "Same referral notes."}
    with app.test_client() as client:
        assert _post(client, "/referrals", data).status_code == 302
        second = _post(client, "/referrals", data)
        assert second.status_code == 302
        assert "thank-you" in second.headers["Location"]
    with app.app_context():
        assert Referral.query.filter_by(
            referral_source="Dup Partner").count() == 1


def test_identical_quick_messages_share_one_row():
    app.config["WTF_CSRF_ENABLED"] = False
    from security import PUBLIC_FORM_ATTEMPTS
    data = {"name": "Dup QM", "contact": "dup-qm@example.com",
            "message": "Same quick note."}
    try:
        with app.test_client() as client:
            _post(client, "/quick-message", data)
            _post(client, "/quick-message", data)
    finally:
        PUBLIC_FORM_ATTEMPTS.clear()
    with app.app_context():
        assert Inquiry.query.filter_by(reason="Quick Message").filter(
            Inquiry.message == "Same quick note.").count() == 1


def test_timezone_aware_created_at_does_not_block():
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        aware = Inquiry(name="Aware", email="dup-d@example.com",
                        message="Same notes here.",
                        created_at=datetime.now(timezone.utc))
        db.session.add(aware)
        db.session.commit()
    with app.test_client() as client:
        response = _post(client, "/contact", _contact_data("d"))
        assert response.status_code == 302
    with app.app_context():
        assert Inquiry.query.filter(
            Inquiry.email == "dup-d@example.com").count() == 1
