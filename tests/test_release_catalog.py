from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from nining.release_catalog import load_release_media_catalog
from nining.session import ApplicationSession

ROOT = Path(__file__).resolve().parents[1]


def _write_manifest(output_root: Path, *, path: str = "outputs/T1.png") -> bytes:
    content = b"clean-install-final-image"
    media = output_root / "T1.png"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(content)
    manifest = {
        "schema_version": "1.0",
        "release_ready": True,
        "final_media": {
            "T1": [
                {
                    "accepted": True,
                    "asset_id": "asset_release_T1",
                    "run_id": "run_release_T1",
                    "trigger_id": "T1",
                    "role": "candidate",
                    "media_type": "image",
                    "mime_type": "image/png",
                    "path": path,
                    "size_bytes": len(content),
                    "sha256": sha256(content).hexdigest(),
                    "width_px": 1,
                    "height_px": 1,
                    "duration_ms": None,
                    "decided_by": "human",
                    "decision_reason": "accepted release fixture",
                }
            ]
        },
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return content


def _write_config(root: Path) -> Path:
    path = root / "offline.toml"
    path.write_text(
        """
[runtime]
mode = "offline"
allow_network = false
strict_provenance = true
output_root = "outputs"

[storage]
backend = "file"
file_root = "state"
fallback_backend = "file"

[models.text]
provider = "deterministic"

[models.image]
provider = "placeholder"

[models.voice]
provider = "prompt_only"
enabled = false

[logging]
directory = "logs"
save_raw_provider_responses = false

[testing]
fixture_root = "fixtures"
block_network_by_default = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def test_release_catalog_is_verified_and_available_without_review_storage(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "outputs"
    content = _write_manifest(output_root)

    records = load_release_media_catalog(output_root)
    assert len(records) == 1
    assert records[0]["relative_path"] == "T1.png"
    assert records[0]["review_action"] == "accept"
    assert records[0]["catalog_source"] == "release_manifest"

    with ApplicationSession.open(
        config_path=_write_config(tmp_path),
        materials_root=ROOT / "materials" / "sample",
    ) as session:
        assert session.storage.list_assets(session.playthrough_id) == []
        dashboard = session.facade.dashboard(view_mode="story")
        asset = dashboard["assets"][0]
        assert asset["asset_id"] == "asset_release_T1"
        assert asset["review_action"] == "accept"
        assert asset["review_mutable"] is False
        assert asset["preview_available"] is True
        resource = session.facade.media("asset_release_T1")
        assert resource is not None
        assert resource.path.read_bytes() == content


def test_release_catalog_rejects_unsafe_paths_and_tampering(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    _write_manifest(output_root, path="outputs/../outside.png")
    with pytest.raises(ValueError, match="unsafe"):
        load_release_media_catalog(output_root)

    _write_manifest(output_root)
    (output_root / "T1.png").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="verification failed"):
        load_release_media_catalog(output_root)
