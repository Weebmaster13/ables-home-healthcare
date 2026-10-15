#!/usr/bin/env python3
"""Compare a cPanel `pip freeze` snapshot against requirements-cpanel.txt.

Read-only and offline: it never contacts the server, never installs
anything, and never prints secret values (freeze output contains none).

Usage (owner runs the freeze on the server, pastes NOTHING secret here):
    <venv>/bin/pip freeze > freeze.txt        # on the cPanel terminal
    python scripts/verify_cpanel_environment.py freeze.txt
    # or:  python scripts/verify_cpanel_environment.py < freeze.txt

Exit 0 iff every pinned package is present at the exact version.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REQUIREMENTS = BASE_DIR / "requirements-cpanel.txt"


def parse_freeze(text):
    """Parse `pip freeze` output into {lower_name: version}. Ignores junk."""
    packages = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")) or "==" not in line:
            continue
        name, _, version = line.partition("==")
        name, version = name.strip().lower(), version.strip()
        if name and version and ";" not in version and " " not in version:
            packages[name] = version
    return packages


def load_requirements(path):
    wanted = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        wanted[name.strip().lower()] = version.strip()
    return wanted


def compare(freeze_text, requirements_path=DEFAULT_REQUIREMENTS):
    wanted = load_requirements(requirements_path)
    installed = parse_freeze(freeze_text)
    results = []  # (level, message)
    for name, expected in sorted(wanted.items()):
        actual = installed.get(name)
        if actual is None:
            results.append(("FAIL", f"{name}=={expected}: MISSING on server"))
        elif actual != expected:
            results.append(("FAIL", f"{name}: server has {actual}, want {expected}"))
        else:
            results.append(("PASS", f"{name}=={expected}"))
    return results


def main(argv=None):
    args = list(argv or sys.argv[1:])
    req_path = DEFAULT_REQUIREMENTS
    if "--requirements" in args:
        idx = args.index("--requirements")
        req_path = Path(args[idx + 1])
        del args[idx:idx + 2]
    if args:
        freeze_text = Path(args[0]).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        freeze_text = sys.stdin.read()
    else:
        print("usage: verify_cpanel_environment.py [freeze.txt] [--requirements PATH]")
        return 2
    if not Path(req_path).exists():
        print(f"FAIL requirements file not found: {req_path}")
        return 1
    results = compare(freeze_text, req_path)
    failed = sum(1 for level, _ in results if level == "FAIL")
    for level, message in results:
        print(f"[{level}] {message}")
    print(f"environment: {'FAIL' if failed else 'OK'} ({failed} mismatch(es))")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
