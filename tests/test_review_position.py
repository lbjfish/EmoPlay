from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from nining.application import GenerationService
from nining.assets import verify_local_asset
from nining.config import load_config
from nining.loader import load_materials
from nining.playthrough import PlaythroughManager
from nining.position import StoryPositionService
from nining.review import ReviewService
from nining.storage import MemoryStorage, StorageConflictError, StorageValidationError
from nining.storage.protocol import StorageBackend
from nining.web import RepositoryWebFacade

ROOT = Path(__file__).resolve().parents[1]


def _review_fixture(tmp_path: Path):
    materials = load_materials(ROOT / "materials" / "sample")
    storage = MemoryStorage()
    backend = cast(StorageBackend, storage)
    manager = PlaythroughManager(backend)
    started = manager.start(
        title="review",
        materials_manifest_sha256=materials.manifest["sha256"],
    )
    playthrough_id = started["playthrough"]["playthrough_id"]
    run = storage.begin_run(
        playthrough_id=playthrough_id,
        branch_id=started["branch"]["branch_id"],
        mode="mock",
        requested_storage="memory",
        effective_storage="memory",
        materials_manifest_sha256=materials.manifest["sha256"],
        config_sha256="a" * 64,
    )
    content = b"verified-candidate"
    (tmp_path / "candidate.png").write_bytes(content)
    candidate = storage.register_asset(
        run_id=run["run_id"],
        trigger_id="T1",
        role="candidate",
        media_type="image",
        relative_path="candidate.png",
        mime_type="image/png",
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
        provenance="azure",
        final_eligible=True,
    )
    placeholder = storage.register_asset(
        run_id=run["run_id"],
        trigger_id="T1",
        role="placeholder",
        media_type="image",
        relative_path="placeholder.png",
        mime_type="image/png",
        size_bytes=0,
        sha256=sha256(b"").hexdigest(),
        provenance="deterministic",
        final_eligible=False,
    )
    (tmp_path / "placeholder.png").write_bytes(b"")
    return materials, storage, backend, manager, playthrough_id, candidate, placeholder


def test_human_accept_and_reject_are_checkpointed_and_hash_bound(
    tmp_path: Path,
) -> None:
    (
        _materials,
        storage,
        backend,
        manager,
        playthrough_id,
        candidate,
        placeholder,
    ) = _review_fixture(tmp_path)
    review = ReviewService(storage=backend, manager=manager, asset_root=tmp_path)
    try:
        accepted = review.review(
            playthrough_id,
            asset_id=candidate["asset_id"],
            action="accept",
            reason="Identity and scene verified by human reviewer.",
            expected_sha256=candidate["sha256"],
        )
        assert accepted["decision"]["decided_by"] == "human"
        assert accepted["decision"]["action"] == "accept"
        assert candidate["asset_id"] in accepted["checkpoint"]["state"][
            "accepted_assets"
        ]

        rejected = review.review(
            playthrough_id,
            asset_id=candidate["asset_id"],
            action="reject",
            reason="Later close review found a continuity mismatch.",
        )
        assert rejected["decision"]["action"] == "reject"
        assert rejected["decision"]["supersedes_decision_id"] == accepted[
            "decision"
        ]["decision_id"]
        assert candidate["asset_id"] not in rejected["checkpoint"]["state"][
            "accepted_assets"
        ]

        with pytest.raises(StorageValidationError, match="cannot be accepted"):
            review.review(
                playthrough_id,
                asset_id=placeholder["asset_id"],
                action="accept",
                reason="must fail",
                expected_sha256=placeholder["sha256"],
            )
        with pytest.raises(StorageValidationError, match="hash changed"):
            review.review(
                playthrough_id,
                asset_id=candidate["asset_id"],
                action="accept",
                reason="stale browser",
                expected_sha256="0" * 64,
            )
    finally:
        storage.close()


def test_asset_verifier_reports_tamper_reason(tmp_path: Path) -> None:
    _, storage, _, _, _, candidate, _ = _review_fixture(tmp_path)
    try:
        assert verify_local_asset(tmp_path, candidate).status == "ok"
        (tmp_path / "candidate.png").write_bytes(b"tampered")
        assert verify_local_asset(tmp_path, candidate).status == "size_mismatch"
    finally:
        storage.close()


def test_checkpoint_position_drives_default_dashboard_and_stale_moves(
    tmp_path: Path,
) -> None:
    materials, storage, backend, manager, playthrough_id, _, _ = _review_fixture(
        tmp_path
    )
    position = StoryPositionService(
        materials=materials, storage=backend, manager=manager
    )
    try:
        initial = position.current(playthrough_id)
        assert initial["trigger_id"] == "T1"
        moved = position.move(
            playthrough_id,
            trigger_id="T2",
            expected_checkpoint_id=initial["checkpoint_id"],
        )
        assert moved["position"]["trigger_id"] == "T2"

        facade = RepositoryWebFacade(
            materials=materials,
            manager=manager,
            storage=backend,
            playthrough_id=playthrough_id,
            asset_root=tmp_path,
            position_provider=lambda: position.current(playthrough_id),
        )
        dashboard = facade.dashboard(view_mode=None)
        assert dashboard["selected_trigger_id"] == "T2"
        assert dashboard["view_mode"] == "story"
        revisited = facade.dashboard(view_mode="story", trigger_id="T1")
        assert revisited["selected_trigger_id"] == "T1"
        assert [item["locked"] for item in revisited["timeline"]] == [
            False,
            False,
            True,
            True,
            True,
            True,
        ]
        with pytest.raises(Exception, match="future story position is locked"):
            facade.dashboard(view_mode="story", trigger_id="T4")

        with pytest.raises(StorageConflictError, match="refresh"):
            position.move(
                playthrough_id,
                trigger_id="T3",
                expected_checkpoint_id=initial["checkpoint_id"],
            )
    finally:
        storage.close()


def test_human_accepted_visual_reference_is_supplied_to_scene_edit(
    tmp_path: Path,
) -> None:
    (
        materials,
        storage,
        backend,
        manager,
        playthrough_id,
        candidate,
        _placeholder,
    ) = _review_fixture(tmp_path)
    review = ReviewService(storage=backend, manager=manager, asset_root=tmp_path)
    try:
        reference_content = b"verified-identity-reference"
        (tmp_path / "reference.png").write_bytes(reference_content)
        reference = storage.register_asset(
            run_id=candidate["run_id"],
            role="reference",
            media_type="image",
            relative_path="reference.png",
            mime_type="image/png",
            size_bytes=len(reference_content),
            sha256=sha256(reference_content).hexdigest(),
            provenance="azure",
            final_eligible=True,
        )
        review.review(
            playthrough_id,
            asset_id=candidate["asset_id"],
            action="accept",
            reason="Accept the scene itself, but never reuse it as an identity anchor.",
            expected_sha256=candidate["sha256"],
        )
        review.review(
            playthrough_id,
            asset_id=reference["asset_id"],
            action="accept",
            reason="Use this fictional identity anchor for scene continuity.",
            expected_sha256=reference["sha256"],
        )
        storage.finish_run(
            candidate["run_id"],
            status="completed",
            finished_at_utc="2026-10-16T00:00:00Z",
        )
        service = GenerationService(
            config=load_config(ROOT / "config.offline.toml"),
            materials=materials,
            storage=backend,
            output_root=tmp_path,
            trace_root=tmp_path / "logs" / "runs",
            call_log_path=tmp_path / "logs" / "provider_calls.jsonl",
        )
        outcome = service.generate("T1", playthrough_id=playthrough_id)
        assert outcome.status == "completed"
        image_call = next(
            item
            for item in storage.list_provider_calls(playthrough_id)
            if item["purpose"].startswith("generate_image")
        )
        request = image_call["parameters"]["request"]
        assert request["operation"] == "edit"
        assert len(request["image_inputs"]) == 1
        assert request["image_inputs"][0]["sha256"] == reference["sha256"]
        assert request["image_inputs"][0]["sha256"] != candidate["sha256"]
    finally:
        storage.close()
