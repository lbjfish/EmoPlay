"""Offline-first storage package.

Importing this package does not import ``sqlite3``. SQLite is loaded lazily only
when the strict/auto backend is requested.
"""

from __future__ import annotations

from .common import (
    MigrationError,
    StorageBusyError,
    StorageConflictError,
    StorageCorruptError,
    StorageError,
    StorageNotFoundError,
    StorageReadOnlyError,
    StorageUnavailableError,
    StorageValidationError,
    canonical_dumps,
    canonical_sha256,
)
from .factory import open_from_config, open_storage
from .file import FileStorage
from .memory import MemoryStorage
from .portable import rebuild_storage, verify_parity
from .protocol import StorageBackend

__all__ = [
    "FileStorage",
    "MemoryStorage",
    "MigrationError",
    "SQLiteStorage",
    "StorageBackend",
    "StorageBusyError",
    "StorageConflictError",
    "StorageCorruptError",
    "StorageError",
    "StorageNotFoundError",
    "StorageReadOnlyError",
    "StorageUnavailableError",
    "StorageValidationError",
    "canonical_dumps",
    "canonical_sha256",
    "open_from_config",
    "open_storage",
    "rebuild_storage",
    "verify_parity",
]


def __getattr__(name: str):
    if name == "SQLiteStorage":
        from .sqlite import SQLiteStorage

        return SQLiteStorage
    raise AttributeError(name)
