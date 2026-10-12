"""Two-page condensation regression tests (public experience only)."""
from app import app


def _client():
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_client()


def test_home_is_compact_two_page_hub():
    with _client() as client:
        page = client.get("/")
        assert page.status_code == 200
        html = page.data.decode()
        assert html.count("<h1") == 1
        assert "/care" in html
        assert "301-455-3030" in html
        assert "301-326-2225" not in html
        assert "tel:+13014553030" in html
        assert "R3114" in html
        # original postcard faces preserved with enlarge links
        assert "postcard-front-corrected.jpg" in html
        assert "postcard-back-corrected.jpg" in html
        assert "postcard-front-print.jpg" in html
        assert "postcard-back-print.jpg" in html
        # no video centerpiece, no carousel
        assert "<video" not in html
        assert "hero-carousel" not in html


def test_care_page_has_all_three_working_forms():
    with _client() as client:
        page = client.get("/care")
        assert page.status_code == 200
        html = page.data.decode()
        assert html.count("<h1") == 1
        assert 'action="/free-consultation"' in html
        assert 'action="/referrals"' in html
        assert 'action="/employment-application"' in html
        # request-care fields preserved
        for name in ["care_level", "payment_method", "specific_needs", "diagnosis_notes",
                     "consent_confirmed", "emergency_contact", "preferred_language"]:
            assert f'name="{name}"' in html
        # referral fields preserved
        for name in ["referral_source", "client_name", "care_needed"]:
            assert f'name="{name}"' in html
        # caregiver application fields preserved (incl. upload + consent)
        for name in ["credential_type", "background_check_consent", "applicant_references",
                     "requirements", 'name="resume"']:
            assert f'{name}' in html
        assert "301-455-3030" in html
        assert "301-326-2225" not in html


def test_public_phone_links_use_authoritative_target():
    with _client() as client:
        for url in ["/", "/care", "/contact", "/brochure"]:
            html = client.get(url).data.decode()
            assert "tel:3013262225" not in html
            assert "301-326-2225" not in html
