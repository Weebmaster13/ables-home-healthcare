"""Open-redirect protection on post-action redirects."""
from security import PUBLIC_FORM_ATTEMPTS, safe_redirect_url


def test_safe_redirect_allows_internal_paths():
    assert safe_redirect_url("/contact", "/") == "/contact"
    assert safe_redirect_url("/care#request-care", "/") == "/care#request-care"
    assert safe_redirect_url("/auth/login?next=%2F", "/") == "/auth/login?next=%2F"


def test_safe_redirect_rejects_external_targets():
    evil = [
        "https://evil.example/phish",
        "http://evil.example/",
        "//evil.example/phish",
        "javascript:alert(1)",
        "",
        None,
        "not-a-path",
        "\\\\evil.example\\share",
    ]
    for value in evil:
        assert safe_redirect_url(value, "/contact") == "/contact"


def test_quick_message_evil_referrer_stays_on_site():
    from app import app
    app.config["WTF_CSRF_ENABLED"] = False
    try:
        with app.test_client() as client:
            response = client.post(
                "/quick-message",
                data={"name": "T", "contact": "t@example.com", "message": "hi"},
                headers={"Referer": "https://evil.example/phish"},
                follow_redirects=False,
            )
            assert response.status_code == 302
            location = response.headers["Location"]
            assert location.startswith("/"), location
            assert "evil.example" not in location
    finally:
        PUBLIC_FORM_ATTEMPTS.clear()
