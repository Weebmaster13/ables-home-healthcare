"""Provider-neutral off-site backup transfer.

A backup in the local ``backups/`` directory protects against a corrupted table,
not against losing the production host. This module copies a *finished* backup —
the database artifact, its authenticated fingerprint, and a non-secret manifest
— to a destination that survives loss of the production host, and refuses to
report success until the remote copy has been verified.

Two destinations ship here, both using tools already present on a cPanel host:

* ``sftp://`` — OpenSSH ``sftp`` with key-based authentication. The transport is
  encrypted and the destination is authenticated against a known-hosts file.
  This is the recommended production destination (a second host, a NAS, or a
  storage account that accepts SFTP). Only an ``sftp://`` destination counts as
  off-site for the production readiness check.
* ``file://`` — a path. This is a mounted remote volume, or a deterministic
  local stand-in for tests. A plain directory on the same host is *not* an
  off-site backup, and this module never claims otherwise.

Invariants callers can rely on:

* The database artifact and its fingerprint always travel together, and the
  fingerprint is written to the destination only after the artifact lands.
* Nothing is reported as uploaded until the remote file's size and SHA-256 match
  the local file.
* A transfer is staged under a ``.partial`` name and renamed only once complete,
  so an interrupted upload is never mistaken for a recovery point.
* Destination credentials come from the environment or a key file and are never
  printed, logged, or embedded in an exception.
* Remote retention never removes the newest copy, so the last known-good
  recovery point cannot be deleted to make room for a new one.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import uuid

BASE_DIR = Path(__file__).resolve().parent

# Reuse the backup module's naming so a listed name is recognisably a backup.
BACKUP_PREFIXES = ("ablehome-", "ablehome-full-", "ables-postgres-", "ables-mysql-")
BACKUP_SUFFIXES = (".zip", ".dump", ".sql", ".db")

MANIFEST_SUFFIX = ".manifest.json"
FINGERPRINT_SUFFIX = ".fingerprint.json"
STATE_FILENAME = "offsite-state.json"


class OffsiteError(RuntimeError):
    """An off-site operation failed in a way the caller must not treat as success."""


def _redact(text):
    """Strip anything that looks like a credential before it can reach a log.

    Applied to every message that could include a destination URL or a command
    line, so a password in a URL or a key path in an argument never surfaces.
    """
    text = str(text)
    # user:password@host -> user:***@host
    text = re.sub(r"://([^/:@\s]+):[^@/\s]+@", r"://\1:***@", text)
    # -i /path/to/key -> -i <key>
    text = re.sub(r"(?<=-i )[^\s]+", "<key>", text)
    text = re.sub(r"(?<=--identity=)[^\s]+", "<key>", text)
    return text


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backend_for(name):
    """Best-effort backend/tool label from a backup filename (never a secret)."""
    name = Path(name).name
    if name.startswith("ables-postgres-") or name.endswith(".dump"):
        return {"backend": "postgresql", "tool": "pg_dump"}
    if name.startswith("ables-mysql-") or name.endswith(".sql"):
        return {"backend": "mysql", "tool": "mysqldump"}
    return {"backend": "sqlite", "tool": "sqlite-copy"}


# --------------------------------------------------------------------------- #
# Destinations
# --------------------------------------------------------------------------- #
class Destination:
    """A place a recovery set can be written to and read back from."""

    kind = "abstract"

    def put_file(self, local_path, remote_name):
        raise NotImplementedError

    def get_file(self, remote_name, local_path):
        raise NotImplementedError

    def list_names(self):
        raise NotImplementedError

    def delete_names(self, names):
        raise NotImplementedError

    def size_of(self, remote_name):
        raise NotImplementedError

    def describe(self):
        raise NotImplementedError


class FileDestination(Destination):
    """A directory. A mounted remote volume, or a deterministic local stand-in.

    This is deliberately *not* treated as off-site by the readiness check: a
    directory on the production host does not survive losing that host. It is
    useful for tests and for a mounted network/remote volume, where the caller
    has arranged durability out of band.
    """

    kind = "file"

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, local_path, remote_name):
        local_path = Path(local_path)
        final = self.root / remote_name
        # Stage under a partial name, then rename, so an interrupted copy never
        # leaves a half-written file under the real name.
        partial = self.root / f"{remote_name}.partial-{uuid.uuid4().hex[:8]}"
        try:
            shutil.copyfile(local_path, partial)
            if partial.stat().st_size != local_path.stat().st_size:
                raise OffsiteError(f"staged copy of {remote_name} has the wrong size")
            os.replace(partial, final)
        finally:
            partial.unlink(missing_ok=True)
        return final

    def get_file(self, remote_name, local_path):
        source = self.root / remote_name
        if not source.is_file():
            raise OffsiteError(f"remote file not found: {remote_name}")
        shutil.copyfile(source, local_path)
        return Path(local_path)

    def list_names(self):
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_file())

    def delete_names(self, names):
        removed = []
        for name in names:
            target = self.root / name
            if target.is_file():
                target.unlink()
                removed.append(name)
        return removed

    def size_of(self, remote_name):
        target = self.root / remote_name
        if not target.is_file():
            raise OffsiteError(f"remote file not found: {remote_name}")
        return target.stat().st_size

    def describe(self):
        return {
            "kind": "file",
            "location": str(self.root),
            "off_site": False,
            "note": (
                "A directory is only off-site if it is a mounted remote volume; "
                "a folder on the production host does not survive losing the host."
            ),
        }


def _default_runner(argv, input_bytes=None, timeout=120):
    """Run a command, returning ``(returncode, stdout_bytes, stderr_bytes)``."""
    result = subprocess.run(
        argv,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


class SftpDestination(Destination):
    """OpenSSH ``sftp`` with key-based auth and a pinned host key.

    Only a key is accepted; a password in the URL is rejected rather than used,
    because it would be exposed in the process list and in error output. The host
    key is verified against a known-hosts file, so the destination is
    authenticated and the transfer is encrypted.
    """

    kind = "sftp"

    def __init__(self, host, remote_dir, user=None, port=22, identity_file=None,
                 known_hosts=None, runner=None):
        if not host:
            raise OffsiteError("sftp destination is missing a host")
        self.host = host
        self.user = user
        self.port = int(port)
        self.remote_dir = "/" + (remote_dir or "").strip("/") if remote_dir else "."
        if self.remote_dir == "/":
            self.remote_dir = "."
        self.identity_file = identity_file
        self.known_hosts = known_hosts or os.environ.get("OFFSITE_SFTP_KNOWN_HOSTS")
        self._runner = runner or _default_runner

    def _target(self):
        return f"{self.user}@{self.host}" if self.user else self.host

    def _authority(self):
        authority = self._target()
        if self.port and self.port != 22:
            authority += f":{self.port}"
        return authority

    def _argv(self, batch_path):
        argv = [
            "sftp",
            "-b", str(batch_path),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-P", str(self.port),
        ]
        if self.identity_file:
            argv += ["-i", str(self.identity_file)]
        if self.known_hosts:
            argv += ["-o", f"UserKnownHostsFile={self.known_hosts}"]
        argv += [self._target()]
        return argv

    def _run_batch(self, lines):
        if shutil.which("sftp") is None:
            raise OffsiteError("sftp client not found on PATH; install openssh-client")
        with tempfile.NamedTemporaryFile("w", suffix=".sftp", delete=False) as handle:
            handle.write("\n".join(lines) + "\n")
            batch_path = handle.name
        try:
            rc, out, err = self._runner(self._argv(batch_path))
        except subprocess.TimeoutExpired as exc:
            raise OffsiteError(f"sftp to {self.host} timed out") from exc
        finally:
            Path(batch_path).unlink(missing_ok=True)
        if rc != 0:
            # Redact before surfacing: stderr can echo the command line.
            detail = _redact(err.decode("utf-8", "replace").strip()) or f"exit {rc}"
            raise OffsiteError(f"sftp to {self.host} failed: {detail}")
        return out.decode("utf-8", "replace")

    def _quote(self, name):
        return '"' + str(name).replace('"', "") + '"'

    def put_file(self, local_path, remote_name):
        local_path = Path(local_path)
        partial = f"{remote_name}.partial"
        self._run_batch([
            f"-mkdir {self._quote(self.remote_dir)}",
            f"cd {self._quote(self.remote_dir)}",
            f"put {self._quote(str(local_path))} {self._quote(partial)}",
        ])
        # Confirm the staged size before promoting it, so a truncated transfer
        # is not renamed into place as if it were complete.
        if self.size_of(partial) != local_path.stat().st_size:
            self.delete_names([partial])
            raise OffsiteError(f"staged upload of {remote_name} has the wrong size")
        self._run_batch([
            f"cd {self._quote(self.remote_dir)}",
            f"rename {self._quote(partial)} {self._quote(remote_name)}",
        ])
        return remote_name

    def get_file(self, remote_name, local_path):
        self._run_batch([
            f"cd {self._quote(self.remote_dir)}",
            f"get {self._quote(remote_name)} {self._quote(str(local_path))}",
        ])
        return Path(local_path)

    def list_names(self):
        # `ls -1` echoes each command as "sftp> ..." before its output, so filter
        # those echoes and keep only plausible filenames. Anything else is
        # skipped rather than guessed at, so an odd listing cannot turn a
        # non-existent file into a "complete" recovery set.
        output = self._run_batch([
            f"cd {self._quote(self.remote_dir)}",
            "ls -1",
        ])
        names = []
        for line in output.splitlines():
            entry = line.strip()
            if not entry or entry.startswith("sftp>"):
                continue
            if entry in (".", "..") or "/" in entry or entry.endswith(":"):
                continue
            names.append(entry)
        return sorted(set(names))

    def delete_names(self, names):
        if not names:
            return []
        lines = [f"cd {self._quote(self.remote_dir)}"]
        lines += [f"rm {self._quote(name)}" for name in names]
        self._run_batch(lines)
        return list(names)

    def size_of(self, remote_name):
        output = self._run_batch([
            f"cd {self._quote(self.remote_dir)}",
            f"ls -l {self._quote(remote_name)}",
        ])
        for line in output.splitlines():
            parts = line.split()
            # `ls -l` output: perms links owner group SIZE date... name
            if len(parts) >= 5 and parts[0].startswith("-") and parts[4].isdigit():
                return int(parts[4])
        raise OffsiteError(f"could not read the size of {remote_name} on {self.host}")

    def describe(self):
        return {
            "kind": "sftp",
            "location": _redact(f"sftp://{self._authority()}{self.remote_dir if self.remote_dir.startswith('/') else '/' + self.remote_dir}"),
            "off_site": True,
            "note": "Encrypted transport with a pinned host key.",
        }


def parse_destination(spec=None, runner=None):
    """Build a :class:`Destination` from an ``OFFSITE_BACKUP_URL`` style spec.

    ``sftp://user@host[:port]/path`` uses OpenSSH sftp; the identity file comes
    from ``OFFSITE_BACKUP_KEY`` and the known-hosts file from
    ``OFFSITE_SFTP_KNOWN_HOSTS``. ``file:///path`` (or a bare path) is a
    directory. A password in an ``sftp://`` URL is refused, not used.
    """
    spec = (spec if spec is not None else os.environ.get("OFFSITE_BACKUP_URL", "")).strip()
    if not spec:
        raise OffsiteError(
            "no off-site destination configured; set OFFSITE_BACKUP_URL "
            "(for example sftp://user@backup-host/home/user/ables-backups)"
        )

    if "://" not in spec:
        return FileDestination(spec)

    parsed = urllib.parse.urlsplit(spec)
    scheme = parsed.scheme.lower()

    if scheme == "file":
        return FileDestination(urllib.parse.unquote(parsed.path))

    if scheme == "sftp":
        if parsed.password is not None:
            raise OffsiteError(
                "refusing an sftp URL that carries a password; use a key file "
                "(OFFSITE_BACKUP_KEY) so the credential never appears in a "
                "command line or a log"
            )
        identity = os.environ.get("OFFSITE_BACKUP_KEY") or None
        return SftpDestination(
            host=parsed.hostname,
            remote_dir=urllib.parse.unquote(parsed.path),
            user=parsed.username,
            port=parsed.port or 22,
            identity_file=identity,
            runner=runner,
        )

    raise OffsiteError(f"unsupported off-site destination scheme: {scheme!r}")


def describe_destination(spec=None):
    """Non-secret description of the configured destination (never credentials)."""
    try:
        return parse_destination(spec).describe()
    except OffsiteError as exc:
        return {"kind": "none", "location": "", "off_site": False, "note": str(exc)}


# --------------------------------------------------------------------------- #
# Recovery set: artifact + fingerprint + manifest
# --------------------------------------------------------------------------- #
def _embedded_fingerprint_bytes(backup_path):
    """Return the raw fingerprint member bytes from a ZIP backup, or ``None``."""
    import zipfile

    with zipfile.ZipFile(backup_path) as archive:
        if "database.fingerprint.json" not in archive.namelist():
            return None
        return archive.read("database.fingerprint.json")


def recovery_set(backup_path):
    """Return ``[(local_path, remote_name), ...]`` for one backup.

    The database artifact and its fingerprint travel together. A ZIP bundle
    already carries its fingerprint, so only the bundle and its manifest are
    separate files; a raw ``.dump``/``.sql`` needs its sidecar beside it. A
    backup with no fingerprint at all is refused: publishing it as a recovery
    point would create a backup that cannot be content-verified after a disaster.
    """
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise OffsiteError(f"backup file not found: {backup_path}")

    import backup_db

    pairs = [(backup_path, backup_path.name)]

    if backup_path.suffix == ".zip":
        if _embedded_fingerprint_bytes(backup_path) is None:
            raise OffsiteError(
                f"{backup_path.name} has no embedded fingerprint; refusing to "
                "publish it as a recovery point"
            )
    elif backup_path.suffix in (".dump", ".sql"):
        sidecar = Path(str(backup_path) + FINGERPRINT_SUFFIX)
        if not sidecar.is_file():
            raise OffsiteError(
                f"{backup_path.name} has no fingerprint sidecar; refusing to "
                "publish it as a recovery point"
            )
        # Authenticate it now so an unsigned/tampered fingerprint never leaves
        # the host under the guise of a verified backup.
        backup_db._read_fingerprint(backup_path)
        pairs.append((sidecar, sidecar.name))
    else:
        # SQLite copies are development-only and carry no fingerprint; record
        # that honestly in the manifest rather than implying verification.
        pass

    return pairs


def build_manifest(backup_path, pairs):
    """A non-secret record of what a recovery set contains and how it was checked."""
    backup_path = Path(backup_path)
    import backup_db

    fingerprint = {"name": None, "sha256": None, "authenticated": False, "key_id": None}
    if backup_path.suffix == ".zip":
        member = _embedded_fingerprint_bytes(backup_path)
        if member is not None:
            fingerprint = {
                "name": "database.fingerprint.json (embedded)",
                "sha256": hashlib.sha256(member).hexdigest(),
                "authenticated": backup_db._fingerprint_key() is not None,
                "key_id": backup_db.fingerprint_key_id(),
            }
    elif backup_path.suffix in (".dump", ".sql"):
        sidecar = Path(str(backup_path) + FINGERPRINT_SUFFIX)
        fingerprint = {
            "name": sidecar.name,
            "sha256": _sha256_file(sidecar),
            "authenticated": backup_db._fingerprint_key() is not None,
            "key_id": backup_db.fingerprint_key_id(),
        }

    manifest = {
        "version": 1,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifact": {
            "name": backup_path.name,
            "size": backup_path.stat().st_size,
            "sha256": _sha256_file(backup_path),
        },
        "fingerprint": fingerprint,
        "members": [name for _, name in pairs],
        "database": _backend_for(backup_path.name),
    }
    return manifest


def _recovery_set_with_manifest(backup_path):
    pairs = recovery_set(backup_path)
    manifest = build_manifest(backup_path, pairs)
    manifest_name = Path(backup_path).name + MANIFEST_SUFFIX
    return pairs, manifest, manifest_name


def is_complete_recovery_set(destination, artifact_name):
    """True when the artifact, its fingerprint, and its manifest are all present.

    Presence only: an interrupted transfer leaves the manifest absent, so a
    half-published set is never treated as a recovery point. Content is checked
    separately by :func:`verify_remote_backup`.
    """
    names = set(destination.list_names())
    if artifact_name not in names:
        return False
    if artifact_name + MANIFEST_SUFFIX not in names:
        return False
    if artifact_name.endswith(".zip"):
        return True
    return artifact_name + FINGERPRINT_SUFFIX in names


# --------------------------------------------------------------------------- #
# Upload / verify / download
# --------------------------------------------------------------------------- #
def verify_remote_backup(backup_path, destination=None, verify="hash"):
    """Confirm the remote copy matches the local backup.

    ``verify="hash"`` (the default) downloads the remote artifact and compares
    its SHA-256 with the local file, which is the only check that actually
    proves the bytes survived the transfer. ``verify="size"`` compares sizes
    only; it is weaker and is reported as such, never as a full verification.
    """
    destination = destination or parse_destination()
    backup_path = Path(backup_path)
    local_size = backup_path.stat().st_size
    remote_size = destination.size_of(backup_path.name)
    if remote_size != local_size:
        return False, f"remote size {remote_size} does not match local {local_size}"

    if verify == "size":
        return True, "remote size matches (size-only check; not a full verification)"

    with tempfile.TemporaryDirectory() as tmpdir:
        fetched = Path(tmpdir) / backup_path.name
        destination.get_file(backup_path.name, fetched)
        remote_hash = _sha256_file(fetched)
    local_hash = _sha256_file(backup_path)
    if remote_hash != local_hash:
        return False, "remote content does not match the local backup (SHA-256 differs)"
    return True, "remote content matches the local backup (SHA-256 verified)"


def upload_backup(backup_path, destination=None, verify="hash", keep=None):
    """Copy a backup and its fingerprint off-site and verify the remote copy.

    Returns a receipt dict. Raises :class:`OffsiteError` on any failure, so the
    caller can never mistake a partial or unverified transfer for a successful
    off-site backup. The local backup is left untouched regardless of outcome.
    """
    destination = destination or parse_destination()
    backup_path = Path(backup_path)
    pairs, manifest, manifest_name = _recovery_set_with_manifest(backup_path)

    # Artifact and fingerprint first, manifest last: the manifest is the marker
    # that the set is complete, so it must never appear before its contents.
    for local_path, remote_name in pairs:
        destination.put_file(local_path, remote_name)
    with tempfile.TemporaryDirectory() as manifest_dir:
        manifest_path = Path(manifest_dir) / manifest_name
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        destination.put_file(manifest_path, manifest_name)

    ok, message = verify_remote_backup(backup_path, destination=destination, verify=verify)
    if not ok:
        # Do not leave a manifest claiming success over an unverified artifact.
        destination.delete_names([manifest_name])
        raise OffsiteError(f"remote verification failed: {message}")

    receipt = {
        "artifact": backup_path.name,
        "sha256": manifest["artifact"]["sha256"],
        "size": manifest["artifact"]["size"],
        "destination": destination.describe(),
        "verification": {"method": verify, "message": message},
        "uploaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_state(receipt)

    if keep:
        cleanup_remote_backups(destination=destination, keep=keep)
    return receipt


def download_backup(remote_name, destination=None, dest_dir=None):
    """Fetch a backup from the destination (for an off-site restore drill)."""
    destination = destination or parse_destination()
    dest_dir = Path(dest_dir or tempfile.mkdtemp())
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / remote_name
    destination.get_file(remote_name, target)
    # Pull the fingerprint and manifest too, so the download is a full set.
    names = set(destination.list_names())
    for extra in (remote_name + FINGERPRINT_SUFFIX, remote_name + MANIFEST_SUFFIX):
        if extra in names:
            destination.get_file(extra, dest_dir / extra)
    return target


def list_remote_backups(destination=None):
    """Names of complete recovery sets on the destination, newest first."""
    destination = destination or parse_destination()
    names = set(destination.list_names())
    artifacts = [
        name for name in names
        if name.startswith(BACKUP_PREFIXES)
        and name.endswith(BACKUP_SUFFIXES)
        and is_complete_recovery_set(destination, name)
    ]
    return sorted(artifacts, reverse=True)


def cleanup_remote_backups(destination=None, keep=10):
    """Delete older remote sets, keeping the newest ``keep``.

    Never removes the newest set, and never removes an artifact's fingerprint or
    manifest independently of the artifact, so the last known-good recovery
    point cannot be destroyed to make room for a new one. Only complete sets are
    considered, so a partially uploaded set is left for an operator to inspect.
    """
    destination = destination or parse_destination()
    keep = max(1, int(keep))
    complete = list_remote_backups(destination=destination)
    removed = []
    for name in complete[keep:]:
        names = [name, name + MANIFEST_SUFFIX]
        if not name.endswith(".zip"):
            names.append(name + FINGERPRINT_SUFFIX)
        removed.extend(destination.delete_names(names))
    return removed


# --------------------------------------------------------------------------- #
# Local receipt of the last verified off-site backup
# --------------------------------------------------------------------------- #
def state_path(directory=None):
    import backup_db

    return Path(directory or backup_db.BACKUP_DIR) / STATE_FILENAME


def read_state(directory=None):
    """The last successful verified upload, or ``None``. Contains no secrets."""
    path = state_path(directory)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def write_state(receipt, directory=None):
    path = state_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return path
