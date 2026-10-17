from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from nining.playthrough import PlaythroughManager
from nining.release_manifest import (
    _public_review_reason,
    build_release_manifest,
    write_manifest_atomic,
)
from nining.review import ReviewService
from nining.storage import FileStorage
from nining.storage.protocol import StorageBackend

HASH_A = "a" * 64
HASH_B = "b" * 64


def test_public_review_reason_removes_private_conversation_prefixes() -> None:
    assert (
        _public_review_reason("\u7528\u6237\u786e\u8ba4 T1 场景图可用。")
        == "人工审核：T1 场景图可用。"
    )
    assert (
        _public_review_reason(
            "\u7528\u6237\u6388\u6743\u540e\u7eed\u9ed8\u8ba4\u5904\u7406；该候选语速过快。"
        )
        == "人工审核：该候选语速过快。"
    )
    assert _public_review_reason("Existing neutral reason") == "Existing neutral reason"


def _write_base_manifest(root: Path) -> None:
    manifest = {
        "schema_version": "1.0",
        "materials_manifest_sha256": HASH_A,
        "spec_count": 6,
        "triggers": [f"T{index}" for index in range(1, 7)],
        "spec_sha256": {f"T{index}": str(index) * 64 for index in range(1, 7)},
        "known_blockers": ["stale manual blocker"],
    }
    path = root / "outputs" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _start_storage(storage_root: Path) -> tuple[FileStorage, PlaythroughManager, str, str]:
    storage = FileStorage(storage_root)
    manager = PlaythroughManager(cast(StorageBackend, storage))
    started = manager.start(
        title="release manifest test",
        materials_manifest_sha256=HASH_A,
    )
    playthrough_id = started["playthrough"]["playthrough_id"]
    branch_id = started["branch"]["branch_id"]
    return storage, manager, playthrough_id, branch_id


def _begin_run(storage: FileStorage, playthrough_id: str, branch_id: str) -> dict[str, Any]:
    return storage.begin_run(
        playthrough_id=playthrough_id,
        branch_id=branch_id,
        mode="local",
        requested_storage="file",
        effective_storage="file",
        materials_manifest_sha256=HASH_A,
        config_sha256=HASH_B,
        capabilities={"image": {"state": "fixture"}, "voice": {"state": "fixture"}},
        created_at_utc="2026-10-16T00:00:00Z",
    )


def _write_asset(
    *,
    storage: FileStorage,
    output_root: Path,
    run_id: str,
    relative_path: str,
    content: bytes,
    role: str,
    media_type: str,
    trigger_id: str | None = None,
    duration_ms: int | None = None,
    provider_call_id: str | None = None,
) -> dict[str, Any]:
    path = output_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    mime_type = "audio/aiff" if media_type == "audio" else "image/png"
    return storage.register_asset(
        run_id=run_id,
        trigger_id=trigger_id,
        provider_call_id=provider_call_id,
        role=role,
        media_type=media_type,
        relative_path=relative_path,
        mime_type=mime_type,
        size_bytes=len(content),
        duration_ms=duration_ms,
        sha256=sha256(content).hexdigest(),
        provenance="local",
        final_eligible=True,
        created_at_utc="2026-10-16T00:00:01Z",
    )


def _review(
    manager: PlaythroughManager,
    storage: FileStorage,
    output_root: Path,
    playthrough_id: str,
    asset: dict[str, Any],
    action: str,
    reason: str,
) -> None:
    service = ReviewService(
        storage=cast(StorageBackend, storage),
        manager=manager,
        asset_root=output_root,
    )
    kwargs = {
        "playthrough_id": playthrough_id,
        "asset_id": asset["asset_id"],
        "action": action,
        "reason": reason,
    }
    if action == "accept":
        kwargs["expected_sha256"] = asset["sha256"]
    service.review(**kwargs)


def _ready_fixture(root: Path, store_root: Path) -> None:
    _write_base_manifest(root)
    output_root = root / "outputs"
    storage, manager, playthrough_id, branch_id = _start_storage(store_root)
    try:
        run = _begin_run(storage, playthrough_id, branch_id)
        accepted = [
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="reference-visual.png",
                content=b"reference visual",
                role="reference",
                media_type="image",
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="reference-voice.aiff",
                content=b"reference voice",
                role="reference",
                media_type="audio",
                duration_ms=4_000,
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="T1.png",
                content=b"T1 image",
                role="candidate",
                media_type="image",
                trigger_id="T1",
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="T3.png",
                content=b"T3 image",
                role="candidate",
                media_type="image",
                trigger_id="T3",
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="T2.png",
                content=b"T2 background image",
                role="candidate",
                media_type="image",
                trigger_id="T2",
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="T2.aiff",
                content=b"T2 audio",
                role="candidate",
                media_type="audio",
                trigger_id="T2",
                duration_ms=8_000,
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="T4.png",
                content=b"T4 image",
                role="candidate",
                media_type="image",
                trigger_id="T4",
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="T5.png",
                content=b"T5 background image",
                role="candidate",
                media_type="image",
                trigger_id="T5",
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="T5.aiff",
                content=b"T5 audio",
                role="candidate",
                media_type="audio",
                trigger_id="T5",
                duration_ms=12_000,
            ),
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path="T6.png",
                content=b"T6 image",
                role="candidate",
                media_type="image",
                trigger_id="T6",
            ),
        ]
        rejected = [
            _write_asset(
                storage=storage,
                output_root=output_root,
                run_id=run["run_id"],
                relative_path=f"reject-{index}.aiff",
                content=f"reject {index}".encode(),
                role="candidate",
                media_type="audio",
                trigger_id="T5",
                duration_ms=2_000,
            )
            for index in range(3)
        ]
        for asset in accepted:
            _review(
                manager,
                storage,
                output_root,
                playthrough_id,
                asset,
                "accept",
                "human verified final media",
            )
        for asset in rejected:
            _review(
                manager,
                storage,
                output_root,
                playthrough_id,
                asset,
                "reject",
                "human rejected this candidate",
            )
        storage.finish_run(
            run["run_id"],
            status="completed",
            finished_at_utc="2026-10-16T00:10:00Z",
        )
    finally:
        storage.close()


def test_manifest_uses_human_accepts_and_clears_only_factually(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _ready_fixture(root, store_root)

    result = build_release_manifest(
        root=root,
        storage_roots=[store_root],
        generated_at_utc="2026-10-16T01:00:00Z",
    )

    assert result.release_ready is True
    assert result.known_blockers == ()
    assert "stale manual blocker" not in result.manifest["known_blockers"]
    assert set(result.manifest["final_media"]) == {
        "reference_visual",
        "reference_voice",
        "T1",
        "T2",
        "T3",
        "T4",
        "T5",
        "T6",
    }
    assert {item["media_type"] for item in result.manifest["final_media"]["T2"]} == {"image", "audio"}
    assert next(item for item in result.manifest["final_media"]["T5"] if item["media_type"] == "audio")["duration_ms"] == 12_000
    assert len(result.manifest["rejected_results"]) == 3
    assert all(item["decided_by"] == "human" for item in result.manifest["rejected_results"])


def test_latest_human_reject_removes_asset_from_final_media(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _write_base_manifest(root)
    output_root = root / "outputs"
    storage, manager, playthrough_id, branch_id = _start_storage(store_root)
    try:
        run = _begin_run(storage, playthrough_id, branch_id)
        asset = _write_asset(
            storage=storage,
            output_root=output_root,
            run_id=run["run_id"],
            relative_path="T5.aiff",
            content=b"T5",
            role="candidate",
            media_type="audio",
            trigger_id="T5",
            duration_ms=5_000,
        )
        _review(manager, storage, output_root, playthrough_id, asset, "accept", "ok")
        _review(
            manager,
            storage,
            output_root,
            playthrough_id,
            asset,
            "reject",
            "human later rejected the clip",
        )
        storage.finish_run(run["run_id"], status="completed")
    finally:
        storage.close()

    manifest = build_release_manifest(
        root=root,
        storage_roots=[store_root],
        generated_at_utc="2026-10-16T01:00:00Z",
    ).manifest

    assert "T5" not in manifest["final_media"]
    assert manifest["rejected_results"][0]["asset_id"] == asset["asset_id"]
    assert any("missing_final_group: T5" == item for item in manifest["known_blockers"])


def test_manifest_keeps_unknown_cost_null_and_blocks_tamper(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _write_base_manifest(root)
    output_root = root / "outputs"
    storage, manager, playthrough_id, branch_id = _start_storage(store_root)
    try:
        run = _begin_run(storage, playthrough_id, branch_id)
        call = storage.record_provider_call(
            run_id=run["run_id"],
            purpose="generate T5",
            provider="fixture",
            mode="local",
            request_sha256="c" * 64,
            billing_state="unknown",
            status="failed",
        )
        asset = _write_asset(
            storage=storage,
            output_root=output_root,
            run_id=run["run_id"],
            provider_call_id=call["call_id"],
            relative_path="T5.aiff",
            content=b"original",
            role="candidate",
            media_type="audio",
            trigger_id="T5",
            duration_ms=5_000,
        )
        _review(manager, storage, output_root, playthrough_id, asset, "accept", "ok")
        (output_root / "T5.aiff").write_bytes(b"tampered")
        storage.finish_run(run["run_id"], status="completed")
    finally:
        storage.close()

    result = build_release_manifest(
        root=root,
        storage_roots=[store_root],
        generated_at_utc="2026-10-16T01:00:00Z",
    )

    assert "T5" not in result.manifest["final_media"]
    assert any(item.startswith("asset_tamper:") for item in result.known_blockers)
    assert any(
        item.startswith("unknown_unreconciled_billing:")
        for item in result.known_blockers
    )


def test_atomic_write_and_dry_run_diff_are_available(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _ready_fixture(root, store_root)
    manifest_path = root / "outputs" / "manifest.json"
    result = build_release_manifest(
        root=root,
        storage_roots=[store_root],
        generated_at_utc="2026-10-16T01:00:00Z",
    )

    assert "--- current manifest" in result.diff
    write_manifest_atomic(manifest_path, result.manifest)
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["release_ready"] is True


def test_compatible_multi_store_multi_branch_release_is_allowed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    first_store = tmp_path / "image-store"
    second_store = tmp_path / "voice-store"
    _ready_fixture(root, first_store)
    output_root = root / "outputs"
    storage, manager, playthrough_id, branch_id = _start_storage(second_store)
    try:
        run = _begin_run(storage, playthrough_id, branch_id)
        rejected = _write_asset(
            storage=storage,
            output_root=output_root,
            run_id=run["run_id"],
            relative_path="voice-store-reject.aiff",
            content=b"unique rejected voice",
            role="candidate",
            media_type="audio",
            trigger_id="T5",
            duration_ms=2_000,
        )
        _review(
            manager,
            storage,
            output_root,
            playthrough_id,
            rejected,
            "reject",
            "human rejected a distinct voice candidate",
        )
        storage.finish_run(run["run_id"], status="completed")
    finally:
        storage.close()

    result = build_release_manifest(
        root=root,
        storage_roots=[first_store, second_store],
        generated_at_utc="2026-10-16T01:00:00Z",
    )

    assert result.release_ready is True
    assert len(result.manifest["rejected_results"]) == 4
    assert not any("multi_store" in item for item in result.known_blockers)
    assert not any("multi_branch" in item for item in result.known_blockers)


def test_multi_store_material_mismatch_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    first_store = tmp_path / "first-store"
    second_store = tmp_path / "second-store"
    _ready_fixture(root, first_store)
    output_root = root / "outputs"
    storage = FileStorage(second_store)
    backend = cast(StorageBackend, storage)
    manager = PlaythroughManager(backend)
    try:
        started = manager.start(
            title="different materials",
            materials_manifest_sha256=HASH_B,
        )
        run = storage.begin_run(
            playthrough_id=started["playthrough"]["playthrough_id"],
            branch_id=started["branch"]["branch_id"],
            mode="local",
            requested_storage="file",
            effective_storage="file",
            materials_manifest_sha256=HASH_B,
            config_sha256=HASH_B,
        )
        rejected = _write_asset(
            storage=storage,
            output_root=output_root,
            run_id=run["run_id"],
            relative_path="different-material-reject.aiff",
            content=b"different material voice",
            role="candidate",
            media_type="audio",
            trigger_id="T5",
            duration_ms=2_000,
        )
        _review(
            manager,
            storage,
            output_root,
            started["playthrough"]["playthrough_id"],
            rejected,
            "reject",
            "human rejected media from a different material set",
        )
        storage.finish_run(run["run_id"], status="completed")
    finally:
        storage.close()

    result = build_release_manifest(
        root=root,
        storage_roots=[first_store, second_store],
        generated_at_utc="2026-10-16T01:00:00Z",
    )

    assert result.release_ready is False
    assert any(
        item.startswith("materials_manifest_mismatch:")
        for item in result.known_blockers
    )


def test_multiple_accepted_assets_for_single_final_group_are_blocked(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    first_store = tmp_path / "first-store"
    second_store = tmp_path / "second-store"
    _ready_fixture(root, first_store)
    output_root = root / "outputs"
    storage, manager, playthrough_id, branch_id = _start_storage(second_store)
    try:
        run = _begin_run(storage, playthrough_id, branch_id)
        extra_t5 = _write_asset(
            storage=storage,
            output_root=output_root,
            run_id=run["run_id"],
            relative_path="extra-T5.aiff",
            content=b"second accepted T5",
            role="candidate",
            media_type="audio",
            trigger_id="T5",
            duration_ms=4_000,
        )
        _review(
            manager,
            storage,
            output_root,
            playthrough_id,
            extra_t5,
            "accept",
            "human accepted a competing final choice",
        )
        storage.finish_run(run["run_id"], status="completed")
    finally:
        storage.close()

    result = build_release_manifest(
        root=root,
        storage_roots=[first_store, second_store],
        generated_at_utc="2026-10-16T01:00:00Z",
    )

    assert result.release_ready is False
    assert any(
        item == "multiple_final_group: T5 audio has 2 accepted assets"
        for item in result.known_blockers
    )


def test_success_with_unknown_cost_keeps_null_without_retry_blocker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _write_base_manifest(root)
    output_root = root / "outputs"
    storage, manager, playthrough_id, branch_id = _start_storage(store_root)
    try:
        run = _begin_run(storage, playthrough_id, branch_id)
        call = storage.record_provider_call(
            run_id=run["run_id"],
            purpose="generate T5",
            provider="fixture",
            mode="local",
            request_sha256="c" * 64,
            billing_state="unknown",
            status="success",
            cost_amount_micros=None,
        )
        asset = _write_asset(
            storage=storage,
            output_root=output_root,
            run_id=run["run_id"],
            provider_call_id=call["call_id"],
            relative_path="T5-unknown-cost.aiff",
            content=b"successful audio with unavailable cost",
            role="candidate",
            media_type="audio",
            trigger_id="T5",
            duration_ms=5_000,
        )
        _review(
            manager,
            storage,
            output_root,
            playthrough_id,
            asset,
            "accept",
            "human accepted a successful candidate with unknown cost",
        )
        storage.finish_run(run["run_id"], status="completed")
    finally:
        storage.close()

    result = build_release_manifest(
        root=root,
        storage_roots=[store_root],
        generated_at_utc="2026-10-16T01:00:00Z",
    )

    record = result.manifest["final_media"]["T5"][0]
    assert record["billing_state"] == "unknown"
    assert record["cost_amount_micros"] is None
    assert not any(
        item.startswith("unknown_unreconciled_billing:")
        for item in result.known_blockers
    )


def test_failed_unknown_billing_call_with_explicit_block_is_reconciled(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _ready_fixture(root, store_root)
    storage = FileStorage(store_root)
    try:
        run = storage.list_runs()[0]
        call = storage.record_provider_call(
            run_id=run["run_id"],
            purpose="failed visual attempt",
            provider="fixture",
            mode="local",
            request_sha256="e" * 64,
            billing_state="unknown",
            status="failed",
        )
        storage.record_decision(
            run_id=run["run_id"],
            subject_kind="run",
            subject_id=run["run_id"],
            action="block",
            reason="unknown billing was reconciled without retry",
            decided_by="rule",
        )
        assert call["billing_state"] == "unknown"
    finally:
        storage.close()

    result = build_release_manifest(
        root=root,
        storage_roots=[store_root],
        generated_at_utc="2026-10-16T01:00:00Z",
    )

    assert result.release_ready is True
    assert not any(
        item.startswith("unknown_unreconciled_billing:")
        for item in result.known_blockers
    )


def test_default_manifest_sync_is_idempotent_and_path_portable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _ready_fixture(root, store_root)
    manifest_path = root / "outputs" / "manifest.json"

    first = build_release_manifest(root=root, storage_roots=[store_root])
    write_manifest_atomic(manifest_path, first.manifest)
    second = build_release_manifest(root=root, storage_roots=[store_root])

    assert second.changed is False
    assert second.diff == ""
    assert "generated_at_utc" not in second.manifest
    assert second.manifest["release_manifest_source"] == {
        "asset_count": 13,
        "asset_root": "outputs",
        "decision_count": 13,
        "storage_sources": ["file_store:0"],
    }
    assert str(tmp_path) not in json.dumps(second.manifest)
