"""One-shot backfill for pre-v1.3.0 Hash Vault entries.

Before v1.3.0, HashVaultSave wrote only `{hash_key}.pt`. v1.3.0 adds a
`{hash_key}.json` sidecar (+ optional `{hash_key}.thumb.png`) so the Hash
Vault Browser can index entries by label, timestamp, and thumbnail.

This script scans a vault directory, finds orphan `.pt` files (no matching
sidecar), loads each, and writes the missing sidecar + thumbnail using file
mtime as `created_at` and `"(legacy)"` as `label`.

Usage (from the pack root, using the ComfyUI embedded Python):
    python tools/migrate_hash_vault.py
    python tools/migrate_hash_vault.py --vault-dir "F:/path/to/output/hash_vault"
    python tools/migrate_hash_vault.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import torch

SCRIPT_DIR = Path(__file__).resolve().parent
PACK_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PACK_ROOT))

from api_optimizer_nodes import (  # noqa: E402
    SIDECAR_SUFFIX,
    THUMBNAIL_SUFFIX,
    _find_image_tensor,
    _generate_thumbnail,
    _summarize_payload,
)

DEFAULT_VAULT_DIR = Path(
    "F:/ComfyUI_windows_portable_nvidia/ComfyUI_windows_portable/ComfyUI/output/hash_vault"
)
LEGACY_LABEL = "(legacy)"


def build_sidecar(hash_key: str, cpu_data, label: str, created_iso: str) -> dict:
    return {
        "hash_key": hash_key,
        "label": label,
        "created_at": created_iso,
        "last_accessed_at": created_iso,
        "payload": _summarize_payload(cpu_data),
        "thumbnail": None,  # Filled in after thumbnail write succeeds
        "schema_version": 1,
    }


def write_sidecar_atomic(sidecar_path: Path, sidecar: dict) -> None:
    tmp_path = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)
    os.replace(tmp_path, sidecar_path)


def migrate_vault(vault_dir: Path, label: str, dry_run: bool) -> dict:
    if not vault_dir.exists():
        print(f"[migrate] vault dir does not exist: {vault_dir}")
        return {"scanned": 0, "skipped": 0, "migrated": 0, "failed": 0}

    stats = {"scanned": 0, "skipped": 0, "migrated": 0, "failed": 0}

    for pt_path in sorted(vault_dir.glob("*.pt")):
        stats["scanned"] += 1
        hash_key = pt_path.stem
        sidecar_path = vault_dir / f"{hash_key}{SIDECAR_SUFFIX}"
        thumbnail_path = vault_dir / f"{hash_key}{THUMBNAIL_SUFFIX}"

        if sidecar_path.exists():
            stats["skipped"] += 1
            continue

        try:
            mtime = pt_path.stat().st_mtime
            created_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

            if dry_run:
                print(f"[migrate] would backfill {hash_key[:8]}  (created {created_iso})")
                stats["migrated"] += 1
                continue

            cached_data = torch.load(pt_path, map_location="cpu", weights_only=False)
            sidecar = build_sidecar(hash_key, cached_data, label, created_iso)

            image_tensor = _find_image_tensor(cached_data)
            if image_tensor is not None:
                if _generate_thumbnail(image_tensor, str(thumbnail_path)):
                    sidecar["thumbnail"] = f"{hash_key}{THUMBNAIL_SUFFIX}"

            write_sidecar_atomic(sidecar_path, sidecar)
            thumb_note = "+thumb" if sidecar["thumbnail"] else "no-thumb"
            print(f"[migrate] backfilled {hash_key[:8]}  ({thumb_note})  created {created_iso}")
            stats["migrated"] += 1
        except Exception as e:
            print(f"[migrate] FAILED {hash_key[:8]}: {e}")
            stats["failed"] += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill sidecars for pre-v1.3.0 Hash Vault entries.")
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=DEFAULT_VAULT_DIR,
        help=f"Path to the hash_vault directory (default: {DEFAULT_VAULT_DIR})",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=LEGACY_LABEL,
        help=f'Label to assign to orphan entries (default: "{LEGACY_LABEL}")',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be migrated without writing anything.",
    )
    args = parser.parse_args()

    print(f"[migrate] vault dir: {args.vault_dir}")
    print(f"[migrate] label:     {args.label!r}")
    print(f"[migrate] dry run:   {args.dry_run}")
    print("-" * 60)

    stats = migrate_vault(args.vault_dir, args.label, args.dry_run)

    print("-" * 60)
    print(f"[migrate] scanned:  {stats['scanned']}")
    print(f"[migrate] skipped:  {stats['skipped']}  (already had sidecar)")
    print(f"[migrate] migrated: {stats['migrated']}")
    print(f"[migrate] failed:   {stats['failed']}")
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
