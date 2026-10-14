"""Tests for the read-only cPanel preflight check."""
import os
import subprocess
import sys
import tempfile

import preflight_cpanel


def test_mask_url_strips_password():
    assert preflight_cpanel.mask_url("") == "(missing)"
    shown = preflight_cpanel.mask_url("mysql+pymysql://u:s3cret@h:3306/db")
    assert "s3cret" not in shown
    assert "u:***@" in shown and "/db" in shown


def test_mask_url_leaves_passwordless_urls_intact():
    shown = preflight_cpanel.mask_url("sqlite:///tmp/x.db")
    assert shown == "sqlite:///tmp/x.db"


def test_missing_secret_key_blocks_preflight(monkeypatch, capsys):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    code = preflight_cpanel.main()
    out = capsys.readouterr().out
    assert code == 1
    assert "SECRET_KEY" in out
    assert "32" in out


def test_full_preflight_passes_on_fresh_sqlite(monkeypatch, tmp_path, capsys):
    db_file = tmp_path / "preflight.db"
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    code = preflight_cpanel.main()
    out = capsys.readouterr().out
    assert code == 0
    # fresh DB has no migrations: reported as WARN, never upgraded
    assert "WARN" in out
    assert "SECRET_KEY" not in out.replace("SECRET_KEY present", "")
