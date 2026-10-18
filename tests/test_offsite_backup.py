"""Tests for the off-site backup transfer layer.

Most tests use a ``FileDestination`` (a deterministic local stand-in) and an
injected command runner for the SFTP transport, so they need no external service
and no real credentials. A single optional live test exercises a real SFTP
server when ``OFFSITE_SFTP_TEST_URL`` points at one (the CI drill does).

The theme throughout: a failure must never be reported as a successful recovery
point, and a credential must never surface in a message.
"""
import json
import os
from pathlib import Path

import pytest

import backup_db
import offsite_backup
from offsite_backup import FileDestination, OffsiteError, SftpDestination


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("BACKUP_FINGERPRINT_KEY", "test-fingerprint-key")
    monkeypatch.delenv("OFFSITE_BACKUP_URL", raising=False)
    return "test-fingerprint-key"


def _make_dump(tmp_path, name="ables-postgres-20260101-000000.dump", payload=b"PGDMP data"):
    dump = tmp_path / name
    dump.write_bytes(payload)
    backup_db._write_fingerprint(dump, {"patients": {"rows": 1, "sha256": "abc"}})
    return dump


def _make_bundle(tmp_path, name="ablehome-full-20260101-000000.zip"):
    import zipfile

    bundle = tmp_path / name
    fingerprint = backup_db._fingerprint_payload({"patients": {"rows": 1, "sha256": "abc"}})
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("database.dump", b"PGDMP data")
        archive.writestr("database.fingerprint.json", json.dumps(fingerprint))
        archive.writestr("uploads/note.txt", "hello")
    return bundle


# --------------------------------------------------------------------------- #
# Destination parsing
# --------------------------------------------------------------------------- #
def test_bare_path_is_a_file_destination(tmp_path):
    dest = offsite_backup.parse_destination(str(tmp_path / "store"))
    assert isinstance(dest, FileDestination)
    assert dest.describe()["off_site"] is False


def test_sftp_url_is_off_site_and_strips_driver():
    dest = offsite_backup.parse_destination("sftp://backup@example.net:2222/var/backups")
    assert isinstance(dest, SftpDestination)
    assert dest.host == "example.net"
    assert dest.port == 2222
    assert dest.user == "backup"
    assert dest.describe()["off_site"] is True


def test_sftp_url_with_password_is_refused():
    with pytest.raises(OffsiteError) as exc:
        offsite_backup.parse_destination("sftp://user:secretpw@host/backups")
    message = str(exc.value)
    assert "password" in message
    assert "secretpw" not in message, "the password must not appear in the error"


def test_unsupported_scheme_is_refused():
    with pytest.raises(OffsiteError):
        offsite_backup.parse_destination("ftp://host/backups")


def test_missing_destination_is_refused(monkeypatch):
    monkeypatch.delenv("OFFSITE_BACKUP_URL", raising=False)
    with pytest.raises(OffsiteError):
        offsite_backup.parse_destination("")


def test_describe_destination_never_raises(monkeypatch):
    monkeypatch.delenv("OFFSITE_BACKUP_URL", raising=False)
    described = offsite_backup.describe_destination()
    assert described["kind"] == "none"
    assert described["off_site"] is False


# --------------------------------------------------------------------------- #
# Recovery set pairing
# --------------------------------------------------------------------------- #
def test_raw_dump_travels_with_its_sidecar(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    pairs = offsite_backup.recovery_set(dump)
    names = {remote for _, remote in pairs}
    assert dump.name in names
    assert dump.name + offsite_backup.FINGERPRINT_SUFFIX in names


def test_bundle_carries_its_embedded_fingerprint(tmp_path, keyed):
    bundle = _make_bundle(tmp_path)
    pairs = offsite_backup.recovery_set(bundle)
    # A zip already contains the fingerprint; only the bundle itself is separate.
    assert [remote for _, remote in pairs] == [bundle.name]


def test_dump_without_sidecar_is_refused(tmp_path, keyed):
    dump = tmp_path / "ables-postgres-20260101-000000.dump"
    dump.write_bytes(b"PGDMP no fingerprint")
    with pytest.raises(OffsiteError):
        offsite_backup.recovery_set(dump)


def test_bundle_without_fingerprint_member_is_refused(tmp_path, keyed):
    import zipfile

    bundle = tmp_path / "ablehome-full-20260101-000000.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("database.dump", b"PGDMP data")
    with pytest.raises(OffsiteError):
        offsite_backup.recovery_set(bundle)


def test_tampered_sidecar_is_refused_before_upload(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    sidecar = Path(str(dump) + offsite_backup.FINGERPRINT_SUFFIX)
    payload = json.loads(sidecar.read_text())
    payload["fingerprint"]["patients"]["sha256"] = "0" * 64
    sidecar.write_text(json.dumps(payload))
    with pytest.raises(backup_db.FingerprintIntegrityError):
        offsite_backup.recovery_set(dump)


def test_manifest_records_artifact_and_fingerprint(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    pairs = offsite_backup.recovery_set(dump)
    manifest = offsite_backup.build_manifest(dump, pairs)
    assert manifest["artifact"]["name"] == dump.name
    assert manifest["artifact"]["sha256"] == offsite_backup._sha256_file(dump)
    assert manifest["fingerprint"]["name"] == dump.name + offsite_backup.FINGERPRINT_SUFFIX
    assert manifest["fingerprint"]["authenticated"] is True
    assert manifest["database"]["backend"] == "postgresql"


def test_manifest_contains_no_secrets(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    pairs = offsite_backup.recovery_set(dump)
    text = json.dumps(offsite_backup.build_manifest(dump, pairs))
    assert "test-fingerprint-key" not in text
    assert "BACKUP_FINGERPRINT_KEY" not in text


# --------------------------------------------------------------------------- #
# Fingerprint signing key preservation
# --------------------------------------------------------------------------- #
def test_key_id_is_stable_and_never_the_key(keyed):
    first = backup_db.fingerprint_key_id()
    second = backup_db.fingerprint_key_id()
    assert first == second
    assert first is not None
    assert "test-fingerprint-key" not in first
    assert len(first) == 16


def test_key_id_changes_with_the_key(monkeypatch):
    monkeypatch.setenv("BACKUP_FINGERPRINT_KEY", "key-one")
    one = backup_db.fingerprint_key_id()
    monkeypatch.setenv("BACKUP_FINGERPRINT_KEY", "key-two")
    two = backup_db.fingerprint_key_id()
    assert one != two


def test_key_id_is_none_without_any_key(monkeypatch):
    monkeypatch.delenv("BACKUP_FINGERPRINT_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert backup_db.fingerprint_key_id() is None


def test_manifest_records_the_signing_key_id(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    pairs = offsite_backup.recovery_set(dump)
    manifest = offsite_backup.build_manifest(dump, pairs)
    assert manifest["fingerprint"]["key_id"] == backup_db.fingerprint_key_id()


def test_wrong_key_is_diagnosed_clearly(tmp_path, keyed, monkeypatch):
    dump = _make_dump(tmp_path)
    monkeypatch.setenv("BACKUP_FINGERPRINT_KEY", "the-wrong-key")
    with pytest.raises(backup_db.FingerprintIntegrityError):
        backup_db.verify_fingerprint_key_matches(dump)


def test_correct_key_confirms_a_backup(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    assert backup_db.verify_fingerprint_key_matches(dump) is True


def test_print_key_id_cli(capsys, keyed):
    backup_db.main(["--print-key-id"])
    output = capsys.readouterr().out
    assert "Fingerprint key ID:" in output
    assert backup_db.fingerprint_key_id() in output
    assert "test-fingerprint-key" not in output


def test_print_key_id_cli_fails_without_a_key(capsys, monkeypatch):
    monkeypatch.delenv("BACKUP_FINGERPRINT_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        backup_db.main(["--print-key-id"])
    assert exc.value.code == 1


# --------------------------------------------------------------------------- #
# Upload / verify / download (FileDestination)
# --------------------------------------------------------------------------- #
def test_upload_verifies_and_publishes_a_complete_set(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    dest = FileDestination(tmp_path / "remote")
    receipt = offsite_backup.upload_backup(dump, destination=dest)

    assert receipt["artifact"] == dump.name
    assert "SHA-256" in receipt["verification"]["message"]
    assert offsite_backup.is_complete_recovery_set(dest, dump.name)
    names = dest.list_names()
    assert dump.name + offsite_backup.MANIFEST_SUFFIX in names
    assert dump.name + offsite_backup.FINGERPRINT_SUFFIX in names


def test_upload_leaves_the_local_backup_intact(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    original = dump.read_bytes()
    offsite_backup.upload_backup(dump, destination=FileDestination(tmp_path / "remote"))
    assert dump.read_bytes() == original
    assert Path(str(dump) + offsite_backup.FINGERPRINT_SUFFIX).is_file()


def test_remote_verification_failure_is_reported_and_no_manifest_left(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    dest = FileDestination(tmp_path / "remote")

    real_put = dest.put_file

    def tampering_put(local_path, remote_name):
        result = real_put(local_path, remote_name)
        if remote_name == dump.name:
            # Corrupt the artifact after it lands, same size so a size-only
            # check would miss it.
            (dest.root / remote_name).write_bytes(b"X" * dump.stat().st_size)
        return result

    dest.put_file = tampering_put
    with pytest.raises(OffsiteError) as exc:
        offsite_backup.upload_backup(dump, destination=dest)
    assert "verification failed" in str(exc.value)
    assert dump.name + offsite_backup.MANIFEST_SUFFIX not in dest.list_names()


def test_size_only_verification_is_flagged_as_weaker(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    dest = FileDestination(tmp_path / "remote")
    receipt = offsite_backup.upload_backup(dump, destination=dest, verify="size")
    assert "not a full verification" in receipt["verification"]["message"]


def test_download_from_offsite_authenticates_the_fingerprint(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    dest = FileDestination(tmp_path / "remote")
    offsite_backup.upload_backup(dump, destination=dest)

    fetched = offsite_backup.download_backup(dump.name, destination=dest, dest_dir=tmp_path / "fetched")
    assert fetched.read_bytes() == dump.read_bytes()
    # The sidecar came along and authenticates.
    assert backup_db._read_fingerprint(fetched) == {"patients": {"rows": 1, "sha256": "abc"}}


def test_downloaded_copy_with_wrong_key_fails_closed(tmp_path, keyed, monkeypatch):
    dump = _make_dump(tmp_path)
    dest = FileDestination(tmp_path / "remote")
    offsite_backup.upload_backup(dump, destination=dest)
    fetched = offsite_backup.download_backup(dump.name, destination=dest, dest_dir=tmp_path / "fetched")

    monkeypatch.setenv("BACKUP_FINGERPRINT_KEY", "rotated-key")
    with pytest.raises(backup_db.FingerprintIntegrityError):
        backup_db._read_fingerprint(fetched)


def test_partial_transfer_is_not_a_recovery_point(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    dest = FileDestination(tmp_path / "remote")
    # Simulate an interrupted transfer: the artifact and fingerprint landed but
    # the manifest (written last) did not.
    dest.put_file(dump, dump.name)
    dest.put_file(Path(str(dump) + offsite_backup.FINGERPRINT_SUFFIX),
                  dump.name + offsite_backup.FINGERPRINT_SUFFIX)
    assert offsite_backup.is_complete_recovery_set(dest, dump.name) is False
    assert offsite_backup.list_remote_backups(destination=dest) == []


def test_retry_after_failure_succeeds(tmp_path, keyed):
    dump = _make_dump(tmp_path)
    dest = FileDestination(tmp_path / "remote")
    calls = {"n": 0}
    real_put = dest.put_file

    def flaky_put(local_path, remote_name):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OffsiteError("simulated network drop")
        return real_put(local_path, remote_name)

    dest.put_file = flaky_put
    with pytest.raises(OffsiteError):
        offsite_backup.upload_backup(dump, destination=dest)

    dest.put_file = real_put
    receipt = offsite_backup.upload_backup(dump, destination=dest)
    assert receipt["artifact"] == dump.name
    assert offsite_backup.is_complete_recovery_set(dest, dump.name)


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
def test_remote_retention_keeps_the_newest(tmp_path, keyed):
    dest = FileDestination(tmp_path / "remote")
    names = []
    for i in range(4):
        dump = _make_dump(tmp_path, name=f"ables-postgres-2026010{i}-000000.dump", payload=f"data{i}".encode())
        offsite_backup.upload_backup(dump, destination=dest)
        names.append(dump.name)

    complete = offsite_backup.list_remote_backups(destination=dest)
    assert set(complete) == set(names)

    offsite_backup.cleanup_remote_backups(destination=dest, keep=2)
    remaining = offsite_backup.list_remote_backups(destination=dest)
    assert len(remaining) == 2
    # The newest artifact must never be removed.
    newest = max(names)
    assert newest in remaining


def test_remote_retention_removes_sidecars_with_their_artifact(tmp_path, keyed):
    dest = FileDestination(tmp_path / "remote")
    for i in range(3):
        dump = _make_dump(tmp_path, name=f"ables-postgres-2026010{i}-000000.dump", payload=f"d{i}".encode())
        offsite_backup.upload_backup(dump, destination=dest)

    offsite_backup.cleanup_remote_backups(destination=dest, keep=1)
    remaining = set(dest.list_names())
    for name in list(remaining):
        if name.endswith(offsite_backup.FINGERPRINT_SUFFIX):
            artifact = name[: -len(offsite_backup.FINGERPRINT_SUFFIX)]
            assert artifact in remaining, "a fingerprint must not outlive its artifact"


def test_remote_retention_keep_at_least_one(tmp_path, keyed):
    dest = FileDestination(tmp_path / "remote")
    dump = _make_dump(tmp_path)
    offsite_backup.upload_backup(dump, destination=dest)
    # keep=0 must still never delete the only copy.
    offsite_backup.cleanup_remote_backups(destination=dest, keep=0)
    assert dump.name in dest.list_names()


# --------------------------------------------------------------------------- #
# SFTP transport (injected runner: no real server, no credentials)
# --------------------------------------------------------------------------- #
class _FakeSftp:
    """A protocol-faithful stand-in for OpenSSH sftp driven by batch commands.

    It models the real server closely enough to test the staging/rename and
    size-check logic: `put` writes a file, `rename` moves it, `ls -1`/`ls -l`
    report contents, and `rm` deletes. A configurable failure lets tests
    simulate a dropped connection mid-transfer.
    """

    def __init__(self, root, fail_on=None, truncate_put=False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.fail_on = fail_on
        self.truncate_put = truncate_put
        self.commands = []

    def __call__(self, argv, input_bytes=None, timeout=120):
        batch = Path(argv[argv.index("-b") + 1]).read_text().splitlines()
        self.commands.append(list(batch))
        out = []
        for line in batch:
            if self.fail_on and self.fail_on in line:
                return 1, "".join(out).encode(), b"Connection closed\r\n"
            parts = line.split()
            if not parts:
                continue
            cmd = parts[0]
            if cmd == "-mkdir":
                # The remote directory is the fake's root; ignore the path.
                pass
            elif cmd == "cd":
                pass
            elif cmd == "put":
                src = Path(parts[1].strip('"'))
                dst = self.root / parts[2].strip('"')
                data = src.read_bytes()
                if self.truncate_put:
                    data = data[: max(0, len(data) // 2)]
                dst.write_bytes(data)
            elif cmd == "rename":
                (self.root / parts[1].strip('"')).rename(self.root / parts[2].strip('"'))
            elif cmd == "get":
                src = self.root / parts[1].strip('"')
                dst = Path(parts[2].strip('"'))
                if not src.is_file():
                    return 1, "".join(out).encode(), b"File not found\r\n"
                dst.write_bytes(src.read_bytes())
            elif cmd == "ls":
                target = parts[-1].strip('"')
                if "-l" in parts:
                    entry = self.root / target
                    if entry.is_file():
                        out.append(f"-rw-r--r-- 1 u g {entry.stat().st_size} Jan 1 00:00 {target}\r")
                else:
                    out.append("sftp> ls -1\r")
                    for child in sorted(self.root.iterdir()):
                        out.append(f"{child.name}\r")
            elif cmd == "rm":
                (self.root / parts[1].strip('"')).unlink(missing_ok=True)
        return 0, "".join(out).encode(), b""


def _sftp_dest(tmp_path, runner):
    return SftpDestination(
        host="backup.example.net",
        remote_dir="/srv/ables-backups",
        user="ables",
        port=2222,
        identity_file="/keys/id_ables",
        known_hosts="/keys/known_hosts",
        runner=runner,
    )


def test_sftp_upload_stages_then_promotes(tmp_path, keyed):
    runner = _FakeSftp(tmp_path / "remote")
    dump = _make_dump(tmp_path)
    receipt = offsite_backup.upload_backup(dump, destination=_sftp_dest(tmp_path, runner))

    assert receipt["artifact"] == dump.name
    assert (runner.root / dump.name).is_file()
    # The partial name must never survive a successful transfer.
    assert not any(".partial" in n for n in (p.name for p in runner.root.iterdir()))
    # The put must have targeted a .partial name, then been renamed.
    puts = [line for batch in runner.commands for line in batch if line.startswith("put")]
    assert any(".partial" in line for line in puts)


def test_sftp_truncated_upload_is_refused(tmp_path, keyed):
    runner = _FakeSftp(tmp_path / "remote", truncate_put=True)
    dump = _make_dump(tmp_path)
    with pytest.raises(OffsiteError) as exc:
        offsite_backup.upload_backup(dump, destination=_sftp_dest(tmp_path, runner))
    assert "wrong size" in str(exc.value)
    assert not (runner.root / dump.name).exists(), "a truncated file must not be promoted"


def test_sftp_failed_upload_does_not_leave_a_recovery_point(tmp_path, keyed):
    runner = _FakeSftp(tmp_path / "remote", fail_on="put")
    dump = _make_dump(tmp_path)
    dest = _sftp_dest(tmp_path, runner)
    with pytest.raises(OffsiteError):
        offsite_backup.upload_backup(dump, destination=dest)
    assert offsite_backup.list_remote_backups(destination=dest) == []


def test_sftp_missing_client_is_reported(tmp_path, keyed, monkeypatch):
    monkeypatch.setattr(offsite_backup.shutil, "which", lambda name: None)
    dest = _sftp_dest(tmp_path, _FakeSftp(tmp_path / "remote"))
    dump = _make_dump(tmp_path)
    with pytest.raises(OffsiteError) as exc:
        offsite_backup.upload_backup(dump, destination=dest)
    assert "sftp client not found" in str(exc.value)


def test_sftp_error_never_leaks_credentials(tmp_path, keyed):
    runner = _FakeSftp(tmp_path / "remote", fail_on="put")
    dest = _sftp_dest(tmp_path, runner)
    dump = _make_dump(tmp_path)
    with pytest.raises(OffsiteError) as exc:
        offsite_backup.upload_backup(dump, destination=dest)
    text = str(exc.value)
    assert "/keys/id_ables" not in text
    assert "known_hosts" not in text


def test_redact_strips_passwords_and_key_paths():
    assert offsite_backup._redact("sftp://u:secret@h/x") == "sftp://u:***@h/x"
    assert "secret" not in offsite_backup._redact("sftp://u:secret@h/x")
    assert offsite_backup._redact("sftp -i /home/u/.ssh/id_x host") == "sftp -i <key> host"


def test_state_receipt_round_trips_without_secrets(tmp_path, keyed, monkeypatch):
    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path / "backups")
    dump = _make_dump(tmp_path)
    receipt = offsite_backup.upload_backup(dump, destination=FileDestination(tmp_path / "remote"))
    stored = offsite_backup.read_state()
    assert stored["artifact"] == receipt["artifact"]
    text = (tmp_path / "backups" / offsite_backup.STATE_FILENAME).read_text()
    assert "test-fingerprint-key" not in text


# --------------------------------------------------------------------------- #
# Optional live drill against a real SFTP server
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not os.environ.get("OFFSITE_SFTP_TEST_URL"),
    reason="set OFFSITE_SFTP_TEST_URL to run the live SFTP transfer drill",
)
def test_live_sftp_round_trip(tmp_path, keyed, monkeypatch):
    """A real encrypted transfer, verified and downloaded back.

    Requires OFFSITE_SFTP_TEST_URL (sftp://user@host:port/path),
    OFFSITE_BACKUP_KEY (identity file), and OFFSITE_SFTP_KNOWN_HOSTS. The CI
    drill sets these against a throwaway sshd; no external service is used.
    """
    monkeypatch.setenv("OFFSITE_BACKUP_URL", os.environ["OFFSITE_SFTP_TEST_URL"])
    dest = offsite_backup.parse_destination()
    dump = _make_dump(tmp_path)
    receipt = offsite_backup.upload_backup(dump, destination=dest)
    assert "SHA-256" in receipt["verification"]["message"]

    fetched = offsite_backup.download_backup(dump.name, destination=dest, dest_dir=tmp_path / "fetched")
    assert fetched.read_bytes() == dump.read_bytes()
    assert backup_db._read_fingerprint(fetched) == {"patients": {"rows": 1, "sha256": "abc"}}

    dest.delete_names([dump.name, dump.name + offsite_backup.FINGERPRINT_SUFFIX,
                       dump.name + offsite_backup.MANIFEST_SUFFIX])


# --------------------------------------------------------------------------- #
# Readiness integration
# --------------------------------------------------------------------------- #
def test_off_site_readiness_requires_sftp_not_a_directory(tmp_path, keyed, monkeypatch):
    from production_check import offsite_destination_is_off_site

    assert offsite_destination_is_off_site("sftp://u@h/path") is True
    assert offsite_destination_is_off_site(str(tmp_path)) is False
    assert offsite_destination_is_off_site("file:///mnt/backups") is False
    assert offsite_destination_is_off_site("") is False


def test_production_check_flags_missing_off_site_destination():
    import production_check

    assert "Off-site backup destination" in [name for name, _, _ in production_check.CHECKS]


def test_launch_checklist_reports_offsite_not_ready_without_verified_upload(tmp_path, keyed, monkeypatch):
    from app import app

    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setenv("OFFSITE_BACKUP_URL", "sftp://u@h/path")
    with app.test_request_context():
        from routes import offsite_backup_status

        ready, detail = offsite_backup_status()
    assert ready is False
    assert "no verified upload" in detail


def test_launch_checklist_reports_offsite_ready_after_verified_upload(tmp_path, keyed, monkeypatch):
    from app import app

    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setenv("OFFSITE_BACKUP_URL", "sftp://u@h/path")
    dump = _make_dump(tmp_path)
    offsite_backup.upload_backup(dump, destination=FileDestination(tmp_path / "remote"))

    with app.test_request_context():
        from routes import offsite_backup_status

        ready, detail = offsite_backup_status()
    assert ready is True
    assert "Last verified off-site backup" in detail


def test_directory_destination_never_counts_as_off_site(tmp_path, keyed, monkeypatch):
    from app import app

    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setenv("OFFSITE_BACKUP_URL", str(tmp_path / "remote"))
    dump = _make_dump(tmp_path)
    offsite_backup.upload_backup(dump, destination=FileDestination(tmp_path / "remote"))

    with app.test_request_context():
        from routes import offsite_backup_status

        ready, detail = offsite_backup_status()
    assert ready is False
    assert "not off-site" in detail


# --------------------------------------------------------------------------- #
# CLI integration
# --------------------------------------------------------------------------- #
def test_cli_offsite_uploads_and_verifies(tmp_path, keyed, monkeypatch, capsys):
    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup_db, "_dispatch_backup", lambda url, include_uploads=True: _make_dump(tmp_path))
    monkeypatch.setenv("OFFSITE_BACKUP_URL", str(tmp_path / "remote"))
    monkeypatch.setenv("OFFSITE_RETENTION", "5")

    backup_db.main(["--offsite"])

    output = capsys.readouterr().out
    assert "Off-site backup verified" in output
    assert (tmp_path / "remote").is_dir()


def test_cli_offsite_failure_exits_nonzero(tmp_path, keyed, monkeypatch, capsys):
    monkeypatch.setattr(backup_db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(backup_db, "_dispatch_backup", lambda url, include_uploads=True: _make_dump(tmp_path))
    # A destination that refuses every write must make the run fail loudly.
    monkeypatch.setenv("OFFSITE_BACKUP_URL", "sftp://user:pw@host/path")

    with pytest.raises(SystemExit) as exc:
        backup_db.main(["--offsite"])
    assert exc.value.code == 1
    assert "ERROR" in capsys.readouterr().out
