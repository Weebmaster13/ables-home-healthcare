"""SEO regression tests: sitemap, robots, canonical, metadata, noindex."""
import re

from app import app

PAGES = ["/", "/about", "/administrator", "/care", "/care-process",
         "/care-programs", "/careers", "/contact",
         "/free-consultation", "/referrals", "/services",
         "/service-areas", "/privacy", "/terms", "/faq",
         "/brochure", "/why-choose-us", "/testimonials",
         "/services/personal-care",
         "/resources/home-care-vs-home-health-care",
         "/home-care-maryland-service-areas",
         "/personal-care-montgomery-county-md"]

AUTH_PAGES = ["/auth/login", "/auth/register", "/auth/forgot-password"]


def client():
    return app.test_client()


def test_sitemap_is_valid_and_covers_public_pages():
    response = client().get("/sitemap.xml")
    assert response.status_code == 200
    assert response.content_type.startswith("application/xml")
    body = response.data.decode("utf-8")
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<urlset" in body
    locs = re.findall(r"<loc>([^<]+)</loc>", body)
    assert len(locs) >= 40, f"only {len(locs)} urls"
    for path in ["/brochure", "/why-choose-us", "/contact", "/referrals",
                 "/services/personal-care"]:
        assert any(u.rstrip("/").endswith(path) for u in locs), path
    assert not any("/admin/" in u or "/auth/" in u or "/login" in u.split("/")[-1]
                   or "/register" in u for u in locs)


def test_robots_allows_public_blocks_private():
    response = client().get("/robots.txt")
    assert response.status_code == 200
    body = response.data.decode("utf-8")
    assert "User-agent: *" in body
    assert "Allow: /" in body
    for blocked in ["/admin/", "/caregiver/", "/dashboard", "/profile",
                    "/request-care"]:
        assert f"Disallow: {blocked}" in body, blocked
    with app.app_context():
        base = app.config["SITE_BASE_URL"].rstrip("/")
    assert f"Sitemap: {base}/sitemap.xml" in body


def test_canonical_points_at_configured_base():
    with app.app_context():
        base = app.config["SITE_BASE_URL"].rstrip("/")
    html = client().get("/").data.decode("utf-8")
    assert f'<link rel="canonical" href="{base}/">' in html


def test_auth_pages_carry_noindex_and_unique_titles():
    titles = set()
    for path in AUTH_PAGES:
        html = client().get(path).data.decode("utf-8")
        assert '<meta name="robots" content="noindex, nofollow">' in html, path
        title = re.search(r"<title>([^<]*)</title>", html).group(1)
        assert "Ables Home Healthcare" in title
        titles.add(title)
    assert len(titles) == len(AUTH_PAGES), "auth titles must differ"


def test_local_seo_meta_is_bounded_and_grammatical():
    html = client().get("/home-care-maryland-service-areas").data.decode("utf-8")
    meta = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
    assert len(meta) <= 230, f"meta too long ({len(meta)})"
    assert "Become a Client" not in meta
    assert "free consultation" in meta


def test_key_pages_have_unique_titles_and_h1():
    seen = {}
    for path in PAGES:
        response = client().get(path)
        assert response.status_code == 200, path
        html = response.data.decode("utf-8")
        title = re.search(r"<title>([^<]*)</title>", html).group(1)
        assert "Ables Home Healthcare" in title, path
        assert title not in seen, f"duplicate title {title!r} on {path}"
        seen[title] = path
        assert len(re.findall(r"<h1[\s>]", html)) == 1, f"h1 count on {path}"


def test_homepage_open_graph_and_structured_data():
    html = client().get("/").data.decode("utf-8")
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    assert 'property="og:url"' in html
    assert '"@type": "LocalBusiness"' in html
    assert "301-455-3030" in html


def test_home_alias_redirects_instead_of_duplicating():
    response = client().get("/home", follow_redirects=False)
    assert response.status_code in (301, 302, 308)
    assert response.headers["Location"].endswith("/")
