"""Staging end-to-end verification against the real running stack.

Exercises the live HTTP surface (gunicorn + MariaDB + MailHog + ClamAV) rather
than the Flask test client, so it proves the deployment wiring, not just the
code paths.

Usage:
    python tools/staging_verify.py --base http://127.0.0.1:5055 \
        --mailhog http://127.0.0.1:8025 --email admin@staging.invalid \
        --password "$STAGING_ADMIN_PASSWORD"

No credential is committed: --password is required and has no default.
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


class Session:
    """Cookie-aware HTTP client with CSRF extraction."""

    def __init__(self, base, insecure=False):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)]
        if insecure:
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self.opener = urllib.request.build_opener(*handlers)

    @staticmethod
    def _csrf(html):
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        return m.group(1) if m else None

    def _headers(self, extra=None):
        # Browsers always send Referer; Flask-WTF's strict HTTPS CSRF check
        # requires it, so the harness must send it too.
        h = {"Referer": self.base + "/"}
        if extra:
            h.update(extra)
        return h

    def get(self, path):
        req = urllib.request.Request(
            self.base + path, method="GET", headers=self._headers()
        )
        try:
            with self.opener.open(req, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def get_no_redirect(self, path):
        """GET without following redirects; returns (status, content-type)."""
        handlers = [_NoRedirect, urllib.request.HTTPCookieProcessor(self.jar)]
        if self.base.startswith("https://"):
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(
            self.base + path, method="GET", headers=self._headers()
        )
        try:
            with opener.open(req, timeout=30) as r:
                return r.status, r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", "")

    def post_form(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method="POST",
            headers=self._headers(
                {"Content-Type": "application/x-www-form-urlencoded"}
            ),
        )
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def post_multipart(self, path, fields, files):
        boundary = "----stagingverify" + str(int(time.time() * 1000))
        body = io.BytesIO()

        def w(s):
            body.write(s.encode() if isinstance(s, str) else s)

        for k, v in fields.items():
            w(f"--{boundary}\r\n")
            w(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
        for field, filename, ctype, content in files:
            w(f"--{boundary}\r\n")
            w(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n')
            w(f"Content-Type: {ctype}\r\n\r\n")
            w(content)
            w("\r\n")
        w(f"--{boundary}--\r\n")
        req = urllib.request.Request(
            self.base + path, data=body.getvalue(), method="POST",
            headers=self._headers(
                {"Content-Type": f"multipart/form-data; boundary={boundary}"}
            ),
        )
        try:
            with self.opener.open(req, timeout=120) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep redirects visible so auth gates can be asserted, not followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def looks_like_login_page(html):
    """True when the response is the login form (i.e. we were bounced out)."""
    return 'name="password"' in html and "/auth/login" in html


RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def mailhog_messages(mailhog):
    req = urllib.request.Request(mailhog.rstrip("/") + "/api/v2/messages")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r).get("items", [])


def _first_user_id(html):
    m = re.search(r'/admin/users/(\d+)/edit', html)
    return int(m.group(1)) if m else None


EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
# Minimal valid PDF with an EICAR payload placed AFTER the old 4 KB scan window.
PDF_PREFIX = b"%PDF-1.4\n"
PADDING = b"% " + b"A" * 4200 + b"\n"
EICAR_PDF = PDF_PREFIX + PADDING + EICAR + b"\n%%EOF\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://127.0.0.1:4443")
    ap.add_argument("--mailhog", default="http://127.0.0.1:8025")
    ap.add_argument("--email", default="admin@staging.invalid")
    ap.add_argument("--password", required=True,
                    help="admin password for the staging account (no default; "
                         "pass it explicitly so no credential is committed)")
    ap.add_argument("--recovery-code", default=None,
                    help="an unused admin recovery code; required when "
                         "ADMIN_RECOVERY_CODES_REQUIRED=true and codes exist")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (self-signed staging cert)")
    ap.add_argument("--scanner-probe-log", default=None,
                    help="path to a probe log written by the scanner command; "
                         "when set, proves the external scanner receives the "
                         "complete file (see docs/STAGING_RELEASE_EVIDENCE.md)")
    args = ap.parse_args()

    s = Session(args.base, insecure=args.insecure)

    # 1. Minimal liveness
    status, body = s.get("/healthz")
    try:
        minimal = json.loads(body) == {"status": "ok"}
    except Exception:
        minimal = False
    record("healthz is minimal liveness only", status == 200 and minimal,
           f"HTTP {status} {body[:60]}")

    # 2. Login page + CSRF
    status, html = s.get("/auth/login")
    token = s._csrf(html)
    record("login page serves a CSRF token", status == 200 and bool(token), f"HTTP {status}")

    # 3. Public contact form persists
    status, html = s.get("/contact")
    token = s._csrf(html) or token
    status, html = s.post_form("/contact", {
        "csrf_token": token, "name": "Staging Verify",
        "email": "verify@staging.invalid", "phone": "555-0100",
        "subject": "Staging verification",
        "message": "Automated staging verification submission.",
    })
    record("public contact form accepts a submission", status in (200, 302), f"HTTP {status}")

    # 4. Admin login
    status, html = s.get("/auth/login")
    token = s._csrf(html)
    status, html = s.post_form("/auth/login", {
        "csrf_token": token, "email": args.email, "password": args.password,
        "recovery_code": args.recovery_code or "",
    })
    record("admin login succeeds",
           status in (200, 302) and not looks_like_login_page(html), f"HTTP {status}")

    # 4b. With ADMIN_RECOVERY_CODES_REQUIRED=true (production default) a fresh
    # admin is forced to generate recovery codes before anything else. Do it
    # here exactly as the operator must, then confirm the gate lifts.
    status, html = s.get("/admin/security")
    token = s._csrf(html) or token
    status, html = s.post_form("/admin/security/recovery-codes", {"csrf_token": token})
    record("admin recovery-code setup succeeds", status in (200, 302), f"HTTP {status}")
    status, html = s.get("/admin/users")
    record("recovery gate lifts after codes are generated",
           status == 200 and not looks_like_login_page(html), f"HTTP {status}")

    # 5. Password-reset email really delivers through SMTP
    before = len(mailhog_messages(args.mailhog))
    s2 = Session(args.base, insecure=args.insecure)
    status, html = s2.get("/auth/forgot-password")
    token = s2._csrf(html)
    status, html = s2.post_form("/auth/forgot-password", {
        "csrf_token": token, "email": args.email,
    })
    time.sleep(3)
    delivered = len(mailhog_messages(args.mailhog)) - before
    record("password-reset email delivered via SMTP", delivered > 0,
           f"{delivered} new message(s)")

    # 6. Admin area reachable (must not be bounced to login)
    status, html = s.get("/admin/users")
    record("admin area reachable after login",
           status == 200 and not looks_like_login_page(html), f"HTTP {status}")

    # 7. Real scanner: clean PDF is accepted and persisted
    user_id = _first_user_id(html)
    if user_id is None:
        record("found a user id for upload test", False, "no /admin/users/<id> link")
    else:
        clean_pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        status, html = s.get(f"/admin/users/{user_id}/edit")
        token = s._csrf(html) or token
        status, html = s.post_multipart(
            f"/admin/users/{user_id}/documents",
            {"csrf_token": token, "title": "staging-clean", "category": "Other"},
            [("document", "clean.pdf", "application/pdf", clean_pdf)],
        )
        accepted = "safety scan" not in html.lower()
        record("clean PDF accepted by real scanner", accepted, f"HTTP {status}")

        # 8. Real scanner: EICAR rejected
        status, html = s.get(f"/admin/users/{user_id}/edit")
        token = s._csrf(html) or token
        status, html = s.post_multipart(
            f"/admin/users/{user_id}/documents",
            {"csrf_token": token, "title": "staging-eicar", "category": "Other"},
            [("document", "eicar.pdf", "application/pdf", PDF_PREFIX + EICAR + b"\n%%EOF\n")],
        )
        record("EICAR payload rejected by real scanner",
               "safety scan" in html.lower(), f"HTTP {status}")

        # 9. Real scanner: EICAR past the old 4 KB window still rejected
        status, html = s.get(f"/admin/users/{user_id}/edit")
        token = s._csrf(html) or token
        status, html = s.post_multipart(
            f"/admin/users/{user_id}/documents",
            {"csrf_token": token, "title": "staging-eicar-deep", "category": "Other"},
            [("document", "deep.pdf", "application/pdf", EICAR_PDF)],
        )
        record("EICAR past 4 KB rejected (full-file scan)",
               "safety scan" in html.lower(), f"HTTP {status}")

        # 10. When a scanner probe log is supplied, prove the external scanner
        # received the COMPLETE file, not a 4 KB sample.
        if args.scanner_probe_log and os.path.exists(args.scanner_probe_log):
            big = b"%PDF-1.4\n" + b"% " + b"B" * 9000 + b"\n%%EOF\n"
            status, html = s.get(f"/admin/users/{user_id}/edit")
            token = s._csrf(html) or token
            status, html = s.post_multipart(
                f"/admin/users/{user_id}/documents",
                {"csrf_token": token, "title": "probe-full-file", "category": "Other"},
                [("document", "big.pdf", "application/pdf", big)],
            )
            time.sleep(1)
            with open(args.scanner_probe_log) as fh:
                sizes = [int(x) for x in fh.read().split() if x.strip().isdigit()]
            received_full = bool(sizes) and sizes[-1] >= len(big)
            record("external scanner received the complete file",
                   received_full,
                   f"uploaded {len(big)} bytes, scanner saw {sizes[-1] if sizes else 0}")

        # 11. Document-download authorization. Anonymous must be bounced to
        # login, and a different signed-in user must get 403, not the file.
        # Use the document uploaded in this run so its file exists in the
        # currently configured upload folder.
        status, edit_html = s.get(f"/admin/users/{user_id}/edit")
        doc_ids = [int(x) for x in re.findall(r"/documents/(\d+)/download", edit_html)]
        if not doc_ids:
            record("found an uploaded document to download", False, "no download link")
        else:
            doc_id = doc_ids[-1]
            anon = Session(args.base, insecure=args.insecure)
            st, _ct = anon.get_no_redirect(f"/documents/{doc_id}/download")
            record("anonymous download denied", st in (301, 302, 401, 403), f"HTTP {st}")

            st, _ct = s.get_no_redirect(f"/documents/{doc_id}/download")
            record("owner/admin download allowed", st == 200, f"HTTP {st}")

            # Create a second client and confirm it cannot read this document.
            status, html = s.get("/admin/users/create")
            token = s._csrf(html) or token
            s.post_form("/admin/users/create", {
                "csrf_token": token, "name": "Authorization Probe",
                "email": "authprobe@staging.invalid", "role": "client",
                "password": "Auth-Probe-Pass-2026!x",
            })
            other = Session(args.base, insecure=args.insecure)
            status, html = other.get("/auth/login")
            token = other._csrf(html)
            status, html = other.post_form("/auth/login", {
                "csrf_token": token, "email": "authprobe@staging.invalid",
                "password": "Auth-Probe-Pass-2026!x",
            })
            st, _ct = other.get_no_redirect(f"/documents/{doc_id}/download")
            record("non-owner download denied with 403", st == 403, f"HTTP {st}")

    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
