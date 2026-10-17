from __future__ import annotations

from pathlib import Path

import pytest

from nining.playthrough import PlaythroughManager
from nining.storage import FileStorage, MemoryStorage
from nining.storage.sqlite import SQLiteStorage

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


@pytest.fixture(params=("memory", "file", "sqlite"))
def backend(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "memory":
        storage = MemoryStorage()
    elif request.param == "file":
        storage = FileStorage(tmp_path / "files")
    else:
        storage = SQLiteStorage(tmp_path / "state.sqlite3")
    try:
        yield storage
    finally:
        storage.close()


def start_story(storage, *, title: str = "柠柠"):
    manager = PlaythroughManager(storage)
    started = manager.start(title=title, materials_manifest_sha256=HASH_A)
    return manager, started


def begin_mock_run(storage, started, *, branch_id: str | None = None):
    return storage.begin_run(
        playthrough_id=started["playthrough"]["playthrough_id"],
        branch_id=branch_id or started["branch"]["branch_id"],
        mode="mock",
        requested_storage=storage.backend_name,
        effective_storage=storage.backend_name,
        materials_manifest_sha256=HASH_A,
        config_sha256=HASH_B,
        capabilities={"image": {"state": "fixture", "final_eligible": False}},
    )
