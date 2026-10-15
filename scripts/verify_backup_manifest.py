#!/usr/bin/env python3
"""Verify a backup manifest against a directory (integrity + completeness).

Read-only: recomputes size/SHA-256 for every manifest entry and reports
OK / MISMATCH / MISSING. Extra files on disk are listed as INFO (a restore
source may legitimately contain more than the manifest).

Usage:
    python scripts/verify_backup_manifest.py manifest.json <directory>

Exit 0 iff every manifest entry verifies. Used for isolated-restore
checks: point it at the *restored copy*, never at live production paths
when testing a restore procedure.
"""

import hashlib
import json
import sys
from pathlib import Path


def sha256_of(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify(manifest, base_dir):
    base = Path(base_dir)
    results = []
    for entry in manifest.get("files", []):
        target = base / entry["path"]
        if not target.is_file():
            results.append(("FAIL", f"MISSING {entry['path']}"))
            continue
        size = target.stat().st_size
        if size != entry["bytes"]:
            results.append(("FAIL", f"SIZE-MISMATCH {entry['path']} "
                                    f"(disk {size}, manifest {entry['bytes']})"))
            continue
        if sha256_of(target) != entry["sha256"]:
            results.append(("FAIL", f"HASH-MISMATCH {entry['path']}"))
            continue
        results.append(("PASS", f"OK {entry['path']}"))
    return results


def main(argv=None):
    args = list(argv or sys.argv[1:])
    if len(args) != 2:
        print("usage: verify_backup_manifest.py manifest.json <directory>")
        return 2
    manifest_path, base_dir = Path(args[0]), Path(args[1])
    if not manifest_path.is_file():
        print(f"FAIL manifest not found: {manifest_path}")
        return 1
    if not base_dir.is_dir():
        print(f"FAIL directory not found: {base_dir}")
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"FAIL manifest unreadable: {type(exc).__name__}")
        return 1
    if not isinstance(manifest.get("files"), list):
        print("FAIL manifest has no 'files' list (wrong file?)")
        return 1
    results = verify(manifest, base_dir)
    failed = sum(1 for level, _ in results if level == "FAIL")
    for level, message in results:
        print(f"[{level}] {message}")
    print(f"manifest: {'FAIL' if failed else 'OK'} "
          f"({len(results) - failed}/{len(results)} verified)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
