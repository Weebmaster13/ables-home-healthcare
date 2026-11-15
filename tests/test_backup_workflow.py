"""Tests for the real-backup workflow: checklist, restore guard, inventory."""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backup_checklist
import backup_inventory
import restore_verify
from test_core_features import create_user, login

from app import app
from extensions import db


def _sqlite_copy(path, tables=("user", "inquiry"), rows=3):
    conn = sqlite3.connect(path)
    with conn:
        for table in tables:
            conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
            conn.executemany(f'INSERT INTO "{table}" (id) VALUES (?)',
                             [(i,) for i in range(rows)])
    return path


# --- admin backup button degrades safely on MySQL-like hosts ---

def test_admin_backup_button_guides_when_no_local_db(monkeypatch):
    import routes
    app.config["WTF_CSRF_ENABLED"] = False
    monkeypatch.setattr(routes, "create_backup",
                        lambda **kw: (_ for _ in ()).throw(
                            FileNotFoundError("no db file")))
    with app.app_context():
        admin, password = create_user("admin")
    try:
        with app.test_client() as client:
            assert login(client, admin, password).status_code == 302
            response = client.post("/admin/backups/create",
                                   follow_redirects=False)
            assert response.status_code == 302  # guidance, never a 500
            page = client.get("/admin/backups")
            assert b"cPanel" in page.data
    finally:
        with app.app_context():
            from models import User
            User.query.filter_by(id=admin.id).delete()
            db.session.commit()


# --- checklist template ---

def test_checklist_template_is_unverified_and_secret_free(tmp_path):
    out = tmp_path / "check.json"
    assert backup_checklist.main([str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["status"] == "unverified"
    assert len(data["database"]["tables"]) == 15
    assert "SECRET_KEY" in data["env_names_present_in_cpanel"]
    assert "DATABASE_URL" in data["env_names_present_in_cpanel"]
    blob = out.read_text()
    assert "topsecret" not in blob and "s3cret" not in blob


def test_record_counts_accepts_disposable_sqlite_only(tmp_path):
    out = tmp_path / "check.json"
    backup_checklist.main([str(out)])
    copy = _sqlite_copy(str(tmp_path / "copy.db"))
    assert backup_checklist.main(
        [str(out), "--record-counts", f"sqlite:///{copy}"]) == 0
    data = json.loads(out.read_text())
    assert data["database"]["tables"]["user"]["count"] == 3
    assert data["status"] == "incomplete"  # other tables absent
    # full-schema copy => counts-recorded, still explicitly unverified
    from sqlalchemy import create_engine
    full = tmp_path / "full.db"
    engine = create_engine(f"sqlite:///{full}")
    db.Model.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(full)
    with conn:
        conn.execute("CREATE TABLE alembic_version "
                     "(version_num VARCHAR(64) PRIMARY KEY)")
    assert backup_checklist.main(
        [str(out), "--record-counts", f"sqlite:///{full}"]) == 0
    data = json.loads(out.read_text())
    assert data["status"] == "counts-recorded-unverified"
    # refusals
    assert backup_checklist.main(
        [str(out), "--record-counts",
         "mysql+pymysql://u:p@h/db"]) == 1


def test_record_counts_refuses_suspicious_table_names(tmp_path):
    out = tmp_path / "check.json"
    backup_checklist.main([str(out)])
    data = json.loads(out.read_text())
    data["database"]["tables"]["weird-table"] = {"count": None}
    out.write_text(json.dumps(data))
    copy = tmp_path / "copy.db"
    conn = sqlite3.connect(copy)
    with conn:
        conn.execute('CREATE TABLE "weird-table" (id INTEGER)')
    assert backup_checklist.main(
        [str(out), "--record-counts", f"sqlite:///{copy}"]) == 1


def test_record_counts_refuses_repo_inside_paths(tmp_path):
    import config as app_config
    out = tmp_path / "check.json"
    backup_checklist.main([str(out)])
    repo_db = Path(app_config.BASE_DIR) / "instance" / "x.db"
    assert backup_checklist.main(
        [str(out), "--record-counts", f"sqlite:///{repo_db}"]) == 1


# --- restore verification guards ---

def _stage(tmp_path):
    from sqlalchemy import create_engine
    src = tmp_path / "stage"
    (src / "uploads").mkdir(parents=True)
    (src / "uploads" / "doc.pdf").write_bytes(b"%PDF-fake")
    dbfile = src / "app.db"
    engine = create_engine(f"sqlite:///{dbfile}")
    db.Model.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(dbfile)
    with conn:
        conn.execute("CREATE TABLE alembic_version "
                     "(version_num VARCHAR(64) PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version VALUES ('7b2f4d31a9ce')")
    # Zero-row app tables: counts of 0 still exercise the match path.
    manifest = tmp_path / "m.json"
    assert backup_inventory.main([str(manifest), str(src)]) == 0
    check = tmp_path / "check.json"
    assert backup_checklist.main([str(check)]) == 0
    assert backup_checklist.main(
        [str(check), "--record-counts", f"sqlite:///{dbfile}"]) == 0
    data = json.loads(check.read_text())
    assert data["status"] == "counts-recorded-unverified"
    return manifest, check, src


def test_restore_verify_happy_path_and_tamper(tmp_path):
    manifest, check, src = _stage(tmp_path)
    ok = [str(manifest), "--dir", str(src), "--checklist", str(check),
          "--db", f"sqlite:///{src / 'app.db'}", "--confirm-disposable"]
    assert restore_verify.main(ok) == 0
    (src / "uploads" / "doc.pdf").write_bytes(b"tampered")
    assert restore_verify.main(ok) == 1


def test_restore_verify_refusals(tmp_path):
    manifest, check, src = _stage(tmp_path)
    base = [str(manifest), "--dir", str(src)]
    assert restore_verify.main(base) == 2  # no confirmation flag
    import config as app_config
    assert restore_verify.main(
        [str(manifest), "--dir", app_config.BASE_DIR,
         "--confirm-disposable"]) == 1  # inside repo
    fake_prod = tmp_path / "live"
    fake_prod.mkdir()
    (fake_prod / "passenger_wsgi.py").write_text("x")
    assert restore_verify.main(
        [str(manifest), "--dir", str(fake_prod),
         "--confirm-disposable"]) == 1  # app-root marker
    assert restore_verify.main(
        base + ["--db", "mysql+pymysql://u:p@h/db",
                "--confirm-disposable"]) == 1  # non-sqlite db
    # checklist without recorded counts => INCOMPLETE, never OK
    fresh = tmp_path / "fresh.json"
    backup_checklist.main([str(fresh)])
    assert restore_verify.main(
        [str(manifest), "--dir", str(src), "--checklist", str(fresh),
         "--confirm-disposable"]) == 1


# --- inventory symlink + secret handling ---

def test_inventory_skips_symlinks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.txt").write_bytes(b"data")
    link = src / "link.txt"
    try:
        os.symlink(src / "real.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    manifest, error = backup_inventory.inventory([str(src)])
    assert error is None
    assert manifest["skipped_symlinks_count"] == 1
    assert [f["path"] for f in manifest["files"]] == ["real.txt"]


def test_manifest_rejects_unsafe_and_malformed_entries(tmp_path):
    import verify_backup_manifest
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.txt").write_bytes(b"ok")
    manifest = {"files": [
        {"path": "ok.txt", "bytes": 2,
         "sha256": verify_backup_manifest.sha256_of(src / "ok.txt")},
        {"path": "../escape.txt", "bytes": 2, "sha256": "x" * 64},
        {"path": "/abs/path.txt", "bytes": 2, "sha256": "x" * 64},
        {"path": "nohash.txt"},
        "not-a-dict",
    ]}
    results = dict((m, lv) for lv, m in
                   verify_backup_manifest.verify(manifest, src))
    assert results["OK ok.txt"] == "PASS"
    assert any("UNSAFE" in m and lv == "FAIL" for m, lv in results.items())
    assert any("MALFORMED" in m and lv == "FAIL"
               for m, lv in results.items())


def test_restore_verify_rejects_broken_checklist(tmp_path):
    manifest, _, src = _stage(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert restore_verify.main(
        [str(manifest), "--dir", str(src), "--checklist", str(broken),
         "--confirm-disposable"]) == 1
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"hello": 1}')
    assert restore_verify.main(
        [str(manifest), "--dir", str(src), "--checklist", str(wrong),
         "--confirm-disposable"]) == 1
