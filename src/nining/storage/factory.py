"""Storage selection with explicit and auditable fallback behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from .common import StorageError, StorageUnavailableError
from .file import FileStorage
from .memory import MemoryStorage
from .protocol import StorageBackend


def _fallback(name: str, *, file_root: str | Path) -> StorageBackend:
    if name == "file":
        return cast(StorageBackend, FileStorage(file_root))
    if name == "memory":
        return cast(StorageBackend, MemoryStorage())
    if name == "sqlite":
        raise StorageUnavailableError(
            "sqlite fallback requires an explicit sqlite_path"
        )
    raise StorageUnavailableError(f"unsupported fallback backend: {name}")


def open_storage(
    backend: str,
    *,
    sqlite_path: str | Path = "work/state/nining.sqlite3",
    file_root: str | Path = "work/state/files",
    fallback_backend: str = "file",
    busy_timeout_ms: int = 5000,
    wal: bool = False,
    read_only: bool = False,
) -> StorageBackend:
    """Open one backend; only ``auto`` may degrade to another backend."""

    if backend == "memory":
        if read_only:
            raise StorageUnavailableError("memory backend has no read-only mode")
        return cast(StorageBackend, MemoryStorage())
    if backend == "file":
        return cast(StorageBackend, FileStorage(file_root, read_only=read_only))
    if backend == "sqlite":
        try:
            from .sqlite import SQLiteStorage
        except (ImportError, ModuleNotFoundError) as exc:
            raise StorageUnavailableError(
                "Python sqlite3 module is unavailable"
            ) from exc
        return cast(
            StorageBackend,
            SQLiteStorage(
                sqlite_path,
                busy_timeout_ms=busy_timeout_ms,
                wal=wal,
                read_only=read_only,
            ),
        )
    if backend != "auto":
        raise StorageUnavailableError(f"unsupported storage backend: {backend}")
    if read_only:
        # Read-only auto selection must never silently switch to a different
        # source of truth. Callers must state which persisted store to inspect.
        raise StorageUnavailableError("auto backend is not allowed in read-only mode")
    sqlite_exists = (
        str(sqlite_path) == ":memory:" or Path(sqlite_path).expanduser().exists()
    )
    file_exists = (Path(file_root).expanduser() / "format.json").is_file()
    if file_exists and not sqlite_exists:
        selected = cast(StorageBackend, FileStorage(file_root))
        selected.fallback_reason = (
            "sqlite index absent; using portable file fact source"
        )
        return selected
    try:
        selected = open_storage(
            "sqlite",
            sqlite_path=sqlite_path,
            file_root=file_root,
            busy_timeout_ms=busy_timeout_ms,
            wal=wal,
        )
        return selected
    except (StorageError, ImportError, ModuleNotFoundError, OSError) as exc:
        if sqlite_exists and isinstance(exc, StorageError):
            raise StorageUnavailableError(
                "existing SQLite state could not be opened; refusing to hide it behind a fallback"
            ) from exc
        if fallback_backend == "sqlite":
            raise StorageUnavailableError(
                "SQLite auto selection failed and fallback is sqlite"
            ) from exc
        selected = _fallback(fallback_backend, file_root=file_root)
        selected.fallback_reason = f"sqlite unavailable: {type(exc).__name__}"
        return selected


def open_from_config(config: Any, *, read_only: bool = False) -> StorageBackend:
    """Open from ``nining.config.StorageConfig`` without importing it here."""

    return open_storage(
        config.backend,
        sqlite_path=config.sqlite_path,
        file_root=config.file_root,
        fallback_backend=config.fallback_backend,
        busy_timeout_ms=config.busy_timeout_ms,
        wal=config.wal,
        read_only=read_only,
    )
