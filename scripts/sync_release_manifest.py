#!/usr/bin/env python3
"""Synchronize outputs/manifest.json from storage review facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nining.release_manifest import build_release_manifest, write_manifest_atomic
from nining.storage import StorageError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument(
        "--storage-root",
        action="append",
        default=[],
        type=Path,
        help="FileStorage root to read in read-only mode; may be repeated.",
    )
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument("--manifest", dest="manifest_path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="exit non-zero when fact-derived known_blockers remain.",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    manifest_path = args.manifest_path or root / "outputs" / "manifest.json"
    try:
        result = build_release_manifest(
            root=root,
            storage_roots=args.storage_root,
            asset_root=args.asset_root or root / "outputs",
            manifest_path=manifest_path,
        )
    except (OSError, StorageError, TypeError, ValueError):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_code": "manifest_sync_failed",
                    "message": "release facts failed validation",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    if args.dry_run:
        print(result.diff or "manifest is already synchronized")
    else:
        write_manifest_atomic(manifest_path, result.manifest)
        print(
            json.dumps(
                {
                    "manifest": manifest_path.name,
                    "changed": result.changed,
                    "release_ready": result.release_ready,
                    "known_blockers": list(result.known_blockers),
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
    return 1 if args.require_ready and result.known_blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
