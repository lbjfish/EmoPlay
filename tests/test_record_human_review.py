from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from nining.human_review import main, record_human_review
from nining.playthrough import PlaythroughManager
from nining.storage import FileStorage
from nining.storage.protocol import StorageBackend

HASH_A = "a" * 64
HASH_B = "b" * 64


def _file_review_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, str, dict[str, Any], dict[str, Any]]:
    storage_root = tmp_path / "state"
    asset_root = tmp_path / "outputs"
    asset_root.mkdir()
    storage = FileStorage(storage_root)
    try:
        backend = cast(StorageBackend, storage)
        manager = PlaythroughManager(backend)
        started = manager.start(title="review", materials_manifest_sha256=HASH_A)
        playthrough_id = str(started["playthrough"]["playthrough_id"])
        run = storage.begin_run(
            playthrough_id=playthrough_id,
            branch_id=str(started["branch"]["branch_id"]),
            mode="mock",
            requested_storage="file",
            effective_storage="file",
            materials_manifest_sha256=HASH_A,
            config_sha256=HASH_B,
        )
        audio = b"human review audio"
        (asset_root / "candidate.aiff").write_bytes(audio)
        candidate = storage.register_asset(
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
        draft_bytes = b"{}"
        (asset_root / "draft.json").write_bytes(draft_bytes)
        draft = storage.register_asset(
            run_id=str(run["run_id"]),
            trigger_id="T5",
            role="draft",
            media_type="json",
            relative_path="draft.json",
            mime_type="application/json",
            size_bytes=len(draft_bytes),
            sha256=sha256(draft_bytes).hexdigest(),
            provenance="fixture",
            final_eligible=False,
        )
    finally:
        storage.close()
    return storage_root, asset_root, playthrough_id, candidate, draft


def test_human_review_dry_run_does_not_write(tmp_path: Path) -> None:
    storage_root, asset_root, playthrough_id, candidate, _draft = _file_review_fixture(tmp_path)
    result = record_human_review(
        storage_root=storage_root,
        asset_root=asset_root,
        playthrough_id=playthrough_id,
        asset_id=str(candidate["asset_id"]),
        action="accept",
        reason="Human listened and accepts this voice candidate.",
        expected_sha256=str(candidate["sha256"]),
        write=False,
    )

    assert result["status"] == "dry_run"
    assert result["would_write"] is False
    storage = FileStorage(storage_root, read_only=True)
    try:
        assert storage.list_decisions(playthrough_id) == []
    finally:
        storage.close()


def test_human_review_accept_requires_current_sha(tmp_path: Path) -> None:
    _storage_root, _asset_root, playthrough_id, candidate, _draft = _file_review_fixture(tmp_path)
    result = main(
        [
            str(tmp_path),
            "--storage-root",
            "state",
            "--asset-root",
            "outputs",
            "--playthrough-id",
            playthrough_id,
            "--asset-id",
            str(candidate["asset_id"]),
            "--action",
            "accept",
            "--reason",
            "stale hash must fail",
            "--expected-sha256",
            "0" * 64,
        ]
    )

    assert result == 2


def test_human_review_reject_can_be_previewed_for_nonfinal_candidate(tmp_path: Path) -> None:
    storage_root, asset_root, playthrough_id, _candidate, draft = _file_review_fixture(tmp_path)
    result = record_human_review(
        storage_root=storage_root,
        asset_root=asset_root,
        playthrough_id=playthrough_id,
        asset_id=str(draft["asset_id"]),
        action="reject",
        reason="Draft specs are not media results.",
        expected_sha256=None,
        write=False,
    )

    assert result["status"] == "dry_run"
    assert result["decision"]["action"] == "reject"


def test_human_review_write_records_decision_and_checkpoint(tmp_path: Path) -> None:
    storage_root, asset_root, playthrough_id, candidate, _draft = _file_review_fixture(tmp_path)
    result = record_human_review(
        storage_root=storage_root,
        asset_root=asset_root,
        playthrough_id=playthrough_id,
        asset_id=str(candidate["asset_id"]),
        action="accept",
        reason="Human listened and accepts this voice candidate.",
        expected_sha256=str(candidate["sha256"]),
        write=True,
    )

    assert result["status"] == "written"
    assert result["decision"]["action"] == "accept"
    storage = FileStorage(storage_root, read_only=True)
    try:
        decisions = storage.list_decisions(playthrough_id)
        assert len(decisions) == 1
        assert decisions[0]["decided_by"] == "human"
        head = storage.get_head(playthrough_id)
        checkpoint = storage.get_checkpoint(playthrough_id, str(head["checkpoint_id"]))
        assert candidate["asset_id"] in checkpoint["state"]["accepted_assets"]
    finally:
        storage.close()


def test_human_review_missing_storage_root_does_not_create_store(tmp_path: Path) -> None:
    missing = tmp_path / "missing-state"
    result = main(
        [
            str(tmp_path),
            "--storage-root",
            "missing-state",
            "--asset-root",
            "outputs",
            "--playthrough-id",
            "play_missing",
            "--asset-id",
            "asset_missing",
            "--action",
            "reject",
            "--reason",
            "path typo must not create storage",
        ]
    )

    assert result == 2
    assert not missing.exists()


def test_human_review_blocks_asset_from_non_current_branch_before_decision(
    tmp_path: Path,
) -> None:
    storage_root, _asset_root, playthrough_id, candidate, _draft = _file_review_fixture(tmp_path)
    storage = FileStorage(storage_root)
    try:
        backend = cast(StorageBackend, storage)
        manager = PlaythroughManager(backend)
        head = storage.get_head(playthrough_id)
        manager.rewind(
            playthrough_id,
            checkpoint_id=str(head["checkpoint_id"]),
            inspect_only=False,
            branch_name="alternate",
        )
    finally:
        storage.close()

    result = main(
        [
            str(tmp_path),
            "--storage-root",
            "state",
            "--asset-root",
            "outputs",
            "--playthrough-id",
            playthrough_id,
            "--asset-id",
            str(candidate["asset_id"]),
            "--action",
            "accept",
            "--reason",
            "should be blocked before writing",
            "--expected-sha256",
            str(candidate["sha256"]),
        ]
    )

    assert result == 2
    storage = FileStorage(storage_root, read_only=True)
    try:
        assert storage.list_decisions(playthrough_id) == []
    finally:
        storage.close()
