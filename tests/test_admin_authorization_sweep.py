"""Authorization sweep over every admin route.

The staging harness exercises a handful of routes; a successful run there does
not establish that the rest are protected. This test enumerates the live URL
map, so a new admin route is covered the moment it is registered, and drives
each one as an anonymous visitor, an ordinary authenticated user, and a real
administrator.

Rules enforced:

* Anonymous requests must never be served admin content (login redirect or 403).
* A non-admin authenticated user must be refused (403).
* An administrator must not be refused for authorization reasons. A 400/404/500
  from bad path parameters or an empty test database is not an authorization
  failure and is tolerated; a 403 is not.

Login uses the real ``/auth/login`` flow rather than a hand-built session,
because the app sets ``session_protection = "strong"``, which rejects synthetic
sessions and would make every request look anonymous.
"""
import pytest

from app import app
from extensions import db
from models import User
from werkzeug.security import generate_password_hash

# ``/administrator`` shares the prefix but is a public informational page, not
# an admin tool. Only the /admin/ namespace is administrative.
ADMIN_PREFIX = "/admin/"


def _admin_rules():
    """(rule, methods) for every route under /admin/, from the live URL map."""
    found = []
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith(ADMIN_PREFIX):
            continue
        methods = {m for m in rule.methods if m not in {"HEAD", "OPTIONS"}}
        found.append((str(rule), sorted(methods)))
    return found


def _make_user(email, role):
    with app.app_context():
        User.query.filter_by(email=email).delete()
        user = User(
            email=email,
            name=email.split("@")[0],
            role=role,
            password=generate_password_hash("Sweep-Pass-123!"),
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def _client_as(email, role):
    """A client logged in through the real login form."""
    _make_user(email, role)
    client = app.test_client()
    response = client.post(
        "/auth/login",
        data={"email": email, "password": "Sweep-Pass-123!"},
        follow_redirects=False,
    )
    # A successful login redirects to the dashboard; a failure re-renders (200).
    assert response.status_code == 302, (
        f"login for {email} ({role}) did not succeed: {response.status_code}"
    )
    return client


def _fill_params(rule):
    path = rule
    for placeholder, value in (
        ("<int:user_id>", "1"),
        ("<int:message_id>", "1"),
        ("<int:inquiry_id>", "1"),
        ("<int:referral_id>", "1"),
        ("<int:care_plan_id>", "1"),
        ("<int:application_id>", "1"),
        ("<int:appointment_id>", "1"),
        ("<int:document_id>", "1"),
        ("<int:group_id>", "1"),
        ("<path:filename>", "x.txt"),
        ("<label>", "today"),
        ("<action>", "confirm"),
        ("<report_type>", "inquiries"),
    ):
        path = path.replace(placeholder, value)
    return path


def _call(client, rule, method):
    return client.open(_fill_params(rule), method=method, follow_redirects=False)


def test_every_admin_route_is_enumerated():
    """Guard against the sweep silently covering nothing."""
    assert len(_admin_rules()) >= 40


def test_anonymous_cannot_reach_any_admin_route():
    rules = _admin_rules()
    assert rules, "no admin routes found"
    client = app.test_client()
    offenders = []
    for rule, methods in rules:
        for method in methods:
            response = _call(client, rule, method)
            if response.status_code not in (302, 401, 403):
                offenders.append(f"{method} {rule} -> {response.status_code}")
    assert not offenders, "anonymous reached admin routes: " + "; ".join(offenders)


def test_non_admin_is_refused_every_admin_route():
    rules = _admin_rules()
    client = _client_as("sweep-caregiver@example.com", "caregiver")
    offenders = []
    for rule, methods in rules:
        for method in methods:
            response = _call(client, rule, method)
            if response.status_code != 403:
                offenders.append(f"{method} {rule} -> {response.status_code}")
    assert not offenders, "non-admin not refused: " + "; ".join(offenders)


def test_admin_is_not_refused_for_authorization():
    rules = _admin_rules()
    client = _client_as("sweep-admin@example.com", "admin")
    offenders = []
    for rule, methods in rules:
        for method in methods:
            response = _call(client, rule, method)
            # 403 here means the route denied an administrator; 400/404/500 from
            # bad parameters or an empty database is not an authz failure.
            if response.status_code == 403:
                offenders.append(f"{method} {rule}")
    assert not offenders, "admin denied by route guard: " + "; ".join(offenders)
