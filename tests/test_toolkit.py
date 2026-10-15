"""Tests for the read-only production-verification toolkit (scripts/)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backup_inventory
import production_readiness
import security_headers_check
import verify_backup_manifest
import verify_cpanel_environment as vce

GOOD_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
}

FREEZE = """Flask==3.1.3
Werkzeug==3.1.9
PyMySQL==1.1.1
# a comment
-e editable-install
malformed-line-without-version
"""


def levels(results):
    return {m: lv for lv, m in results}


# --- security headers ---

def test_good_public_page_has_no_failures():
    res = security_headers_check.check_headers(GOOD_HEADERS, path="/")
    assert not [m for lv, m in res if lv == "FAIL"]


def test_missing_csp_is_fail():
    bad = dict(GOOD_HEADERS)
    del bad["Content-Security-Policy"]
    assert any(lv == "FAIL" and "Content-Security" in m
               for lv, m in security_headers_check.check_headers(bad, "/"))


def test_private_page_requires_no_store():
    bad = dict(GOOD_HEADERS)
    del bad["Cache-Control"]
    res = security_headers_check.check_headers(bad, "/admin/users")
    assert any(lv == "FAIL" and "no-store" in m for lv, m in res)
    # same headers on a public page: no such failure
    res = security_headers_check.check_headers(bad, "/")
    assert not any("no-store" in m and lv == "FAIL" for lv, m in res)


def test_refuses_non_http_url():
    assert security_headers_check.main(["ftp://x/y"]) == 1


# --- cpanel environment compare ---

def test_matching_freeze_passes(tmp_path):
    req = tmp_path / "req.txt"
    req.write_text("Flask==3.1.3\nWerkzeug==3.1.9\n")
    assert vce.compare(FREEZE, req) == [
        ("PASS", "flask==3.1.3"), ("PASS", "werkzeug==3.1.9")]


def test_mismatch_missing_and_malformed(tmp_path):
    req = tmp_path / "req.txt"
    req.write_text("Flask==9.9.9\nAbsent==1.0\n")
    res = dict((m.split(":")[0], lv) for lv, m in vce.compare(FREEZE, req))
    assert res["flask"] == "FAIL"  # version mismatch
    assert res["absent==1.0"] == "FAIL"  # missing
    # malformed freeze lines never crash and never match
    assert vce.parse_freeze("garbage\n==1.0\nname==\n") == {}


def test_empty_freeze_means_all_missing(tmp_path):
    req = tmp_path / "req.txt"
    req.write_text("Flask==3.1.3\n")
    assert vce.compare("", req) == [("FAIL", "flask==3.1.3: MISSING on server")]


# --- backup manifest roundtrip ---

def _make_source(root):
    (root / "uploads").mkdir()
    (root / "app.db").write_bytes(b"fake-db-bytes")
    (root / "uploads" / "resume.pdf").write_bytes(b"%PDF-fake")
    (root / ".env").write_text("SECRET_KEY=topsecret")  # must be excluded
    return [root / "app.db", root / "uploads"]


def test_inventory_skips_secret_names_and_verifies(tmp_path, capsys):
    src, manifest_p = tmp_path / "src", tmp_path / "m.json"
    src.mkdir()
    _make_source(src)
    assert backup_inventory.main([str(manifest_p), str(src)]) == 0
    manifest = json.loads(manifest_p.read_text())
    assert manifest["skipped_secret_names_count"] == 1
    assert "topsecret" not in manifest_p.read_text()
    assert "topsecret" not in capsys.readouterr().out
    assert verify_backup_manifest.main(
        [str(manifest_p), str(src)]) == 0


def test_tamper_and_loss_detected(tmp_path):
    src, manifest_p = tmp_path / "src", tmp_path / "m.json"
    src.mkdir()
    _make_source(src)
    backup_inventory.main([str(manifest_p), str(src)])
    (src / "app.db").write_bytes(b"tampered!!")
    assert verify_backup_manifest.main([str(manifest_p), str(src)]) == 1
    (src / "uploads" / "resume.pdf").unlink()
    out = verify_backup_manifest.verify(
        json.loads(manifest_p.read_text()), src)
    assert any(lv == "FAIL" and "MISSING" in m for lv, m in out)


def test_bad_manifest_inputs_rejected(tmp_path):
    assert verify_backup_manifest.main(["/nonexistent.json", str(tmp_path)]) == 1
    broken = tmp_path / "b.json"
    broken.write_text("{not json")
    assert verify_backup_manifest.main([str(broken), str(tmp_path)]) == 1
    wrong = tmp_path / "w.json"
    wrong.write_text('{"hello": 1}')
    assert verify_backup_manifest.main([str(wrong), str(tmp_path)]) == 1


# --- route inventory ---

def test_inventory_flags_ungated_sensitive_route():
    import ast
    import route_inventory
    tree = ast.parse(
        "@x.route('/admin/secret')\n"
        "def view():\n"
        "    return 'x'\n")
    rows = route_inventory.analyze_source(tree)
    assert rows and rows[0]["login"] is False
    tree = ast.parse(
        "@x.route('/admin/secret')\n"
        "@login_required\n"
        "def view():\n"
        "    require_role('admin')\n"
        "    return 'x'\n")
    rows = route_inventory.analyze_source(tree)
    assert rows[0]["login"] is True
    assert rows[0]["roles"] == ["admin"]


def test_real_tree_has_no_ungated_sensitive_routes():
    import route_inventory
    assert route_inventory.main(["--flags-only"]) == 0


# --- readiness aggregator ---

def test_readiness_runs_clean_locally(capsys):
    assert production_readiness.main() == 0
    out = capsys.readouterr().out
    for section in ("files", "pins", "migrations", "entrypoints",
                    "secret-hygiene", "launch-config"):
        assert section in out
    assert "BLOCKED" in out
