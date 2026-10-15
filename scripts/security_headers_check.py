#!/usr/bin/env python3
"""Read-only security-headers check (local or live).

Evaluates an HTTP response header set against the headers the app sends
(see app.register_security_headers). Two uses:

1. Offline (unit-testable, no network):
       check_headers(headers, path="/") -> [(level, message)]
2. Live read-only GET (follows redirects unless --no-follow):
       python scripts/security_headers_check.py https://example.com/ [--no-follow]

Levels: PASS / WARN / FAIL. Exit 0 iff no FAIL. Never sends credentials,
never POSTs, never prints secret values (headers contain none).
"""

import sys
import urllib.request
import urllib.error
from collections.abc import Mapping

PRIVATE_PREFIXES = (
    "/admin", "/auth", "/dashboard", "/profile", "/request-care",
    "/caregiver", "/messages", "/documents", "/my-appointments", "/appointment",
)


def _norm(headers):
    if isinstance(headers, Mapping):
        items = headers.items()
    else:
        items = headers
    return {str(k).lower(): str(v) for k, v in items}


def check_headers(headers, path="/"):
    """Evaluate a header set. Returns [(level, message)]."""
    found = _norm(headers)
    results = []

    def need(name, want=None, level="FAIL"):
        value = found.get(name.lower())
        if value is None:
            results.append((level, f"{name} missing"))
        elif want and want not in value:
            results.append((level, f"{name} present but unexpected: {value[:80]}"))
        else:
            results.append(("PASS", f"{name} present"))

    need("X-Content-Type-Options", "nosniff", "FAIL")
    need("X-Frame-Options", "SAMEORIGIN", "FAIL")
    need("Referrer-Policy", None, "WARN")
    need("Permissions-Policy", None, "WARN")
    need("Content-Security-Policy", "default-src 'self'", "FAIL")
    # HSTS is emitted only when SESSION_COOKIE_SECURE is on (production);
    # absence on a dev/plain-HTTP host is expected, hence WARN not FAIL.
    need("Strict-Transport-Security", "max-age=", "WARN")

    if path.startswith(PRIVATE_PREFIXES):
        value = found.get("cache-control", "")
        if "no-store" in value:
            results.append(("PASS", "Cache-Control no-store on sensitive page"))
        else:
            results.append(("FAIL", "Cache-Control no-store missing on sensitive page"))
    return results


def fetch(url, follow=True, timeout=25):
    """Read-only GET. Returns (status, headers_dict, location_or_None)."""
    if follow:
        opener = urllib.request.build_opener()
    else:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect())
    opener.addheaders = [("User-Agent", "ables-readonly-headers-check")]
    try:
        response = opener.open(url, timeout=timeout)
        return response.status, dict(response.headers), None
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.headers.get("Location")


def main(argv=None):
    args = list(argv or sys.argv[1:])
    follow = True
    if "--no-follow" in args:
        follow = False
        args.remove("--no-follow")
    if len(args) != 1:
        print("usage: security_headers_check.py URL [--no-follow]")
        return 2
    url = args[0]
    if not url.startswith(("http://", "https://")):
        print(f"FAIL refusing non-http(s) URL")
        return 1
    from urllib.parse import urlparse

    try:
        status, headers, location = fetch(url, follow=follow)
    except Exception as exc:
        print(f"FAIL request failed: {type(exc).__name__}")
        return 1
    print(f"HTTP {status}" + (f" -> {location}" if location else ""))
    results = check_headers(headers, path=urlparse(url).path or "/")
    failed = 0
    for level, message in results:
        print(f"[{level}] {message}")
        failed += level == "FAIL"
    print(f"headers: {'FAIL' if failed else 'OK'} ({failed} failure(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
