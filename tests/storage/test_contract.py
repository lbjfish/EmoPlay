from __future__ import annotations

import pytest

from nining.checkpoints import checkpoint_state_sha256
from nining.storage import (
    StorageConflictError,
    StorageValidationError,
)
from nining.storage.protocol import StorageBackend

from .conftest import HASH_C, HASH_D, begin_mock_run, start_story


def test_backend_satisfies_protocol_and_game_contract(backend):
    assert isinstance(backend, StorageBackend)
    manager, started = start_story(backend)
    play_id = started["playthrough"]["playthrough_id"]
    branch_id = started["branch"]["branch_id"]

    advanced = manager.append_event(
        play_id,
        event_type="trigger_reached",
        actor="user",
        payload={"trigger_id": "T1", "地点": "餐馆"},
        checkpoint_kind="trigger",
        checkpoint_state={"cursor": {"trigger_id": "T1"}, "view_mode": "god"},
        checkpoint_label="T1 before image",
    )
    slot = manager.save(play_id, slot_name="before-t1-image", label="生成前")
    selected = manager.select_feature(
        play_id,
        feature_key="image.provider",
        option_key="placeholder",
        selected_by="capability",
        effective=False,
        config={"reason": "offline"},
    )

    assert slot["checkpoint_id"] == advanced["checkpoint"]["checkpoint_id"]
    assert selected["selection"]["effective"] is False
    assert (
        backend.get_head(play_id)["head_event_id"]
        == selected["event"]["story_event_id"]
    )
    assert [event["event_type"] for event in manager.history(play_id)] == [
        "playthrough_started",
        "trigger_reached",
        "feature_selected",
    ]
    checkpoint = backend.get_checkpoint(play_id, slot["checkpoint_id"])
    assert checkpoint["state_sha256"] == checkpoint_state_sha256(checkpoint["state"])
    assert backend.list_branches(play_id)[0]["branch_id"] == branch_id
    assert backend.integrity_check()["ok"] is True


def test_immutable_records_are_idempotent_but_conflicts_fail(backend):
    _, started = start_story(backend)
    original = started["playthrough"]
    assert backend.create_playthrough(**original) == original
    with pytest.raises(StorageConflictError):
        backend.create_playthrough(**{**original, "title": "different"})


def test_secret_bearing_fields_and_unsafe_paths_are_rejected(backend):
    with pytest.raises(StorageValidationError, match="secret-bearing"):
        backend.create_playthrough(
            playthrough_id="play_secret",
            title="unsafe",
            materials_manifest_sha256="a" * 64,
            metadata={"api_key": "must-not-persist"},
        )
    with pytest.raises(StorageValidationError, match="secret-bearing"):
        backend.create_playthrough(
            playthrough_id="play_secret_alias",
            title="unsafe alias",
            materials_manifest_sha256="a" * 64,
            metadata={"x-client-secret-header": "must-not-persist"},
        )

    _, started = start_story(backend)
    run = begin_mock_run(backend, started)
    with pytest.raises(StorageValidationError, match="run root"):
        backend.register_asset(
            run_id=run["run_id"],
            role="placeholder",
            media_type="image",
            relative_path="../escape.png",
            size_bytes=0,
            sha256=HASH_D,
            provenance="deterministic",
            final_eligible=False,
        )
    with pytest.raises(StorageValidationError, match="full URL"):
        backend.record_provider_call(
            run_id=run["run_id"],
            purpose="image",
            provider="azure",
            mode="azure",
            request_sha256=HASH_C,
            endpoint_alias="https://private.example.invalid/path",
        )
    with pytest.raises(StorageValidationError, match="must be a boolean"):
        backend.register_asset(
            run_id=run["run_id"],
            role="placeholder",
            media_type="image",
            relative_path="placeholder.png",
            size_bytes=1,
            sha256=HASH_D,
            provenance="deterministic",
            final_eligible="false",
        )
    with pytest.raises(StorageValidationError, match="cannot be final eligible"):
        backend.register_asset(
            run_id=run["run_id"],
            role="placeholder",
            media_type="image",
            relative_path="placeholder-final.png",
            size_bytes=1,
            sha256=HASH_D,
            provenance="deterministic",
            final_eligible=True,
        )


def test_historical_generation_records_are_append_only(backend):
    _, started = start_story(backend)
    play_id = started["playthrough"]["playthrough_id"]
    branch_id = started["branch"]["branch_id"]
    run = begin_mock_run(backend, started)
    call = backend.record_provider_call(
        run_id=run["run_id"],
        purpose="T1 image candidate",
        provider="fixture",
        model="fixture-v1",
        mode="mock",
        endpoint_alias="FIXTURE_IMAGE",
        request_sha256=HASH_C,
        prompt_sha256=HASH_D,
        parameters={"seed": 7},
        usage={"images": 1},
        billing_state="zero",
        status="success",
    )
    asset = backend.register_asset(
        run_id=run["run_id"],
        provider_call_id=call["call_id"],
        role="placeholder",
        media_type="image",
        relative_path="candidates/T1-fixture.png",
        mime_type="image/png",
        size_bytes=42,
        width_px=64,
        height_px=64,
        sha256=HASH_D,
        provenance="fixture",
        final_eligible=False,
    )
    decision = backend.record_decision(
        run_id=run["run_id"],
        subject_kind="asset",
        subject_id=asset["asset_id"],
        action="reject",
        reason="fixture cannot be final",
        decided_by="rule",
    )

    assert backend.list_provider_calls(play_id, branch_id=branch_id) == [call]
    assert backend.list_assets(play_id, branch_id=branch_id) == [asset]
    assert backend.list_decisions(play_id, branch_id=branch_id) == [decision]


def test_run_has_one_idempotent_terminal_transition(backend):
    _, started = start_story(backend)
    run = begin_mock_run(backend, started)

    completed = backend.finish_run(
        run["run_id"],
        status="completed",
        finished_at_utc="2026-10-15T12:00:00Z",
    )
    assert completed["status"] == "completed"
    assert completed["finished_at_utc"] == "2026-10-15T12:00:00.000000Z"
    assert backend.finish_run(
        run["run_id"],
        status="completed",
        finished_at_utc="2026-10-15T12:00:00Z",
    ) == completed

    with pytest.raises(StorageConflictError, match="cannot be rewritten"):
        backend.finish_run(
            run["run_id"],
            status="failed",
            safe_error="late rewrite",
            finished_at_utc="2026-10-15T12:00:01Z",
        )


def test_same_event_id_cannot_be_rewritten(backend):
    _, started = start_story(backend)
    event = started["event"]
    assert backend.append_story_event(**event) == event
    with pytest.raises(StorageConflictError):
        backend.append_story_event(**{**event, "payload": {"changed": True}})
