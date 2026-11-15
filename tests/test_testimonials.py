"""Unverified endorsements must not appear on public pages.

The three previously published quotes/names are asserted ABSENT from
every page that ever rendered them, while the pages themselves must
keep working (status, headings, no broken markup).
"""
from app import app

REMOVED = [
    "This home healthcare group has been a godsend",
    "Having help with daily routines gave our loved one",
    "The scheduling process made it easier to know what visits",
    "Carole C.",
    "Client family",
    "Referral partner",
]

PAGES = ["/", "/why-choose-us", "/testimonials"]


def test_removed_endorsements_absent_everywhere():
    with app.test_client() as client:
        for path in PAGES:
            response = client.get(path)
            assert response.status_code == 200, path
            html = response.data.decode("utf-8")
            for snippet in REMOVED:
                assert snippet not in html, f"{snippet!r} on {path}"


def test_testimonial_pages_still_render_without_quotes():
    with app.test_client() as client:
        for path, heading in [("/testimonials", "Stories of reassurance"),
                              ("/why-choose-us", "Trust is built")]:
            html = client.get(path).data.decode("utf-8")
            assert heading in html
            assert "testimonial-card h-100" not in html


def test_no_review_structured_data():
    with app.test_client() as client:
        html = client.get("/").data.decode("utf-8")
        assert '"review"' not in html.lower()
        assert "aggregateRating" not in html
