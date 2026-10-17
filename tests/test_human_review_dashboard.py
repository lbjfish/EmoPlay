from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import cast

from nining.human_review_dashboard import (
    build_dashboard,
    collect_review_assets,
    render_html,
    render_markdown,
)
from nining.playthrough import PlaythroughManager
from nining.storage import FileStorage
from nining.storage.protocol import StorageBackend


def _manifest() -> dict[str, object]:
    return {
        "known_blockers": [
            "visual references not generated",
            "voice reference and T5 preset-voice candidates require human listening",
        ],
        "candidates": {
            "T5": [
                {
                    "tuning_label": "after-rate-220",
                    "duration_ms": 5132,
                    "sha256": "e" * 64,
                    "path": "outputs/runs/run/T5/candidate-01.aiff",
                }
            ],
            "voice_reference": [
                {
                    "duration_ms": 4273,
                    "sha256": "c" * 64,
                    "path": "outputs/references/voice/nining-reference.aiff",
                }
            ],
        },
    }


def test_dashboard_renderers_are_local_coordination_only() -> None:
    package = {
        "source_commit": "abc123",
        "zip_sha256": "d" * 64,
        "release_ready": False,
    }
    review_assets = [
        {
            "asset_id": "asset_t5",
            "role": "candidate",
            "media_type": "audio",
            "trigger_id": "T5",
            "duration_ms": 5132,
            "sha256": "e" * 64,
            "relative_path": "runs/run/T5/candidate-01.aiff",
            "manifest_candidate": True,
            "latest_decision": "none",
            "duplicate_note": "",
            "accept_dry_run": "PYTHONPATH=src .venv/bin/python scripts/record_human_review.py . --asset-id asset_t5 --action accept",
            "reject_dry_run": "PYTHONPATH=src .venv/bin/python scripts/record_human_review.py . --asset-id asset_t5 --action reject",
        }
    ]
    markdown = render_markdown(
        manifest=_manifest(),
        package_manifest=package,
        voice_review_url="http://127.0.0.1:59103/",
        review_assets=review_assets,
    )
    html = render_html(
        manifest=_manifest(),
        package_manifest=package,
        voice_review_url="http://127.0.0.1:59103/",
        review_assets=review_assets,
    )

    assert "not final" in markdown
    assert "does not authorize provider calls" in markdown
    assert "visual references not generated" in markdown
    assert "after-rate-220" in markdown
    assert "release_ready" in markdown
    assert "asset_t5" in markdown
    assert "record_human_review.py" in markdown
    assert "record_human_review_plan.py" in markdown
    assert "voice-decision-plan.template.json" in markdown
    assert "Not final" in html
    assert "after-rate-220" in html
    assert "Voice decision pack" in html
    assert "asset_t5" in html
    assert "record_human_review_plan.py" in html
    assert "AZURE_IMAGE_API_KEY" not in markdown + html
    assert "Bearer " not in markdown + html


def test_dashboard_builder_writes_markdown_and_html(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outputs = root / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    package_manifest = tmp_path / "zip.manifest.json"
    package_manifest.write_text(
        json.dumps({"source_commit": "abc123", "zip_sha256": "d" * 64}),
        encoding="utf-8",
    )
    destination = tmp_path / "human-review"

    result = build_dashboard(
        root=root,
        output_dir=destination,
        package_manifest_path=package_manifest,
        voice_review_url="http://127.0.0.1:59103/",
    )

    assert result["markdown"].is_file()
    assert result["html"].is_file()
    assert "visual references not generated" in result["markdown"].read_text(
        encoding="utf-8"
    )


def test_collect_review_assets_builds_exact_dry_run_commands(tmp_path: Path) -> None:
    storage_root = tmp_path / "state"
    asset_root = tmp_path / "outputs"
    asset_root.mkdir()
    storage = FileStorage(storage_root)
    try:
        backend = cast(StorageBackend, storage)
        manager = PlaythroughManager(backend)
        started = manager.start(title="review", materials_manifest_sha256="a" * 64)
        playthrough_id = str(started["playthrough"]["playthrough_id"])
        run = storage.begin_run(
            playthrough_id=playthrough_id,
            branch_id=str(started["branch"]["branch_id"]),
            mode="mock",
            requested_storage="file",
            effective_storage="file",
            materials_manifest_sha256="a" * 64,
            config_sha256="b" * 64,
        )
        audio = b"review candidate"
        (asset_root / "candidate.aiff").write_bytes(audio)
        asset = storage.register_asset(
            run_id=str(run["run_id"]),
            trigger_id="T5",
            role="candidate",
            media_type="audio",
            relative_path="candidate.aiff",
            mime_type="audio/aiff",
            size_bytes=len(audio),
            duration_ms=1200,
            sha256=sha256(audio).hexdigest(),
            provenance="local",
            final_eligible=True,
        )
    finally:
        storage.close()

    rows = collect_review_assets(
        storage_root=storage_root,
        playthrough_id=playthrough_id,
        storage_root_label="work/state/local-voice-files",
        asset_root_label="outputs",
    )

    assert rows == [
        {
            "asset_id": asset["asset_id"],
            "role": "candidate",
            "media_type": "audio",
            "trigger_id": "T5",
            "duration_ms": 1200,
            "sha256": asset["sha256"],
            "relative_path": "candidate.aiff",
            "manifest_candidate": False,
            "latest_decision": "none",
            "duplicate_note": "",
            "accept_dry_run": (
                "PYTHONPATH=src .venv/bin/python scripts/record_human_review.py . "
                "--storage-root work/state/local-voice-files "
                "--asset-root outputs "
                f"--playthrough-id {playthrough_id} "
                f"--asset-id {asset['asset_id']} "
                "--action accept --reason '<human reason>' "
                f"--expected-sha256 {asset['sha256']}"
            ),
            "reject_dry_run": (
                "PYTHONPATH=src .venv/bin/python scripts/record_human_review.py . "
                "--storage-root work/state/local-voice-files "
                "--asset-root outputs "
                f"--playthrough-id {playthrough_id} "
                f"--asset-id {asset['asset_id']} "
                "--action reject --reason '<human reason>'"
            ),
        }
    ]


def test_collect_review_assets_marks_duplicate_sha_after_manifest_candidate(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "state"
    asset_root = tmp_path / "outputs"
    asset_root.mkdir()
    storage = FileStorage(storage_root)
    try:
        backend = cast(StorageBackend, storage)
        manager = PlaythroughManager(backend)
        started = manager.start(title="review", materials_manifest_sha256="a" * 64)
        playthrough_id = str(started["playthrough"]["playthrough_id"])
        run = storage.begin_run(
            playthrough_id=playthrough_id,
            branch_id=str(started["branch"]["branch_id"]),
            mode="mock",
            requested_storage="file",
            effective_storage="file",
            materials_manifest_sha256="a" * 64,
            config_sha256="b" * 64,
        )
        audio = b"same candidate"
        digest = sha256(audio).hexdigest()
        (asset_root / "primary.aiff").write_bytes(audio)
        (asset_root / "duplicate.aiff").write_bytes(audio)
        primary = storage.register_asset(
            run_id=str(run["run_id"]),
            trigger_id="T5",
            role="candidate",
            media_type="audio",
            relative_path="primary.aiff",
            mime_type="audio/aiff",
            size_bytes=len(audio),
            duration_ms=1200,
            sha256=digest,
            provenance="local",
            final_eligible=True,
        )
        duplicate = storage.register_asset(
            run_id=str(run["run_id"]),
            trigger_id="T5",
            role="candidate",
            media_type="audio",
            relative_path="duplicate.aiff",
            mime_type="audio/aiff",
            size_bytes=len(audio),
            duration_ms=1200,
            sha256=digest,
            provenance="local",
            final_eligible=True,
        )
    finally:
        storage.close()

    rows = collect_review_assets(
        storage_root=storage_root,
        playthrough_id=playthrough_id,
        storage_root_label="state",
        asset_root_label="outputs",
        manifest_paths={"outputs/primary.aiff"},
    )

    assert [row["asset_id"] for row in rows] == [
        primary["asset_id"],
        duplicate["asset_id"],
    ]
    assert rows[0]["manifest_candidate"] is True
    assert rows[0]["duplicate_note"] == ""
    assert rows[1]["manifest_candidate"] is False
    assert rows[1]["duplicate_note"] == (
        f"same bytes as {primary['asset_id']}; do not count as a separate rejected result"
    )
