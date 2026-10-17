"""Backend-neutral export, rebuild, and parity helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .common import StorageConflictError, semantic_snapshot_digest


class PortableStorage(Protocol):
    """Narrow structural contract required for export and rebuild."""

    def export_snapshot(self) -> dict[str, Any]: ...

    def import_snapshot(self, snapshot: Mapping[str, Any]) -> None: ...


def rebuild_storage(source: PortableStorage, target: PortableStorage) -> dict[str, Any]:
    """Rebuild ``target`` from the source's complete portable snapshot.

    The target must be empty. This prevents an accidental merge from hiding
    stale history that exists only in the destination.
    """

    before = target.export_snapshot()
    if any(before["collections"].values()):
        raise StorageConflictError("rebuild target must be empty")
    snapshot = source.export_snapshot()
    target.import_snapshot(snapshot)
    result = verify_parity(source, target)
    if not result["equal"]:  # pragma: no cover - import contract should prevent this
        raise StorageConflictError("rebuilt storage does not match source")
    return result


def verify_parity(left: PortableStorage, right: PortableStorage) -> dict[str, Any]:
    left_snapshot = left.export_snapshot()
    right_snapshot = right.export_snapshot()
    left_digest = semantic_snapshot_digest(left_snapshot)
    right_digest = semantic_snapshot_digest(right_snapshot)
    counts = {
        name: {
            "left": len(left_snapshot["collections"].get(name, [])),
            "right": len(right_snapshot["collections"].get(name, [])),
        }
        for name in sorted(
            set(left_snapshot["collections"]) | set(right_snapshot["collections"])
        )
    }
    return {
        "equal": left_snapshot == right_snapshot,
        "left_digest": left_digest,
        "right_digest": right_digest,
        "counts": counts,
    }
