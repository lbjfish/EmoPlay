from __future__ import annotations

from pathlib import Path

import pytest

from nining.storage import FileStorage, StorageValidationError
from nining.storage.sqlite import SQLiteStorage

from .conftest import HASH_A, HASH_B, HASH_C, HASH_D, start_story


@pytest.mark.parametrize("kind", ("file", "sqlite"))
def test_save_restart_restore_round_trip(kind: str, tmp_path: Path):
    path = tmp_path / ("files" if kind == "file" else "state.sqlite3")
    cls = FileStorage if kind == "file" else SQLiteStorage
    first = cls(path)
    manager, started = start_story(first)
    play_id = started["playthrough"]["playthrough_id"]
    manager.append_event(
        play_id,
        event_type="trigger_reached",
        actor="user",
        payload={"trigger_id": "T3"},
        checkpoint_kind="trigger",
        checkpoint_state={
            "cursor": {"trigger_id": "T3"},
            "visible_facts": [{"id": "旺财"}],
        },
    )
    manager.save(play_id, slot_name="童年照生成前", label="T3")
    expected = first.export_snapshot()
    expected_head = first.get_head(play_id)
    first.close()

    second = cls(path)
    try:
        assert second.export_snapshot() == expected
        assert second.get_head(play_id) == expected_head
        assert (
            second.get_save_slot(play_id, "童年照生成前")["checkpoint_id"]
            == expected_head["checkpoint_id"]
        )
        assert second.integrity_check()["ok"] is True
    finally:
        second.close()


def _record_call(
    storage, started, *, branch_id: str | None = None, suffix: str = "main"
):
    run = storage.begin_run(
        playthrough_id=started["playthrough"]["playthrough_id"],
        branch_id=branch_id or started["branch"]["branch_id"],
        mode="mock",
        requested_storage=storage.backend_name,
        effective_storage=storage.backend_name,
        materials_manifest_sha256=HASH_A,
        config_sha256=HASH_B,
    )
    call = storage.record_provider_call(
        run_id=run["run_id"],
        purpose=f"candidate-{suffix}",
        provider="fixture",
        mode="mock",
        request_sha256=HASH_C,
        status="success",
        billing_state="zero",
    )
    asset = storage.register_asset(
        run_id=run["run_id"],
        provider_call_id=call["call_id"],
        role="candidate",
        media_type="image",
        relative_path=f"branches/{suffix}.png",
        size_bytes=1,
        sha256=HASH_D,
        provenance="fixture",
        final_eligible=False,
    )
    decision = storage.record_decision(
        run_id=run["run_id"],
        subject_kind="asset",
        subject_id=asset["asset_id"],
        action="needs_review",
        reason=f"review {suffix}",
        decided_by="human",
    )
    return run, call, asset, decision


def test_rewind_does_not_create_calls_and_branch_history_isolated(backend):
    manager, started = start_story(backend)
    play_id = started["playthrough"]["playthrough_id"]
    main_id = started["branch"]["branch_id"]
    manager.save(play_id, slot_name="before-generation")
    manager.append_event(
        play_id,
        event_type="candidate_requested",
        actor="user",
        payload={"candidate": "A"},
        checkpoint_kind="post_generation",
        checkpoint_state={"cursor": {"candidate": "A"}},
    )
    main_records = _record_call(backend, started, suffix="main")
    counts_before = (
        len(backend.list_provider_calls(play_id)),
        len(backend.list_assets(play_id)),
        len(backend.list_decisions(play_id)),
    )
    event_count_before = len(backend.list_story_events(play_id, main_id))

    inspected = manager.rewind(
        play_id, slot_name="before-generation", inspect_only=True
    )
    assert inspected.inspect_only is True
    assert counts_before == (
        len(backend.list_provider_calls(play_id)),
        len(backend.list_assets(play_id)),
        len(backend.list_decisions(play_id)),
    )
    assert len(backend.list_story_events(play_id, main_id)) == event_count_before

    forked = manager.rewind(
        play_id,
        slot_name="before-generation",
        inspect_only=False,
        branch_name="alternate-composition",
    )
    assert len(backend.list_provider_calls(play_id)) == 1
    alt_id = forked.branch_id
    alt_records = _record_call(backend, started, branch_id=alt_id, suffix="alternate")

    with pytest.raises(StorageValidationError, match="another run branch"):
        backend.record_provider_call(
            run_id=alt_records[0]["run_id"],
            story_event_id=started["event"]["story_event_id"],
            purpose="invalid cross-branch link",
            provider="fixture",
            mode="mock",
            request_sha256=HASH_C,
        )

    assert backend.list_provider_calls(play_id, branch_id=main_id) == [main_records[1]]
    assert backend.list_provider_calls(play_id, branch_id=alt_id) == [alt_records[1]]
    assert backend.list_assets(play_id, branch_id=main_id) == [main_records[2]]
    assert backend.list_assets(play_id, branch_id=alt_id) == [alt_records[2]]
    assert backend.list_decisions(play_id, branch_id=main_id) == [main_records[3]]
    assert backend.list_decisions(play_id, branch_id=alt_id) == [alt_records[3]]

    event_types = [
        event["event_type"] for event in manager.history(play_id, branch_id=alt_id)
    ]
    assert event_types == ["playthrough_started", "branch_forked"]
    manager.checkout(play_id, branch_id=main_id)
    assert backend.get_head(play_id)["branch_id"] == main_id


def test_repeated_rewind_uses_unique_default_branch_names(backend):
    manager, started = start_story(backend)
    play_id = started["playthrough"]["playthrough_id"]
    manager.save(play_id, slot_name="fork-point")
    first = manager.rewind(play_id, slot_name="fork-point", inspect_only=False)
    second = manager.rewind(play_id, slot_name="fork-point", inspect_only=False)
    assert first.branch_id != second.branch_id
    names = {branch["name"] for branch in backend.list_branches(play_id)}
    base = f"rewind-{started['checkpoint']['checkpoint_id'][-8:]}"
    assert {base, f"{base}-2"} <= names
