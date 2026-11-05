#!/usr/bin/env python
"""Zip the recordings vault with a hash manifest.

All evidence currently lives on a single disk with an unreachable cloud mirror
(see IMPROVEMENTS.md 3.11). This produces one portable archive plus a manifest
an investigator can use to prove no file changed in transit.

    python scripts/backup_vault.py                # -> backups/vault-<ts>.zip
    python scripts/backup_vault.py --dest D:\\bu  # custom destination
    python scripts/backup_vault.py --verify backups/vault-<ts>.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
VAULT_DIR = BASE_DIR / "backend" / "recordings"
DEFAULT_DEST = BASE_DIR / "backups"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_backup(dest: Path) -> Path:
    if not VAULT_DIR.is_dir():
        raise SystemExit(f"No vault directory at {VAULT_DIR}")
    files = sorted(p for p in VAULT_DIR.iterdir() if p.is_file() and p.name != ".gitkeep")
    if not files:
        raise SystemExit("The vault is empty; nothing to back up.")

    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    archive = dest / f"vault-{stamp}.zip"

    manifest = {
        "generated_at": int(time.time()),
        "source": str(VAULT_DIR),
        "file_count": len(files),
        "files": [],
    }

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            digest = sha256_file(path)
            manifest["files"].append(
                {"name": path.name, "size": path.stat().st_size, "sha256": digest}
            )
            bundle.write(path, arcname=path.name)
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2))

    total = sum(entry["size"] for entry in manifest["files"])
    print(f"Backed up {len(files)} file(s), {total / 1024:.1f} KB -> {archive}")
    print("Manifest written inside the archive as manifest.json")
    return archive


def verify_backup(archive: Path) -> int:
    if not archive.is_file():
        raise SystemExit(f"Archive not found: {archive}")
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        problems = 0
        for entry in manifest["files"]:
            try:
                payload = bundle.read(entry["name"])
            except KeyError:
                print(f"MISSING  {entry['name']}")
                problems += 1
                continue
            actual = hashlib.sha256(payload).hexdigest()
            if actual != entry["sha256"]:
                print(f"MISMATCH {entry['name']}")
                problems += 1
    print(
        f"{'OK' if problems == 0 else 'FAILED'}: "
        f"{len(manifest['files']) - problems}/{len(manifest['files'])} files verified"
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the CERS recordings vault.")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--verify", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.verify:
        return 1 if verify_backup(args.verify) else 0
    create_backup(args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
