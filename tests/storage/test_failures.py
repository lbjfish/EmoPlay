from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from nining.storage import (
    FileStorage,
    MigrationError,
    StorageBusyError,
    StorageCorruptError,
    StorageReadOnlyError,
    StorageUnavailableError,
    canonical_dumps,
    open_storage,
)
from nining.storage.sqlite import SQLiteStorage

from .conftest import HASH_A, start_story


def test_file_corruption_is_fail_closed(tmp_path: Path):
    root = tmp_path / "files"
    storage = FileStorage(root)
    _, started = start_story(storage)
    storage.close()
    play_id = started["playthrough"]["playthrough_id"]
    (root / "records" / "playthroughs" / f"{play_id}.json").write_text(
        "{truncated", encoding="utf-8"
    )
    with pytest.raises(StorageCorruptError, match="invalid JSON"):
        FileStorage(root)


def test_file_explicit_read_only_mode_allows_reads_but_rejects_writes(tmp_path: Path):
    root = tmp_path / "files"
    writer = FileStorage(root)
    _, started = start_story(writer)
    writer.close()
    reader = FileStorage(root, read_only=True)
    try:
        assert (
            reader.get_playthrough(started["playthrough"]["playthrough_id"])["title"]
            == "柠柠"
        )
        with pytest.raises(StorageReadOnlyError):
            reader.create_playthrough(
                title="no write",
                materials_manifest_sha256=HASH_A,
            )
    finally:
        reader.close()


def test_file_records_are_canonical_unicode_and_multiple_slots_stay_healthy(
    tmp_path: Path,
):
    root = tmp_path / "files"
    storage = FileStorage(root)
    manager, started = start_story(storage)
    play_id = started["playthrough"]["playthrough_id"]
    manager.save(play_id, slot_name="z-slot")
    manager.save(play_id, slot_name="a-slot")
    path = root / "records" / "playthroughs" / f"{play_id}.json"
    raw = path.read_text(encoding="utf-8")
    import json

    assert "柠柠" in raw
    assert "\\u" not in raw
    assert raw.strip() == canonical_dumps(json.loads(raw))
    assert storage.integrity_check()["ok"] is True
    storage.close()


def test_file_backend_enforces_one_active_writer(tmp_path: Path):
    root = tmp_path / "files"
    first = FileStorage(root)
    try:
        with pytest.raises(StorageBusyError, match="active writer"):
            FileStorage(root)
        reader = FileStorage(root, read_only=True)
        reader.close()
    finally:
        first.close()

    replacement = FileStorage(root)
    replacement.close()


def test_file_backend_imports_and_runs_when_sqlite_import_is_blocked():
    script = r"""
import builtins
import tempfile
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "sqlite3" or name.startswith("sqlite3."):
        raise AssertionError("sqlite3 import attempted")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from nining.storage import FileStorage
with tempfile.TemporaryDirectory() as directory:
    storage = FileStorage(directory)
    assert storage.backend_name == "file"
    storage.close()
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_auto_mode_prefers_existing_file_fact_source_over_empty_sqlite(tmp_path: Path):
    file_root = tmp_path / "files"
    writer = FileStorage(file_root)
    _, started = start_story(writer)
    writer.close()
    selected = open_storage(
        "auto",
        sqlite_path=tmp_path / "missing-index.sqlite3",
        file_root=file_root,
    )
    try:
        assert selected.backend_name == "file"
        assert "fact source" in (selected.fallback_reason or "")
        assert (
            selected.get_playthrough(started["playthrough"]["playthrough_id"])["title"]
            == "柠柠"
        )
    finally:
        selected.close()


def test_sqlite_schema_migrations_views_and_integrity(tmp_path: Path):
    storage = SQLiteStorage(tmp_path / "state.sqlite3")
    try:
        tables = {
            row[0]
            for row in storage._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        views = {
            row[0]
            for row in storage._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            )
        }
        assert {
            "schema_migrations",
            "playthroughs",
            "branches",
            "story_events",
            "checkpoints",
            "playthrough_heads",
            "save_slots",
            "feature_selections",
            "runs",
            "provider_calls",
            "assets",
            "decisions",
        } <= tables
        assert {"v_playthrough_history", "v_provider_costs"} <= views
        assert storage.integrity_check()["migration_versions"] == [1]
        assert storage.integrity_check()["ok"] is True
    finally:
        storage.close()


def test_sqlite_corrupt_database_is_rejected(tmp_path: Path):
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"this is not sqlite")
    with pytest.raises(StorageCorruptError):
        SQLiteStorage(path)


def test_auto_mode_does_not_hide_existing_corrupt_sqlite(tmp_path: Path):
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"this is not sqlite")

    with pytest.raises(StorageUnavailableError, match="refusing to hide"):
        open_storage(
            "auto",
            sqlite_path=path,
            file_root=tmp_path / "files",
            fallback_backend="file",
        )

    assert not (tmp_path / "files").exists()


def test_sqlite_read_only_allows_reads_but_rejects_writes(tmp_path: Path):
    path = tmp_path / "state.sqlite3"
    writer = SQLiteStorage(path)
    _, started = start_story(writer)
    writer.close()
    reader = SQLiteStorage(path, read_only=True)
    try:
        assert (
            reader.get_playthrough(started["playthrough"]["playthrough_id"])["title"]
            == "柠柠"
        )
        with pytest.raises(StorageReadOnlyError):
            reader.create_playthrough(
                title="no write", materials_manifest_sha256=HASH_A
            )
    finally:
        reader.close()


def test_sqlite_lock_is_reported_without_fallback(tmp_path: Path):
    path = tmp_path / "locked.sqlite3"
    first = SQLiteStorage(path, busy_timeout_ms=5)
    second = SQLiteStorage(path, busy_timeout_ms=5)
    try:
        first._conn.execute("BEGIN EXCLUSIVE")
        with pytest.raises(StorageBusyError):
            second.create_playthrough(title="locked", materials_manifest_sha256=HASH_A)
    finally:
        first._conn.rollback()
        first.close()
        second.close()


def test_migration_checksum_drift_is_rejected(tmp_path: Path):
    path = tmp_path / "drift.sqlite3"
    storage = SQLiteStorage(path)
    storage._conn.execute(
        "UPDATE schema_migrations SET sha256=? WHERE version=1", ("0" * 64,)
    )
    storage._conn.commit()
    storage.close()
    with pytest.raises(MigrationError, match="checksum drift"):
        SQLiteStorage(path)


def test_future_migration_is_rejected(tmp_path: Path):
    path = tmp_path / "future.sqlite3"
    storage = SQLiteStorage(path)
    storage._conn.execute(
        "INSERT INTO schema_migrations VALUES (999,'future',?,?)",
        ("f" * 64, "2099-01-01T00:00:00.000000Z"),
    )
    storage._conn.commit()
    storage.close()
    with pytest.raises(MigrationError, match="future migrations"):
        SQLiteStorage(path)
