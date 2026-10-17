"""Portable file backend with immutable records and atomic mutable pointers.

This module deliberately never imports ``sqlite3``. Each history record is one
canonical JSON file created once; heads and save slots use atomic replacement.
The layout favors crash recovery and auditable diffs over query speed.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from .common import (
    StorageBusyError,
    StorageConflictError,
    StorageCorruptError,
    StorageError,
    StorageReadOnlyError,
    canonical_dumps,
)
from .memory import ALL_COLLECTIONS, IMMUTABLE_COLLECTIONS, MemoryStorage

IDENTITY_FIELDS = {
    "playthroughs": "playthrough_id",
    "branches": "branch_id",
    "story_events": "story_event_id",
    "checkpoints": "checkpoint_id",
    "feature_selections": "selection_id",
    "runs": "run_id",
    "provider_calls": "call_id",
    "assets": "asset_id",
    "decisions": "decision_id",
    "heads": "playthrough_id",
}


class FileStorage(MemoryStorage):
    """Durable no-SQLite backend implementing the full storage protocol."""

    backend_name = "file"

    def __init__(self, root: str | Path, *, read_only: bool = False) -> None:
        super().__init__()
        self.root = Path(root).expanduser().resolve()
        self.read_only = bool(read_only)
        self._writer_lock_handle: Any | None = None
        self._loading = True
        if not self.root.exists():
            if self.read_only:
                raise StorageReadOnlyError(f"file store does not exist: {self.root}")
            self._mkdir(self.root)
        if not self.root.is_dir():
            raise StorageCorruptError(
                f"file store root is not a directory: {self.root}"
            )
        if not self.read_only:
            self._acquire_writer_lock()
        try:
            marker = self.root / "format.json"
            if marker.exists():
                data = self._read_json(marker)
                if data != {"format": "nining-file-storage", "version": 1}:
                    raise StorageCorruptError(
                        "unsupported or corrupt file storage format"
                    )
            elif self.read_only:
                raise StorageCorruptError("file storage format marker is missing")
            else:
                self._write_atomic(
                    marker, {"format": "nining-file-storage", "version": 1}
                )
            snapshot = self._read_snapshot()
            self.import_snapshot(snapshot)
            self._loading = False
        except Exception:
            self._release_writer_lock()
            raise

    def _acquire_writer_lock(self) -> None:
        path = self.root / ".writer.lock"
        try:
            handle = path.open("a+b")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise StorageBusyError(
                "file storage already has an active writer"
            ) from exc
        except OSError as exc:
            raise StorageError("cannot acquire file storage writer lock") from exc
        self._writer_lock_handle = handle

    def _release_writer_lock(self) -> None:
        handle = self._writer_lock_handle
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._writer_lock_handle = None

    def _mkdir(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise StorageReadOnlyError(
                f"cannot create storage directory: {path}"
            ) from exc
        except OSError as exc:
            self._raise_io(exc, path)

    @staticmethod
    def _raise_io(exc: OSError, path: Path) -> None:
        if exc.errno in {errno.EACCES, errno.EROFS, errno.EPERM}:
            raise StorageReadOnlyError(f"storage path is read-only: {path}") from exc
        raise StorageError(
            f"file storage I/O failure at {path.name}: {exc.strerror or exc}"
        ) from exc

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageCorruptError(
                f"invalid JSON record: {path.relative_to(self.root)}"
            ) from exc
        except OSError as exc:
            self._raise_io(exc, path)
        if not isinstance(value, dict):
            raise StorageCorruptError(
                f"JSON record is not an object: {path.relative_to(self.root)}"
            )
        return value

    def _write_atomic(self, path: Path, value: Mapping[str, Any]) -> None:
        if self.read_only:
            raise StorageReadOnlyError("file storage was opened read-only")
        self._mkdir(path.parent)
        temporary = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
        payload = f"{canonical_dumps(value)}\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError:
                # Directory fsync is unavailable on some filesystems; the file
                # itself is still flushed and atomically installed.
                pass
        except FileExistsError as exc:
            raise StorageConflictError(
                f"temporary storage collision: {path.name}"
            ) from exc
        except OSError as exc:
            self._raise_io(exc, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _record_path(self, collection: str, record: Mapping[str, Any]) -> Path:
        if collection == "save_slots":
            identity = sha256(
                f"{record['playthrough_id']}\0{record['slot_name']}".encode()
            ).hexdigest()
            return self.root / "mutable" / collection / f"{identity}.json"
        field = IDENTITY_FIELDS[collection]
        return (
            self.root
            / ("records" if collection in IMMUTABLE_COLLECTIONS else "mutable")
            / collection
            / f"{record[field]}.json"
        )

    def _persist_append(
        self, collection: str, key: Any, record: Mapping[str, Any]
    ) -> None:
        if self._loading:
            return
        path = self._record_path(collection, record)
        if path.exists():
            existing = self._read_json(path)
            if existing == dict(record):
                return
            raise StorageConflictError(
                f"immutable file record already differs: {path.name}"
            )
        self._write_atomic(path, record)

    def _persist_mutable(
        self, collection: str, key: Any, record: Mapping[str, Any]
    ) -> None:
        if self._loading:
            return
        self._write_atomic(self._record_path(collection, record), record)

    def _read_snapshot(self) -> dict[str, Any]:
        collections: dict[str, list[dict[str, Any]]] = {
            name: [] for name in ALL_COLLECTIONS
        }
        for collection in ALL_COLLECTIONS:
            area = "records" if collection in IMMUTABLE_COLLECTIONS else "mutable"
            directory = self.root / area / collection
            if not directory.exists():
                continue
            if not directory.is_dir():
                raise StorageCorruptError(
                    f"collection path is not a directory: {collection}"
                )
            for path in sorted(directory.glob("*.json")):
                if path.name.startswith("."):
                    continue
                collections[collection].append(self._read_json(path))
        return {"format_version": 1, "collections": collections}

    def close(self) -> None:
        if self._closed:
            return
        self._release_writer_lock()
        super().close()

    def integrity_check(self) -> dict[str, Any]:
        result = super().integrity_check()
        disk_errors: list[str] = []
        try:
            disk_snapshot = self._read_snapshot()
            mirror = MemoryStorage()
            mirror.import_snapshot(disk_snapshot)
            if mirror.export_snapshot() != self.export_snapshot():
                disk_errors.append("memory mirror differs from canonical files")
        except StorageError as exc:
            disk_errors.append(str(exc))
        result["errors"].extend(disk_errors)
        result["ok"] = not result["errors"]
        return result
