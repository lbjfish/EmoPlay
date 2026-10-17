from __future__ import annotations

from pathlib import Path

import pytest

from nining.storage import (
    FileStorage,
    MemoryStorage,
    StorageConflictError,
    rebuild_storage,
    verify_parity,
)
from nining.storage.sqlite import SQLiteStorage

from .conftest import start_story


def test_snapshot_import_orders_retry_and_superseding_decision_dependencies():
    source = MemoryStorage()
    _manager, started = start_story(source)
    run = source.begin_run(
        playthrough_id=started["playthrough"]["playthrough_id"],
        branch_id=started["branch"]["branch_id"],
        mode="mock",
        requested_storage="memory",
        effective_storage="memory",
        materials_manifest_sha256="a" * 64,
        config_sha256="b" * 64,
    )
    original_call = source.record_provider_call(
        call_id="call_z_original",
        run_id=run["run_id"],
        purpose="original",
        provider="fixture",
        mode="mock",
        request_sha256="c" * 64,
        billing_state="zero",
        status="failed",
    )
    source.record_provider_call(
        call_id="call_a_retry",
        run_id=run["run_id"],
        purpose="retry",
        provider="fixture",
        mode="mock",
        request_sha256="d" * 64,
        billing_state="zero",
        status="success",
        retry_of_call_id=original_call["call_id"],
    )
    original_decision = source.record_decision(
        decision_id="decision_z_original",
        run_id=run["run_id"],
        subject_kind="run",
        subject_id=run["run_id"],
        action="needs_review",
        reason="original",
        decided_by="rule",
    )
    source.record_decision(
        decision_id="decision_a_superseding",
        run_id=run["run_id"],
        subject_kind="run",
        subject_id=run["run_id"],
        action="block",
        reason="superseding",
        decided_by="rule",
        supersedes_decision_id=original_decision["decision_id"],
    )

    snapshot = source.export_snapshot()
    restored = MemoryStorage()
    restored.import_snapshot(snapshot)

    assert restored.export_snapshot() == snapshot
    source.close()
    restored.close()


def test_file_to_sqlite_rebuild_and_export_parity(tmp_path: Path):
    files = FileStorage(tmp_path / "files")
    manager, started = start_story(files)
    play_id = started["playthrough"]["playthrough_id"]
    manager.append_event(
        play_id,
        event_type="trigger_reached",
        actor="rule",
        payload={"trigger_id": "T5"},
        checkpoint_kind="trigger",
        checkpoint_state={"cursor": {"trigger_id": "T5"}},
    )
    manager.save(play_id, slot_name="before-voice")
    manager.rewind(
        play_id, slot_name="before-voice", inspect_only=False, branch_name="voice-alt"
    )

    sqlite = SQLiteStorage(tmp_path / "rebuilt.sqlite3")
    parity = rebuild_storage(files, sqlite)
    assert parity["equal"] is True
    assert parity["left_digest"] == parity["right_digest"]

    exported = FileStorage(tmp_path / "exported")
    reverse = rebuild_storage(sqlite, exported)
    assert reverse["equal"] is True
    assert verify_parity(files, exported)["equal"] is True

    with pytest.raises(StorageConflictError, match="must be empty"):
        rebuild_storage(files, exported)

    files.close()
    sqlite.close()
    exported.close()
