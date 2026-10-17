#!/usr/bin/env python3
"""Rebuild the deterministic T1-T6 output set and its manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nining.loader import load_materials
from nining.spec_builder import build_all_scene_specs


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_outputs(root: Path) -> dict[str, Any]:
    root = root.resolve()
    materials = load_materials(root / "materials" / "sample")
    specs = [spec.to_dict() for spec in build_all_scene_specs(materials)]
    output_root = root / "outputs"
    for spec in specs:
        _write_json(output_root / "specs" / f"{spec['trigger_id']}.json", spec)

    manifest_path = output_root / "manifest.json"
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    manifest = {
        **existing,
        "schema_version": "1.0",
        "materials_manifest_sha256": materials.manifest["sha256"],
        "spec_count": len(specs),
        "triggers": [str(spec["trigger_id"]) for spec in specs],
        "spec_sha256": {
            str(spec["trigger_id"]): str(spec["spec_sha256"]) for spec in specs
        },
    }
    manifest.setdefault("final_media", {})
    manifest.setdefault("known_blockers", [])
    _write_json(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    print(
        json.dumps(
            build_outputs(args.root), ensure_ascii=False, sort_keys=True, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
