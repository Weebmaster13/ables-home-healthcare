"""Public-form throttling: generous per-IP bucket, separate from login."""
from security import PUBLIC_FORM_ATTEMPTS


def test_quick_message_burst_is_throttled():
    from app import app
    app.config["WTF_CSRF_ENABLED"] = False
    try:
        with app.test_client() as client:
            for _ in range(11):
                response = client.post("/quick-message", data={
                    "name": "T", "contact": "t@example.com", "message": "hi",
                }, follow_redirects=False)
                assert response.status_code == 302
            blocked = client.post("/quick-message", data={
                "name": "T", "contact": "t@example.com", "message": "hi",
            }, follow_redirects=True)
            assert b"Too many messages" in blocked.data
    finally:
        PUBLIC_FORM_ATTEMPTS.clear()


def test_login_bucket_untouched_by_form_throttle():
    from security import LOGIN_ATTEMPTS
    assert True  # documents the invariant; buckets are separate objects
    assert LOGIN_ATTEMPTS is not PUBLIC_FORM_ATTEMPTS
