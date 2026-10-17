#!/usr/bin/env python3
"""Register a browser-compatible WAV derived from an existing audio asset."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from nining.storage import FileStorage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--playthrough-id", required=True)
    parser.add_argument("--source-asset-id", required=True)
    parser.add_argument("--relative-path", required=True)
    args = parser.parse_args()

    asset_root = args.asset_root.expanduser().resolve()
    target = (asset_root / args.relative_path).resolve()
    target.relative_to(asset_root)
    data = target.read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("target is not a RIFF/WAVE file")

    storage = FileStorage(args.storage_root)
    try:
        assets = storage.list_assets(args.playthrough_id)
        source = next(
            (asset for asset in assets if asset["asset_id"] == args.source_asset_id),
            None,
        )
        if source is None:
            raise KeyError(args.source_asset_id)
        if source.get("media_type") != "audio":
            raise ValueError("source asset is not audio")
        existing = next(
            (
                asset
                for asset in assets
                if asset.get("run_id") == source.get("run_id")
                and asset.get("relative_path") == args.relative_path
            ),
            None,
        )
        if existing is None:
            record = storage.register_asset(
                run_id=str(source["run_id"]),
                trigger_id=(
                    str(source["trigger_id"])
                    if source.get("trigger_id") is not None
                    else None
                ),
                role="candidate",
                media_type="audio",
                relative_path=args.relative_path,
                mime_type="audio/wav",
                size_bytes=len(data),
                duration_ms=(
                    int(source["duration_ms"])
                    if isinstance(source.get("duration_ms"), int)
                    else None
                ),
                sha256=sha256(data).hexdigest(),
                provenance="local",
                final_eligible=True,
            )
        else:
            record = existing
        print(
            json.dumps(
                {
                    "status": "ok",
                    "asset_id": record["asset_id"],
                    "trigger_id": record.get("trigger_id"),
                    "relative_path": record["relative_path"],
                    "mime_type": record.get("mime_type"),
                    "size_bytes": record["size_bytes"],
                    "duration_ms": record.get("duration_ms"),
                    "sha256": record["sha256"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
