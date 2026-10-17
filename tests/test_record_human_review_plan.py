from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from nining.human_review_plan import main, record_human_review_plan
from nining.playthrough import PlaythroughManager
from nining.storage import FileStorage
from nining.storage.protocol import StorageBackend

HASH_A = "a" * 64
HASH_B = "b" * 64


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, list[dict[str, Any]]]:
    storage_root = tmp_path / "state"
    asset_root = tmp_path / "outputs"
    asset_root.mkdir()
    storage = FileStorage(storage_root)
    assets: list[dict[str, Any]] = []
    try:
        backend = cast(StorageBackend, storage)
        manager = PlaythroughManager(backend)
        started = manager.start(title="review-plan", materials_manifest_sha256=HASH_A)
        playthrough_id = str(started["playthrough"]["playthrough_id"])
        for index, content in enumerate((b"accept audio", b"reject audio", b"reject audio")):
            run = storage.begin_run(
                playthrough_id=playthrough_id,
                branch_id=str(started["branch"]["branch_id"]),
                mode="mock",
                requested_storage="file",
                effective_storage="file",
                materials_manifest_sha256=HASH_A,
                config_sha256=HASH_B,
            )
            relative = f"candidate-{index}.aiff"
            (asset_root / relative).write_bytes(content)
            assets.append(
                storage.register_asset(
                    run_id=str(run["run_id"]),
                    trigger_id="T5",
                    role="candidate",
                    media_type="audio",
                    relative_path=relative,
                    mime_type="audio/aiff",
                    size_bytes=len(content),
                    duration_ms=1000 + index,
                    sha256=sha256(content).hexdigest(),
                    provenance="local",
                    final_eligible=True,
                )
            )
    finally:
        storage.close()
    return storage_root, asset_root, playthrough_id, assets


def _write_plan(path: Path, decisions: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({"schema_version": 1, "decisions": decisions}, indent=2),
        encoding="utf-8",
    )
    return path


def test_review_plan_dry_run_does_not_write_and_reports_duplicate_reject_sha(
    tmp_path: Path,
) -> None:
    storage_root, asset_root, playthrough_id, assets = _fixture(tmp_path)
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "asset_id": assets[0]["asset_id"],
                "action": "accept",
                "reason": "Human accepts this candidate.",
                "expected_sha256": assets[0]["sha256"],
            },
            {
                "asset_id": assets[1]["asset_id"],
                "action": "reject",
                "reason": "Human rejects this candidate.",
            },
            {
                "asset_id": assets[2]["asset_id"],
                "action": "reject",
                "reason": "Human rejects duplicate bytes.",
            },
        ],
    )

    result = record_human_review_plan(
        plan_path=plan,
        storage_root=storage_root,
        asset_root=asset_root,
        playthrough_id=playthrough_id,
        write=False,
    )

    assert result["status"] == "dry_run"
    assert result["decision_count"] == 3
    assert result["accept_count"] == 1
    assert result["reject_count"] == 2
    assert result["distinct_rejected_sha_count"] == 1
    assert result["duplicate_reject_sha256"] == [assets[1]["sha256"]]
    storage = FileStorage(storage_root, read_only=True)
    try:
        assert storage.list_decisions(playthrough_id) == []
    finally:
        storage.close()


def test_review_plan_write_records_all_prevalidated_decisions(tmp_path: Path) -> None:
    storage_root, asset_root, playthrough_id, assets = _fixture(tmp_path)
    plan = _write_plan(
        tmp_path / "plan.json",
        [
            {
                "asset_id": assets[0]["asset_id"],
                "action": "accept",
                "reason": "Human accepts this candidate.",
                "expected_sha256": assets[0]["sha256"],
            },
            {
                "asset_id": assets[1]["asset_id"],
                "action": "reject",
                "reason": "Human rejects this candidate.",
            },
        ],
    )

    result = record_human_review_plan(
        plan_path=plan,
        storage_root=storage_root,
        asset_root=asset_root,
        playthrough_id=playthrough_id,
        write=True,
    )

    assert result["status"] == "written"
    assert len(result["written"]) == 2
    storage = FileStorage(storage_root, read_only=True)
    try:
        assert len(storage.list_decisions(playthrough_id)) == 2
    finally:
        storage.close()


def test_review_plan_rejects_duplicate_asset_before_any_write(tmp_path: Path) -> None:
    storage_root, _asset_root, playthrough_id, assets = _fixture(tmp_path)
    _write_plan(
        tmp_path / "plan.json",
        [
            {
                "asset_id": assets[0]["asset_id"],
                "action": "reject",
                "reason": "First decision.",
            },
            {
                "asset_id": assets[0]["asset_id"],
                "action": "reject",
                "reason": "Duplicate decision.",
            },
        ],
    )

    result = main(
        [
            str(tmp_path),
            "--plan",
            "plan.json",
            "--storage-root",
            "state",
            "--asset-root",
            "outputs",
            "--playthrough-id",
            playthrough_id,
        ]
    )

    assert result == 2
    storage = FileStorage(storage_root, read_only=True)
    try:
        assert storage.list_decisions(playthrough_id) == []
    finally:
        storage.close()


def test_review_plan_rejects_placeholder_reason(tmp_path: Path) -> None:
    storage_root, _asset_root, playthrough_id, assets = _fixture(tmp_path)
    _write_plan(
        tmp_path / "plan.json",
        [
            {
                "asset_id": assets[0]["asset_id"],
                "action": "reject",
                "reason": "<human reason>",
            }
        ],
    )

    result = main(
        [
            str(tmp_path),
            "--plan",
            "plan.json",
            "--storage-root",
            "state",
            "--asset-root",
            "outputs",
            "--playthrough-id",
            playthrough_id,
        ]
    )

    assert result == 2
    storage = FileStorage(storage_root, read_only=True)
    try:
        assert storage.list_decisions(playthrough_id) == []
    finally:
        storage.close()
