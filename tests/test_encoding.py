"""Guard against mojibake in templates and static assets.

Some edit pipelines (PowerShell Get-Content/Set-Content round trips) can
silently re-encode UTF-8 as cp1252 and produce sequences like "Â·" or
"â€”" on the live site.  This test fails if any of that reappears.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A lead byte that only appears when UTF-8 was decoded as latin-1/cp1252,
# followed by a character in the 0x80-0xFF range.
MOJIBAKE = re.compile(r"[\u00c2\u00c3\u00e2\u00e3\u00ef][\u0080-\u00bf\u20ac\u201a\u201c\u201e\u2019\u201d\u2013\u2014]")
# Control characters that only exist in broken cp1252 round trips.
CONTROLS = re.compile(r"[\u0081\u008d\u008f\u0090\u009d]")
REPLACEMENT = "\ufffd"

TARGET_DIRS = ("templates", "static")
TARGET_EXTENSIONS = (".html", ".js", ".css", ".txt", ".json")


def _files():
    for folder in TARGET_DIRS:
        base = os.path.join(ROOT, folder)
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if name.endswith(TARGET_EXTENSIONS):
                    yield os.path.join(dirpath, name)


def test_no_mojibake_in_templates_and_static():
    problems = []
    for path in _files():
        with open(path, "rb") as handle:
            raw = handle.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append("%s: not valid UTF-8 (%s)" % (path, exc))
            continue
        if raw.startswith(b"\xef\xbb\xbf"):
            problems.append("%s: starts with a UTF-8 BOM" % path)
        hits = MOJIBAKE.findall(text)
        if hits:
            problems.append("%s: %d mojibake sequence(s) e.g. %r" % (path, len(hits), hits[0]))
        controls = CONTROLS.findall(text)
        if controls:
            problems.append("%s: %d broken control character(s)" % (path, len(controls)))
        if REPLACEMENT in text:
            problems.append("%s: contains a replacement character (U+FFFD)" % path)
    assert not problems, "encoding corruption found:\n" + "\n".join(problems)


def test_templates_keep_their_original_typography():
    """The separator and dashes that were always in the copy must stay put."""
    with open(os.path.join(ROOT, "templates", "home.html"), encoding="utf-8") as handle:
        home = handle.read()
    assert "\u00b7" in home, "the middle-dot separators are missing from home.html"
    assert "\u2014" in home, "the em dashes are missing from home.html"
    assert "Â" not in home and "â€" not in home
