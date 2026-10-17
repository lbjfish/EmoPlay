#!/usr/bin/env python3
"""Import an existing local AIFF reference voice audit into FileStorage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nining.release_manifest import import_voice_reference
from nining.storage import StorageError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--storage-root", required=True, type=Path)
    parser.add_argument("--audio", dest="audio_path", type=Path)
    parser.add_argument("--audit", dest="audit_path", type=Path)
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument("--playthrough-id")
    args = parser.parse_args(argv)

    try:
        result = import_voice_reference(
            root=args.root,
            storage_root=args.storage_root,
            audio_path=args.audio_path,
            audit_path=args.audit_path,
            asset_root=args.asset_root,
            playthrough_id=args.playthrough_id,
        )
    except (OSError, StorageError, TypeError, ValueError):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_code": "voice_reference_import_failed",
                    "message": "reference voice evidence failed validation",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
