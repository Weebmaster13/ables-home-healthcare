#!/usr/bin/env python3
"""Sensitive-route inventory: every route mapped to its access control.

Parses routes.py + auth/routes.py with AST (no app boot needed) and
reports, per route: methods, login_required, require_role roles,
require_permission permissions, owner-check idioms
(owner_id/user_id comparison, get_record_or_404), and unsafe patterns
(redirect(request.), send_file/send_from_directory targets).

Usage: python scripts/route_inventory.py [--flags-only]
Exit 0 always (informational); --flags-only exits 1 if any route with a
sensitive prefix lacks login_required (used by tests/CI).
"""

import ast
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FILES = [BASE_DIR / "routes.py", BASE_DIR / "auth" / "routes.py"]

SENSITIVE_PREFIXES = ("/admin/", "/dashboard", "/profile", "/request-care",
                      "/caregiver/", "/messages", "/documents",
                      "/my-appointments", "/appointment", "/change-password")
# Public marketing pages whose names collide with the prefixes above.
PUBLIC_EXCEPTIONS = {"/administrator", "/caregivers"}


def _route_decorators(node):
    routes = []
    for dec in node.decorator_list:
        func = dec.func if isinstance(dec, ast.Call) else None
        name = ""
        if isinstance(dec, ast.Attribute):
            name = dec.attr
        elif isinstance(dec, ast.Name):
            name = dec.id
        elif func is not None and isinstance(func, ast.Attribute):
            name = func.attr
        if name != "route" or func is None:
            if name == "login_required":
                routes.append(("login_required", None))
            continue
        path, methods = None, ["GET"]
        for arg in dec.args:
            if isinstance(arg, ast.Constant):
                if isinstance(arg.value, str) and path is None:
                    path = arg.value
                elif isinstance(arg.value, list):
                    methods = list(arg.value)
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, ast.List):
                methods = [e.value for e in kw.value.elts
                           if isinstance(e, ast.Constant)]
        if path:
            routes.append(("route", (path, methods)))
    return routes


def analyze_source(tree):
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decs = _route_decorators(node)
        paths = [v for k, v in decs if k == "route"]
        if not paths:
            continue
        logged_in = any(k == "login_required" for k, _ in decs)
        roles, perms, owner, unsafe = [], [], False, []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fname = (sub.func.attr if isinstance(sub.func, ast.Attribute)
                         else getattr(sub.func, "id", ""))
                if fname in ("require_role", "require_permission"):
                    for arg in sub.args:
                        if isinstance(arg, ast.Constant):
                            (roles if fname == "require_role"
                             else perms).append(arg.value)
                if fname in ("redirect",):
                    for arg in sub.args:
                        src = ast.dump(arg)
                        if "request" in src and "referrer" not in src \
                                and "url_for" not in src:
                            unsafe.append("redirect(non-referrer-request-data)")
                if fname in ("send_file", "send_from_directory", "open"):
                    unsafe.append(f"{fname}(check target)")
            if isinstance(sub, ast.Compare):
                if "owner_id" in ast.dump(sub) or "user_id" in ast.dump(sub):
                    owner = True
        for path, methods in paths:
            rows.append({"path": path, "methods": methods,
                         "login": logged_in, "roles": sorted(set(roles)),
                         "permissions": sorted(set(perms)),
                         "owner_check": owner, "notes": sorted(set(unsafe))})
    return rows


def main(argv=None):
    flags_only = "--flags-only" in (argv or sys.argv[1:])
    all_rows = []
    for path in FILES:
        all_rows.extend(analyze_source(
            ast.parse(path.read_text(encoding="utf-8"))))
    all_rows.sort(key=lambda r: r["path"])
    bad = 0
    for row in all_rows:
        if not flags_only:
            gate = ("login" if row["login"] else "PUBLIC")
            extra = []
            if row["roles"]:
                extra.append("roles=" + ",".join(row["roles"]))
            if row["permissions"]:
                extra.append("perms=" + ",".join(row["permissions"]))
            if row["owner_check"]:
                extra.append("owner-check")
            print(f"{','.join(row['methods']):9} {row['path']:<55} "
                  f"{gate} {' '.join(extra)}")
        if (row["path"].startswith(SENSITIVE_PREFIXES)
                and row["path"] not in PUBLIC_EXCEPTIONS
                and not row["login"]):
            bad += 1
            print(f"[FAIL] ungated sensitive route: {row['path']}")
    if not flags_only:
        print(f"routes: {len(all_rows)} inventoried")
    return 1 if flags_only and bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
