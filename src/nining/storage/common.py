"""Shared, SQLite-free storage primitives.

The file and memory backends import this module without importing ``sqlite3``.
All persisted JSON passes through the same canonical encoder so exports can be
compared byte-for-byte at the semantic layer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4


class StorageError(RuntimeError):
    """Base class for storage failures safe to show to a caller."""


class StorageConflictError(StorageError):
    """An immutable identifier already exists with different content."""


class StorageNotFoundError(StorageError):
    """A referenced record does not exist."""


class StorageValidationError(StorageError, ValueError):
    """A record violates a portable storage invariant."""


class StorageCorruptError(StorageError):
    """Persisted state cannot be decoded or fails integrity checks."""


class StorageBusyError(StorageError):
    """The backend is locked by another writer."""


class StorageReadOnlyError(StorageError):
    """A mutation was attempted on a read-only backend."""


class StorageUnavailableError(StorageError):
    """The requested backend is unavailable in this runtime."""


class MigrationError(StorageError):
    """A SQLite schema migration cannot be safely applied."""


ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_SECRET_KEYS = {
    "authorization",
    "api-key",
    "api_key",
    "apikey",
    "cookie",
    "set-cookie",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}
FORBIDDEN_SECRET_KEY_PATTERN = re.compile(
    r"(?:authorization|api[-_ ]?key|client[-_ ]?secret|password|passwd|"
    r"access[-_ ]?token|refresh[-_ ]?token|credential|cookie)",
    re.IGNORECASE,
)


def utc_now() -> str:
    """Return a lexically sortable RFC 3339 UTC timestamp."""

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_utc(value: str | datetime | None) -> str:
    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise StorageValidationError(
                f"invalid RFC 3339 timestamp: {value!r}"
            ) from exc
    else:
        raise StorageValidationError("timestamp must be a string or datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StorageValidationError("timestamp must include a UTC offset")
    parsed = parsed.astimezone(UTC)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_dumps(value: Any) -> str:
    """Encode JSON deterministically without ASCII-escaping Chinese text."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StorageValidationError(f"value is not canonical JSON: {exc}") from exc


def json_copy(value: Any) -> Any:
    """Validate and detach a JSON value from caller-owned mutable objects."""

    assert_no_secrets(value)
    return json.loads(canonical_dumps(value))


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_dumps(value).encode("utf-8")).hexdigest()


def assert_no_secrets(value: Any, path: str = "$") -> None:
    """Reject common credential containers and Authorization values.

    This intentionally checks field names rather than guessing entropy. It
    catches accidental configuration dumps without rejecting ordinary story
    prose or hashes.
    """

    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).strip().lower()
            if key_text in FORBIDDEN_SECRET_KEYS or FORBIDDEN_SECRET_KEY_PATTERN.search(
                key_text
            ):
                raise StorageValidationError(
                    f"secret-bearing field is forbidden at {path}.{key}"
                )
            assert_no_secrets(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_secrets(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.lower().startswith("bearer "):
        raise StorageValidationError(f"Authorization value is forbidden at {path}")


def require_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise StorageValidationError(f"{field} must match {ID_RE.pattern}")
    return value


def require_nonempty(value: str, field: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageValidationError(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise StorageValidationError(f"{field} exceeds {maximum} characters")
    return value


def require_sha256(value: str, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise StorageValidationError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise StorageValidationError(f"{field} must be a boolean")
    return value


def require_relative_path(value: str, field: str = "relative_path") -> str:
    if not isinstance(value, str) or not value:
        raise StorageValidationError(f"{field} must be a non-empty path")
    if "\\" in value:
        raise StorageValidationError(f"{field} must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise StorageValidationError(f"{field} must stay below the run root")
    return path.as_posix()


def new_id(prefix: str) -> str:
    require_nonempty(prefix, "prefix", maximum=24)
    return f"{prefix}_{uuid4().hex}"


def detached(record: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(record))


def semantic_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """Hash a normalized export, excluding physical-backend metadata."""

    return canonical_sha256(snapshot)
