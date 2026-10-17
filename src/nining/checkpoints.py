"""Checkpoint state normalization independent of any persistence backend."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .storage.common import StorageValidationError, canonical_sha256, json_copy


@dataclass(frozen=True)
class CheckpointState:
    """Portable state required to render or resume one story position."""

    cursor: Mapping[str, Any] = field(default_factory=dict)
    visible_facts: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    promises: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    features: Mapping[str, Any] = field(default_factory=dict)
    accepted_assets: Sequence[str] = field(default_factory=tuple)
    view_mode: str = "story"
    extra: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return normalize_checkpoint_state(
            {
                "schema_version": self.schema_version,
                "cursor": dict(self.cursor),
                "visible_facts": list(self.visible_facts),
                "promises": list(self.promises),
                "features": dict(self.features),
                "accepted_assets": list(self.accepted_assets),
                "view_mode": self.view_mode,
                "extra": dict(self.extra),
            }
        )

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())


def normalize_checkpoint_state(
    state: Mapping[str, Any] | CheckpointState,
) -> dict[str, Any]:
    if isinstance(state, CheckpointState):
        return state.to_dict()
    if not isinstance(state, Mapping):
        raise StorageValidationError("checkpoint state must be an object")
    normalized = json_copy(state)
    defaults: dict[str, Any] = {
        "schema_version": "1",
        "cursor": {},
        "visible_facts": [],
        "promises": [],
        "features": {},
        "accepted_assets": [],
        "view_mode": "story",
        "extra": {},
    }
    for key, value in defaults.items():
        normalized.setdefault(key, value)
    if (
        not isinstance(normalized["schema_version"], str)
        or not normalized["schema_version"]
    ):
        raise StorageValidationError("checkpoint schema_version must be a string")
    if not isinstance(normalized["cursor"], dict):
        raise StorageValidationError("checkpoint cursor must be an object")
    if not isinstance(normalized["visible_facts"], list):
        raise StorageValidationError("checkpoint visible_facts must be an array")
    if not isinstance(normalized["promises"], list):
        raise StorageValidationError("checkpoint promises must be an array")
    if not isinstance(normalized["features"], dict):
        raise StorageValidationError("checkpoint features must be an object")
    if not isinstance(normalized["accepted_assets"], list) or not all(
        isinstance(item, str) for item in normalized["accepted_assets"]
    ):
        raise StorageValidationError(
            "checkpoint accepted_assets must be an array of IDs"
        )
    if normalized["view_mode"] not in {"story", "god"}:
        raise StorageValidationError("checkpoint view_mode must be story or god")
    if not isinstance(normalized["extra"], dict):
        raise StorageValidationError("checkpoint extra must be an object")
    return normalized


def checkpoint_state_sha256(state: Mapping[str, Any] | CheckpointState) -> str:
    return canonical_sha256(normalize_checkpoint_state(state))
