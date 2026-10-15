#!/usr/bin/env python3
"""Build a backup manifest: filenames, sizes, timestamps, SHA-256.

Read-only: walks the given paths and writes a JSON manifest. Files whose
names look secret-bearing (.env*, *secret*, *.key, *.pem, *_rsa*) are
SKIPPED by name pattern and counted only — their names, contents, and
values never enter the manifest or stdout.

Usage:
    python scripts/backup_inventory.py manifest.json <path> [<path> ...]

Exit 0 on success. The manifest records what exists; it is not itself
a backup — copy the listed files off-server separately.
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SECRET_NAME_PARTS = (".env", "secret", ".key", ".pem", "id_rsa", ".pfx", ".p12")
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}


def looks_secret(name):
    lowered = name.lower()
    return any(part in lowered for part in SECRET_NAME_PARTS)


def sha256_of(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def inventory(paths):
    files, skipped_secrets, skipped_links, total_bytes = [], 0, 0, 0
    for root in paths:
        root = Path(root)
        if root.is_file() and not root.is_symlink():
            candidates = [root]
            base = root.parent
        elif root.is_dir() and not root.is_symlink():
            base = root
            candidates = sorted(
                p for p in root.rglob("*")
                if p.is_file() and not any(d in p.parts for d in SKIP_DIRS)
            )
        else:
            return None, f"path not found: {root}"
        for path in candidates:
            if path.is_symlink():
                # Never follow links: a backup must be self-contained, and a
                # link could pull production paths or escape the archive.
                skipped_links += 1
                continue
            if looks_secret(path.name):
                skipped_secrets += 1
                continue
            stat = path.stat()
            try:
                rel = str(path.relative_to(base))
            except ValueError:
                rel = path.name
            files.append({
                "path": rel,
                "bytes": stat.st_size,
                "mtime_utc": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc).isoformat(),
                "sha256": sha256_of(path),
            })
            total_bytes += stat.st_size
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "roots": [str(p) for p in paths],
        "files": files,
        "skipped_secret_names_count": skipped_secrets,
        "skipped_symlinks_count": skipped_links,
        "total_bytes": total_bytes,
    }
    return manifest, None


def main(argv=None):
    args = list(argv or sys.argv[1:])
    if len(args) < 2:
        print("usage: backup_inventory.py manifest.json <path> [<path> ...]")
        return 2
    out, paths = Path(args[0]), args[1:]
    manifest, error = inventory(paths)
    if error:
        print(f"FAIL {error}")
        return 1
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[PASS] {len(manifest['files'])} file(s), "
          f"{manifest['total_bytes']} bytes -> {out}")
    if manifest["skipped_secret_names_count"]:
        print(f"[WARN] {manifest['skipped_secret_names_count']} secret-named "
              f"file(s) excluded by name pattern (fetch via cPanel only)")
    if manifest["skipped_symlinks_count"]:
        print(f"[WARN] {manifest['skipped_symlinks_count']} symlink(s) "
              f"excluded (backups must be self-contained)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
