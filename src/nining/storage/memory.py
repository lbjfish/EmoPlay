"""Reference in-memory implementation of the portable storage contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import RLock
from typing import Any, Self

from .common import (
    StorageConflictError,
    StorageError,
    StorageNotFoundError,
    StorageValidationError,
    assert_no_secrets,
    canonical_sha256,
    detached,
    json_copy,
    new_id,
    normalize_utc,
    require_bool,
    require_id,
    require_nonempty,
    require_relative_path,
    require_sha256,
)

IMMUTABLE_COLLECTIONS = (
    "playthroughs",
    "branches",
    "story_events",
    "checkpoints",
    "feature_selections",
    "runs",
    "provider_calls",
    "assets",
    "decisions",
)
MUTABLE_COLLECTIONS = ("heads", "save_slots")
ALL_COLLECTIONS = IMMUTABLE_COLLECTIONS + MUTABLE_COLLECTIONS


class MemoryStorage:
    """Deterministic reference backend used by tests and mock runs."""

    backend_name = "memory"
    fallback_reason: str | None = None

    def __init__(self) -> None:
        self._lock = RLock()
        self._closed = False
        self._records: dict[str, dict[Any, dict[str, Any]]] = {
            name: {} for name in ALL_COLLECTIONS
        }

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise StorageValidationError("storage backend is closed")

    def _persist_append(
        self, collection: str, key: Any, record: Mapping[str, Any]
    ) -> None:
        """Hook for durable backends, called before updating the memory mirror."""

    def _persist_mutable(
        self, collection: str, key: Any, record: Mapping[str, Any]
    ) -> None:
        """Hook for durable backends, called before updating the memory mirror."""

    def _insert_immutable(
        self, collection: str, key: Any, record: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._ensure_open()
        candidate = detached(record)
        assert_no_secrets(candidate)
        existing = self._records[collection].get(key)
        if existing is not None:
            if existing == candidate:
                return detached(existing)
            raise StorageConflictError(
                f"{collection} identifier already has different content"
            )
        self._persist_append(collection, key, candidate)
        self._records[collection][key] = candidate
        return detached(candidate)

    def _set_mutable(
        self, collection: str, key: Any, record: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._ensure_open()
        candidate = detached(record)
        assert_no_secrets(candidate)
        self._persist_mutable(collection, key, candidate)
        self._records[collection][key] = candidate
        return detached(candidate)

    def _require(self, collection: str, key: Any, label: str) -> dict[str, Any]:
        try:
            return self._records[collection][key]
        except KeyError as exc:
            raise StorageNotFoundError(f"{label} not found") from exc

    def create_playthrough(
        self,
        *,
        title: str,
        materials_manifest_sha256: str,
        playthrough_id: str | None = None,
        status: str = "active",
        default_view_mode: str = "story",
        metadata: Mapping[str, Any] | None = None,
        created_at_utc: str | None = None,
        updated_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            identifier = require_id(playthrough_id or new_id("play"), "playthrough_id")
            if status not in {"active", "completed", "archived"}:
                raise StorageValidationError("invalid playthrough status")
            if default_view_mode not in {"story", "god"}:
                raise StorageValidationError("default_view_mode must be story or god")
            created = normalize_utc(created_at_utc)
            record = {
                "playthrough_id": identifier,
                "title": require_nonempty(title, "title"),
                "materials_manifest_sha256": require_sha256(
                    materials_manifest_sha256, "materials_manifest_sha256"
                ),
                "status": status,
                "default_view_mode": default_view_mode,
                "metadata": json_copy(metadata or {}),
                "created_at_utc": created,
                "updated_at_utc": normalize_utc(updated_at_utc or created),
            }
            return self._insert_immutable("playthroughs", identifier, record)

    def get_playthrough(self, playthrough_id: str) -> dict[str, Any]:
        with self._lock:
            return detached(
                self._require(
                    "playthroughs",
                    require_id(playthrough_id, "playthrough_id"),
                    "playthrough",
                )
            )

    def list_playthroughs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                detached(item)
                for item in sorted(
                    self._records["playthroughs"].values(),
                    key=lambda row: (row["created_at_utc"], row["playthrough_id"]),
                )
            ]

    def create_branch(
        self,
        *,
        playthrough_id: str,
        name: str,
        branch_id: str | None = None,
        parent_branch_id: str | None = None,
        forked_from_checkpoint_id: str | None = None,
        status: str = "active",
        created_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            self._require("playthroughs", playthrough_id, "playthrough")
            identifier = require_id(branch_id or new_id("branch"), "branch_id")
            name = require_nonempty(name, "name", maximum=100)
            if status not in {"active", "merged", "archived"}:
                raise StorageValidationError("invalid branch status")
            for branch in self._records["branches"].values():
                if (
                    branch["playthrough_id"] == playthrough_id
                    and branch["name"] == name
                ):
                    if branch["branch_id"] == identifier:
                        break
                    raise StorageConflictError(
                        "branch name already exists in this playthrough"
                    )
            if parent_branch_id is not None:
                parent_branch_id = require_id(parent_branch_id, "parent_branch_id")
                self._require(
                    "branches", (playthrough_id, parent_branch_id), "parent branch"
                )
            if forked_from_checkpoint_id is not None:
                checkpoint = self._require(
                    "checkpoints",
                    require_id(forked_from_checkpoint_id, "forked_from_checkpoint_id"),
                    "checkpoint",
                )
                if checkpoint["playthrough_id"] != playthrough_id:
                    raise StorageValidationError(
                        "fork checkpoint belongs to another playthrough"
                    )
                if parent_branch_id is None:
                    raise StorageValidationError(
                        "fork checkpoint requires a parent branch"
                    )
                if checkpoint["branch_id"] != parent_branch_id:
                    raise StorageValidationError(
                        "fork checkpoint is not on the parent branch"
                    )
            record = {
                "branch_id": identifier,
                "playthrough_id": playthrough_id,
                "name": name,
                "parent_branch_id": parent_branch_id,
                "forked_from_checkpoint_id": forked_from_checkpoint_id,
                "status": status,
                "created_at_utc": normalize_utc(created_at_utc),
            }
            return self._insert_immutable(
                "branches", (playthrough_id, identifier), record
            )

    def get_branch(self, playthrough_id: str, branch_id: str) -> dict[str, Any]:
        with self._lock:
            key = (
                require_id(playthrough_id, "playthrough_id"),
                require_id(branch_id, "branch_id"),
            )
            return detached(self._require("branches", key, "branch"))

    def list_branches(self, playthrough_id: str) -> list[dict[str, Any]]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            self._require("playthroughs", playthrough_id, "playthrough")
            return [
                detached(item)
                for item in sorted(
                    (
                        row
                        for row in self._records["branches"].values()
                        if row["playthrough_id"] == playthrough_id
                    ),
                    key=lambda row: (row["created_at_utc"], row["branch_id"]),
                )
            ]

    def append_story_event(
        self,
        *,
        playthrough_id: str,
        branch_id: str,
        event_type: str,
        actor: str,
        payload: Mapping[str, Any] | None = None,
        story_event_id: str | None = None,
        run_id: str | None = None,
        trigger_id: str | None = None,
        sequence_no: int | None = None,
        reversible: bool = True,
        parent_event_id: str | None = None,
        created_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            branch_id = require_id(branch_id, "branch_id")
            self._require("branches", (playthrough_id, branch_id), "branch")
            identifier = require_id(story_event_id or new_id("event"), "story_event_id")
            if actor not in {"user", "system", "rule", "model_assist"}:
                raise StorageValidationError("invalid story event actor")
            if run_id is not None:
                run_id = require_id(run_id, "run_id")
                run = self._require("runs", run_id, "run")
                if (run["playthrough_id"], run["branch_id"]) != (
                    playthrough_id,
                    branch_id,
                ):
                    raise StorageValidationError("run belongs to another branch")
            if trigger_id is not None:
                trigger_id = require_id(trigger_id, "trigger_id")
                if run_id is None:
                    raise StorageValidationError("trigger_id requires run_id")
            if parent_event_id is not None:
                parent_event_id = require_id(parent_event_id, "parent_event_id")
                self._require("story_events", parent_event_id, "parent event")
            direct = [
                row
                for row in self._records["story_events"].values()
                if row["playthrough_id"] == playthrough_id
                and row["branch_id"] == branch_id
            ]
            if sequence_no is None:
                sequence_no = (
                    0 if not direct else max(row["sequence_no"] for row in direct) + 1
                )
            if not isinstance(sequence_no, int) or sequence_no < 0:
                raise StorageValidationError(
                    "sequence_no must be a non-negative integer"
                )
            if any(row["sequence_no"] == sequence_no for row in direct):
                existing = next(
                    row for row in direct if row["sequence_no"] == sequence_no
                )
                if existing["story_event_id"] != identifier:
                    raise StorageConflictError("story event sequence already exists")
            record = {
                "story_event_id": identifier,
                "playthrough_id": playthrough_id,
                "branch_id": branch_id,
                "run_id": run_id,
                "trigger_id": trigger_id,
                "sequence_no": sequence_no,
                "event_type": require_nonempty(event_type, "event_type", maximum=100),
                "actor": actor,
                "payload": json_copy(payload or {}),
                "reversible": require_bool(reversible, "reversible"),
                "parent_event_id": parent_event_id,
                "created_at_utc": normalize_utc(created_at_utc),
            }
            return self._insert_immutable("story_events", identifier, record)

    def _direct_events(
        self, playthrough_id: str, branch_id: str
    ) -> list[dict[str, Any]]:
        return sorted(
            (
                row
                for row in self._records["story_events"].values()
                if row["playthrough_id"] == playthrough_id
                and row["branch_id"] == branch_id
            ),
            key=lambda row: (row["sequence_no"], row["story_event_id"]),
        )

    def _history(
        self, playthrough_id: str, branch_id: str, seen: set[str]
    ) -> list[dict[str, Any]]:
        if branch_id in seen:
            raise StorageCorruptError("branch ancestry cycle detected")
        seen.add(branch_id)
        branch = self._require("branches", (playthrough_id, branch_id), "branch")
        result: list[dict[str, Any]] = []
        parent = branch["parent_branch_id"]
        fork_checkpoint = branch["forked_from_checkpoint_id"]
        if parent is not None:
            parent_events = self._history(playthrough_id, parent, seen)
            if fork_checkpoint is not None:
                checkpoint = self._require(
                    "checkpoints", fork_checkpoint, "fork checkpoint"
                )
                head_event_id = checkpoint["head_event_id"]
                if head_event_id is None:
                    parent_events = []
                else:
                    positions = [
                        index
                        for index, row in enumerate(parent_events)
                        if row["story_event_id"] == head_event_id
                    ]
                    if not positions:
                        raise StorageCorruptError(
                            "fork checkpoint head is absent from parent history"
                        )
                    parent_events = parent_events[: positions[0] + 1]
            result.extend(parent_events)
        result.extend(self._direct_events(playthrough_id, branch_id))
        seen.remove(branch_id)
        return result

    def list_story_events(
        self, playthrough_id: str, branch_id: str, *, include_ancestry: bool = False
    ) -> list[dict[str, Any]]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            branch_id = require_id(branch_id, "branch_id")
            self._require("branches", (playthrough_id, branch_id), "branch")
            rows = (
                self._history(playthrough_id, branch_id, set())
                if include_ancestry
                else self._direct_events(playthrough_id, branch_id)
            )
            return [detached(item) for item in rows]

    def create_checkpoint(
        self,
        *,
        playthrough_id: str,
        branch_id: str,
        state: Mapping[str, Any],
        checkpoint_kind: str,
        checkpoint_id: str | None = None,
        head_event_id: str | None = None,
        generated_by_run_id: str | None = None,
        label: str | None = None,
        state_sha256: str | None = None,
        created_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            branch_id = require_id(branch_id, "branch_id")
            self._require("branches", (playthrough_id, branch_id), "branch")
            identifier = require_id(
                checkpoint_id or new_id("checkpoint"), "checkpoint_id"
            )
            if checkpoint_kind not in {
                "auto",
                "manual",
                "trigger",
                "pre_generation",
                "post_generation",
                "release",
            }:
                raise StorageValidationError("invalid checkpoint kind")
            if head_event_id is not None:
                head_event_id = require_id(head_event_id, "head_event_id")
                event = self._require("story_events", head_event_id, "head event")
                if (event["playthrough_id"], event["branch_id"]) != (
                    playthrough_id,
                    branch_id,
                ):
                    raise StorageValidationError("head event belongs to another branch")
            if generated_by_run_id is not None:
                generated_by_run_id = require_id(
                    generated_by_run_id, "generated_by_run_id"
                )
                run = self._require("runs", generated_by_run_id, "run")
                if (run["playthrough_id"], run["branch_id"]) != (
                    playthrough_id,
                    branch_id,
                ):
                    raise StorageValidationError("run belongs to another branch")
            normalized_state = json_copy(state)
            digest = canonical_sha256(normalized_state)
            if (
                state_sha256 is not None
                and require_sha256(state_sha256, "state_sha256") != digest
            ):
                raise StorageValidationError("state_sha256 does not match state")
            record = {
                "checkpoint_id": identifier,
                "playthrough_id": playthrough_id,
                "branch_id": branch_id,
                "head_event_id": head_event_id,
                "generated_by_run_id": generated_by_run_id,
                "label": None
                if label is None
                else require_nonempty(label, "label", maximum=200),
                "checkpoint_kind": checkpoint_kind,
                "state": normalized_state,
                "state_sha256": digest,
                "created_at_utc": normalize_utc(created_at_utc),
            }
            return self._insert_immutable("checkpoints", identifier, record)

    def get_checkpoint(self, playthrough_id: str, checkpoint_id: str) -> dict[str, Any]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            record = self._require(
                "checkpoints", require_id(checkpoint_id, "checkpoint_id"), "checkpoint"
            )
            if record["playthrough_id"] != playthrough_id:
                raise StorageNotFoundError("checkpoint not found")
            return detached(record)

    def list_checkpoints(
        self, playthrough_id: str, *, branch_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            if branch_id is not None:
                branch_id = require_id(branch_id, "branch_id")
            rows = [
                row
                for row in self._records["checkpoints"].values()
                if row["playthrough_id"] == playthrough_id
                and (branch_id is None or row["branch_id"] == branch_id)
            ]
            return [
                detached(item)
                for item in sorted(
                    rows, key=lambda row: (row["created_at_utc"], row["checkpoint_id"])
                )
            ]

    def set_head(
        self,
        *,
        playthrough_id: str,
        branch_id: str,
        checkpoint_id: str | None,
        head_event_id: str | None,
        updated_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            branch_id = require_id(branch_id, "branch_id")
            self._require("branches", (playthrough_id, branch_id), "branch")
            if checkpoint_id is not None:
                checkpoint_id = require_id(checkpoint_id, "checkpoint_id")
                checkpoint = self._require("checkpoints", checkpoint_id, "checkpoint")
                if (checkpoint["playthrough_id"], checkpoint["branch_id"]) != (
                    playthrough_id,
                    branch_id,
                ):
                    raise StorageValidationError("checkpoint belongs to another branch")
            if head_event_id is not None:
                head_event_id = require_id(head_event_id, "head_event_id")
                event = self._require("story_events", head_event_id, "head event")
                if (event["playthrough_id"], event["branch_id"]) != (
                    playthrough_id,
                    branch_id,
                ):
                    raise StorageValidationError("head event belongs to another branch")
            record = {
                "playthrough_id": playthrough_id,
                "branch_id": branch_id,
                "checkpoint_id": checkpoint_id,
                "head_event_id": head_event_id,
                "updated_at_utc": normalize_utc(updated_at_utc),
            }
            return self._set_mutable("heads", playthrough_id, record)

    def get_head(self, playthrough_id: str) -> dict[str, Any]:
        with self._lock:
            return detached(
                self._require(
                    "heads",
                    require_id(playthrough_id, "playthrough_id"),
                    "playthrough head",
                )
            )

    def save_slot(
        self,
        *,
        playthrough_id: str,
        slot_name: str,
        checkpoint_id: str,
        label: str | None = None,
        updated_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            slot_name = require_nonempty(slot_name, "slot_name", maximum=80)
            checkpoint_id = require_id(checkpoint_id, "checkpoint_id")
            checkpoint = self._require("checkpoints", checkpoint_id, "checkpoint")
            if checkpoint["playthrough_id"] != playthrough_id:
                raise StorageValidationError(
                    "checkpoint belongs to another playthrough"
                )
            record = {
                "playthrough_id": playthrough_id,
                "slot_name": slot_name,
                "checkpoint_id": checkpoint_id,
                "label": None
                if label is None
                else require_nonempty(label, "label", maximum=200),
                "updated_at_utc": normalize_utc(updated_at_utc),
            }
            return self._set_mutable("save_slots", (playthrough_id, slot_name), record)

    def get_save_slot(self, playthrough_id: str, slot_name: str) -> dict[str, Any]:
        with self._lock:
            key = (
                require_id(playthrough_id, "playthrough_id"),
                require_nonempty(slot_name, "slot_name", maximum=80),
            )
            return detached(self._require("save_slots", key, "save slot"))

    def list_save_slots(self, playthrough_id: str) -> list[dict[str, Any]]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            rows = [
                row
                for row in self._records["save_slots"].values()
                if row["playthrough_id"] == playthrough_id
            ]
            return [
                detached(item)
                for item in sorted(rows, key=lambda row: row["slot_name"])
            ]

    def select_feature(
        self,
        *,
        playthrough_id: str,
        branch_id: str,
        feature_key: str,
        option_key: str,
        selection_id: str | None = None,
        checkpoint_id: str | None = None,
        config: Mapping[str, Any] | None = None,
        selected_by: str = "user",
        effective: bool = True,
        created_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            branch_id = require_id(branch_id, "branch_id")
            self._require("branches", (playthrough_id, branch_id), "branch")
            identifier = require_id(selection_id or new_id("selection"), "selection_id")
            if selected_by not in {"default", "capability", "user", "system"}:
                raise StorageValidationError("invalid selected_by value")
            if checkpoint_id is not None:
                checkpoint_id = require_id(checkpoint_id, "checkpoint_id")
                checkpoint = self._require("checkpoints", checkpoint_id, "checkpoint")
                if (checkpoint["playthrough_id"], checkpoint["branch_id"]) != (
                    playthrough_id,
                    branch_id,
                ):
                    raise StorageValidationError("checkpoint belongs to another branch")
            record = {
                "selection_id": identifier,
                "playthrough_id": playthrough_id,
                "branch_id": branch_id,
                "checkpoint_id": checkpoint_id,
                "feature_key": require_nonempty(
                    feature_key, "feature_key", maximum=100
                ),
                "option_key": require_nonempty(option_key, "option_key", maximum=160),
                "config": json_copy(config or {}),
                "selected_by": selected_by,
                "effective": require_bool(effective, "effective"),
                "created_at_utc": normalize_utc(created_at_utc),
            }
            return self._insert_immutable("feature_selections", identifier, record)

    def list_feature_selections(
        self, playthrough_id: str, *, branch_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            if branch_id is not None:
                branch_id = require_id(branch_id, "branch_id")
            rows = [
                row
                for row in self._records["feature_selections"].values()
                if row["playthrough_id"] == playthrough_id
                and (branch_id is None or row["branch_id"] == branch_id)
            ]
            return [
                detached(item)
                for item in sorted(
                    rows, key=lambda row: (row["created_at_utc"], row["selection_id"])
                )
            ]

    def begin_run(
        self,
        *,
        playthrough_id: str,
        branch_id: str,
        mode: str,
        requested_storage: str,
        effective_storage: str,
        materials_manifest_sha256: str,
        config_sha256: str,
        run_id: str | None = None,
        status: str = "running",
        fallback_reason: str | None = None,
        code_version: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
        safe_error: str | None = None,
        created_at_utc: str | None = None,
        finished_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
            branch_id = require_id(branch_id, "branch_id")
            self._require("branches", (playthrough_id, branch_id), "branch")
            identifier = require_id(run_id or new_id("run"), "run_id")
            if mode not in {
                "offline",
                "mock",
                "local",
                "hybrid",
                "azure",
                "release-view",
            }:
                raise StorageValidationError("invalid run mode")
            if requested_storage not in {"auto", "sqlite", "file", "memory"}:
                raise StorageValidationError("invalid requested storage")
            if effective_storage not in {"sqlite", "file", "memory"}:
                raise StorageValidationError("invalid effective storage")
            if status not in {"running", "completed", "failed", "cancelled"}:
                raise StorageValidationError("invalid run status")
            record = {
                "run_id": identifier,
                "playthrough_id": playthrough_id,
                "branch_id": branch_id,
                "created_at_utc": normalize_utc(created_at_utc),
                "finished_at_utc": None
                if finished_at_utc is None
                else normalize_utc(finished_at_utc),
                "status": status,
                "mode": mode,
                "requested_storage": requested_storage,
                "effective_storage": effective_storage,
                "fallback_reason": fallback_reason,
                "materials_manifest_sha256": require_sha256(
                    materials_manifest_sha256, "materials_manifest_sha256"
                ),
                "config_sha256": require_sha256(config_sha256, "config_sha256"),
                "code_version": code_version,
                "capabilities": json_copy(capabilities or {}),
                "safe_error": safe_error,
            }
            return self._insert_immutable("runs", identifier, record)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            return detached(self._require("runs", require_id(run_id, "run_id"), "run"))

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        safe_error: str | None = None,
        finished_at_utc: str | None = None,
    ) -> dict[str, Any]:
        """Finish a running record without permitting historical rewrites.

        A run is the one deliberate exception to the immutable generation
        collections: only ``status``, ``safe_error``, and ``finished_at_utc``
        may transition once, from ``running`` to a terminal state. Repeating
        the exact terminal update is idempotent.
        """

        with self._lock:
            identifier = require_id(run_id, "run_id")
            if status not in {"completed", "failed", "cancelled"}:
                raise StorageValidationError("run terminal status is invalid")
            if safe_error is not None:
                safe_error = require_nonempty(
                    safe_error, "safe_error", maximum=2000
                )
            current = self._require("runs", identifier, "run")
            finished = normalize_utc(finished_at_utc)
            if current["status"] != "running":
                comparable = {
                    "status": status,
                    "safe_error": safe_error,
                    "finished_at_utc": finished,
                }
                existing = {key: current[key] for key in comparable}
                if existing == comparable:
                    return detached(current)
                raise StorageConflictError("terminal run record cannot be rewritten")
            updated = {
                **current,
                "status": status,
                "safe_error": safe_error,
                "finished_at_utc": finished,
            }
            self._persist_mutable("runs", identifier, updated)
            self._records["runs"][identifier] = detached(updated)
            return detached(updated)

    def list_runs(
        self, playthrough_id: str | None = None, *, branch_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            if playthrough_id is not None:
                playthrough_id = require_id(playthrough_id, "playthrough_id")
            if branch_id is not None:
                branch_id = require_id(branch_id, "branch_id")
                if playthrough_id is None:
                    raise StorageValidationError(
                        "branch filter requires playthrough_id"
                    )
            rows = [
                row
                for row in self._records["runs"].values()
                if (playthrough_id is None or row["playthrough_id"] == playthrough_id)
                and (branch_id is None or row["branch_id"] == branch_id)
            ]
            return [
                detached(item)
                for item in sorted(
                    rows, key=lambda row: (row["created_at_utc"], row["run_id"])
                )
            ]

    def record_provider_call(
        self,
        *,
        run_id: str,
        purpose: str,
        provider: str,
        mode: str,
        request_sha256: str,
        call_id: str | None = None,
        trigger_id: str | None = None,
        story_event_id: str | None = None,
        model: str | None = None,
        endpoint_alias: str | None = None,
        prompt_sha256: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        started_at_utc: str | None = None,
        finished_at_utc: str | None = None,
        latency_ms: int | None = None,
        usage: Mapping[str, Any] | None = None,
        cost_amount_micros: int | None = None,
        cost_currency: str | None = None,
        billing_state: str = "unknown",
        status: str = "unknown",
        error_code: str | None = None,
        safe_error: str | None = None,
        retry_of_call_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            run_id = require_id(run_id, "run_id")
            self._require("runs", run_id, "run")
            identifier = require_id(call_id or new_id("call"), "call_id")
            if mode not in {"mock", "local", "azure"}:
                raise StorageValidationError("invalid provider call mode")
            if billing_state not in {"actual", "estimated", "zero", "unknown"}:
                raise StorageValidationError("invalid billing_state")
            if status not in {"pending", "success", "failed", "cancelled", "unknown"}:
                raise StorageValidationError("invalid provider call status")
            if endpoint_alias is not None:
                endpoint_alias = require_nonempty(
                    endpoint_alias, "endpoint_alias", maximum=160
                )
                if "://" in endpoint_alias:
                    raise StorageValidationError(
                        "endpoint_alias must not contain a full URL"
                    )
            if prompt_sha256 is not None:
                prompt_sha256 = require_sha256(prompt_sha256, "prompt_sha256")
            if latency_ms is not None and (
                not isinstance(latency_ms, int) or latency_ms < 0
            ):
                raise StorageValidationError("latency_ms must be non-negative")
            if cost_amount_micros is not None and (
                not isinstance(cost_amount_micros, int) or cost_amount_micros < 0
            ):
                raise StorageValidationError("cost_amount_micros must be non-negative")
            if trigger_id is not None:
                trigger_id = require_id(trigger_id, "trigger_id")
            if story_event_id is not None:
                story_event_id = require_id(story_event_id, "story_event_id")
                event = self._require("story_events", story_event_id, "story event")
                run = self._require("runs", run_id, "run")
                if (event["playthrough_id"], event["branch_id"]) != (
                    run["playthrough_id"],
                    run["branch_id"],
                ):
                    raise StorageValidationError(
                        "story event belongs to another run branch"
                    )
            if retry_of_call_id is not None:
                retry_of_call_id = require_id(retry_of_call_id, "retry_of_call_id")
                previous = self._require(
                    "provider_calls", retry_of_call_id, "retry call"
                )
                if previous["run_id"] != run_id:
                    raise StorageValidationError("retry call belongs to another run")
            record = {
                "call_id": identifier,
                "run_id": run_id,
                "trigger_id": trigger_id,
                "story_event_id": story_event_id,
                "purpose": require_nonempty(purpose, "purpose", maximum=160),
                "provider": require_nonempty(provider, "provider", maximum=100),
                "model": model,
                "mode": mode,
                "endpoint_alias": endpoint_alias,
                "request_sha256": require_sha256(request_sha256, "request_sha256"),
                "prompt_sha256": prompt_sha256,
                "parameters": json_copy(parameters or {}),
                "started_at_utc": normalize_utc(started_at_utc),
                "finished_at_utc": None
                if finished_at_utc is None
                else normalize_utc(finished_at_utc),
                "latency_ms": latency_ms,
                "usage": json_copy(usage or {}),
                "cost_amount_micros": cost_amount_micros,
                "cost_currency": cost_currency,
                "billing_state": billing_state,
                "status": status,
                "error_code": error_code,
                "safe_error": safe_error,
                "retry_of_call_id": retry_of_call_id,
            }
            return self._insert_immutable("provider_calls", identifier, record)

    def register_asset(
        self,
        *,
        run_id: str,
        role: str,
        media_type: str,
        relative_path: str,
        size_bytes: int,
        sha256: str,
        provenance: str,
        final_eligible: bool,
        asset_id: str | None = None,
        trigger_id: str | None = None,
        provider_call_id: str | None = None,
        mime_type: str | None = None,
        width_px: int | None = None,
        height_px: int | None = None,
        duration_ms: int | None = None,
        created_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            run_id = require_id(run_id, "run_id")
            self._require("runs", run_id, "run")
            identifier = require_id(asset_id or new_id("asset"), "asset_id")
            if role not in {
                "reference",
                "candidate",
                "final",
                "rejected",
                "placeholder",
                "draft",
            }:
                raise StorageValidationError("invalid asset role")
            if media_type not in {
                "image",
                "audio",
                "video",
                "json",
                "document",
                "other",
            }:
                raise StorageValidationError("invalid media_type")
            if provenance not in {
                "deterministic",
                "fixture",
                "local",
                "azure",
                "manual",
            }:
                raise StorageValidationError("invalid provenance")
            final_eligible = require_bool(final_eligible, "final_eligible")
            if final_eligible and (
                role in {"placeholder", "draft", "rejected"}
                or provenance in {"deterministic", "fixture"}
            ):
                raise StorageValidationError(
                    "placeholder, draft, rejected, deterministic, and fixture assets cannot be final eligible"
                )
            if not isinstance(size_bytes, int) or size_bytes < 0:
                raise StorageValidationError("size_bytes must be non-negative")
            for field, value in (("width_px", width_px), ("height_px", height_px)):
                if value is not None and (not isinstance(value, int) or value <= 0):
                    raise StorageValidationError(f"{field} must be positive")
            if duration_ms is not None and (
                not isinstance(duration_ms, int) or duration_ms < 0
            ):
                raise StorageValidationError("duration_ms must be non-negative")
            if provider_call_id is not None:
                provider_call_id = require_id(provider_call_id, "provider_call_id")
                call = self._require(
                    "provider_calls", provider_call_id, "provider call"
                )
                if call["run_id"] != run_id:
                    raise StorageValidationError("provider call belongs to another run")
            if trigger_id is not None:
                trigger_id = require_id(trigger_id, "trigger_id")
            relative_path = require_relative_path(relative_path)
            if any(
                row["run_id"] == run_id and row["relative_path"] == relative_path
                for row in self._records["assets"].values()
            ):
                existing = next(
                    row
                    for row in self._records["assets"].values()
                    if row["run_id"] == run_id and row["relative_path"] == relative_path
                )
                if existing["asset_id"] != identifier:
                    raise StorageConflictError("asset path already exists in this run")
            record = {
                "asset_id": identifier,
                "run_id": run_id,
                "trigger_id": trigger_id,
                "provider_call_id": provider_call_id,
                "role": role,
                "media_type": media_type,
                "relative_path": relative_path,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "width_px": width_px,
                "height_px": height_px,
                "duration_ms": duration_ms,
                "sha256": require_sha256(sha256, "sha256"),
                "provenance": provenance,
                "final_eligible": final_eligible,
                "created_at_utc": normalize_utc(created_at_utc),
            }
            return self._insert_immutable("assets", identifier, record)

    def record_decision(
        self,
        *,
        run_id: str,
        subject_kind: str,
        subject_id: str,
        action: str,
        reason: str,
        decided_by: str,
        decision_id: str | None = None,
        trigger_id: str | None = None,
        supersedes_decision_id: str | None = None,
        created_at_utc: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            run_id = require_id(run_id, "run_id")
            self._require("runs", run_id, "run")
            identifier = require_id(decision_id or new_id("decision"), "decision_id")
            if subject_kind not in {
                "spec",
                "asset",
                "conflict",
                "run",
                "checkpoint",
                "branch",
                "feature",
                "release",
            }:
                raise StorageValidationError("invalid decision subject_kind")
            if action not in {"accept", "reject", "needs_review", "supersede", "block"}:
                raise StorageValidationError("invalid decision action")
            if decided_by not in {"human", "rule", "model_assist"}:
                raise StorageValidationError("invalid decided_by value")
            if trigger_id is not None:
                trigger_id = require_id(trigger_id, "trigger_id")
            if supersedes_decision_id is not None:
                supersedes_decision_id = require_id(
                    supersedes_decision_id, "supersedes_decision_id"
                )
                previous = self._require(
                    "decisions", supersedes_decision_id, "decision"
                )
                if previous["run_id"] != run_id:
                    raise StorageValidationError(
                        "superseded decision belongs to another run"
                    )
            record = {
                "decision_id": identifier,
                "run_id": run_id,
                "trigger_id": trigger_id,
                "subject_kind": subject_kind,
                "subject_id": require_id(subject_id, "subject_id"),
                "action": action,
                "reason": require_nonempty(reason, "reason", maximum=2000),
                "decided_by": decided_by,
                "supersedes_decision_id": supersedes_decision_id,
                "created_at_utc": normalize_utc(created_at_utc),
            }
            return self._insert_immutable("decisions", identifier, record)

    def _filter_run_children(
        self, collection: str, playthrough_id: str | None, branch_id: str | None
    ) -> list[dict[str, Any]]:
        if playthrough_id is not None:
            playthrough_id = require_id(playthrough_id, "playthrough_id")
        if branch_id is not None:
            branch_id = require_id(branch_id, "branch_id")
            if playthrough_id is None:
                raise StorageValidationError("branch filter requires playthrough_id")
        rows = []
        for row in self._records[collection].values():
            run = self._require("runs", row["run_id"], "run")
            if playthrough_id is not None and run["playthrough_id"] != playthrough_id:
                continue
            if branch_id is not None and run["branch_id"] != branch_id:
                continue
            rows.append(row)
        identity = {
            "provider_calls": "call_id",
            "assets": "asset_id",
            "decisions": "decision_id",
        }[collection]
        return [
            detached(item)
            for item in sorted(
                rows,
                key=lambda row: (
                    row["created_at_utc"]
                    if "created_at_utc" in row
                    else row["started_at_utc"],
                    row[identity],
                ),
            )
        ]

    def list_provider_calls(
        self, playthrough_id: str | None = None, *, branch_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            return self._filter_run_children(
                "provider_calls", playthrough_id, branch_id
            )

    def list_assets(
        self, playthrough_id: str | None = None, *, branch_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            return self._filter_run_children("assets", playthrough_id, branch_id)

    def list_decisions(
        self, playthrough_id: str | None = None, *, branch_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            return self._filter_run_children("decisions", playthrough_id, branch_id)

    def export_snapshot(self) -> dict[str, Any]:
        with self._lock:
            collections: dict[str, list[dict[str, Any]]] = {}
            sort_fields = {
                "playthroughs": ("playthrough_id",),
                "branches": ("playthrough_id", "branch_id"),
                "story_events": ("story_event_id",),
                "checkpoints": ("checkpoint_id",),
                "heads": ("playthrough_id",),
                "save_slots": ("playthrough_id", "slot_name"),
                "feature_selections": ("selection_id",),
                "runs": ("run_id",),
                "provider_calls": ("call_id",),
                "assets": ("asset_id",),
                "decisions": ("decision_id",),
            }
            for name in ALL_COLLECTIONS:
                fields = sort_fields[name]
                collections[name] = [
                    detached(row)
                    for row in sorted(
                        self._records[name].values(),
                        key=lambda row: self._snapshot_sort_key(row, fields),
                    )
                ]
            return {"format_version": 1, "collections": collections}

    @staticmethod
    def _snapshot_sort_key(
        row: Mapping[str, Any], fields: tuple[str, ...]
    ) -> tuple[Any, ...]:
        return tuple(row[field] for field in fields)

    @staticmethod
    def _import_dependency_ordered(
        rows: list[dict[str, Any]],
        *,
        identity_field: str,
        dependency_field: str,
        importer: Callable[..., dict[str, Any]],
        label: str,
    ) -> None:
        """Import an append-only supersession/retry chain topologically."""

        pending = list(rows)
        imported: set[str] = set()
        while pending:
            progressed = False
            for row in list(pending):
                dependency = row.get(dependency_field)
                if dependency is not None and dependency not in imported:
                    continue
                importer(**row)
                imported.add(str(row[identity_field]))
                pending.remove(row)
                progressed = True
            if not progressed:
                unresolved = [str(row.get(identity_field)) for row in pending]
                raise StorageValidationError(
                    f"snapshot {label} graph has missing parents or a cycle: {unresolved}"
                )

    def import_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        with self._lock:
            if snapshot.get("format_version") != 1:
                raise StorageValidationError("unsupported snapshot format_version")
            collections = snapshot.get("collections")
            if not isinstance(collections, Mapping):
                raise StorageValidationError("snapshot collections must be an object")
            unknown = set(collections) - set(ALL_COLLECTIONS)
            if unknown:
                raise StorageValidationError(
                    f"unknown snapshot collections: {sorted(unknown)}"
                )
            normalized: dict[str, list[dict[str, Any]]] = {}
            for name in ALL_COLLECTIONS:
                rows = collections.get(name, [])
                if not isinstance(rows, list):
                    raise StorageValidationError(
                        f"snapshot collection {name} must be an array"
                    )
                normalized[name] = []
                for row in rows:
                    if not isinstance(row, Mapping):
                        raise StorageValidationError(
                            f"snapshot {name} record must be an object"
                        )
                    normalized[name].append(dict(row))

            for row in normalized["playthroughs"]:
                self.create_playthrough(**row)

            # Branches and checkpoints form a deliberate graph: a child branch
            # points at a checkpoint on its already-materialized parent. Import
            # each branch together with its branch-local history, then unlock
            # its children. This works for arbitrarily deep rewind trees.
            pending = list(normalized["branches"])
            imported_branches: set[tuple[str, str]] = set()
            while pending:
                progressed = False
                for branch in list(pending):
                    parent = branch.get("parent_branch_id")
                    parent_key = (branch["playthrough_id"], parent) if parent else None
                    fork = branch.get("forked_from_checkpoint_id")
                    if parent_key is not None and parent_key not in imported_branches:
                        continue
                    if fork is not None and fork not in self._records["checkpoints"]:
                        continue
                    self.create_branch(**branch)
                    branch_key = (branch["playthrough_id"], branch["branch_id"])
                    imported_branches.add(branch_key)
                    for row in normalized["runs"]:
                        if (row["playthrough_id"], row["branch_id"]) == branch_key:
                            self.begin_run(**row)
                    branch_events = [
                        row
                        for row in normalized["story_events"]
                        if (row["playthrough_id"], row["branch_id"]) == branch_key
                    ]
                    for row in sorted(
                        branch_events, key=lambda item: item["sequence_no"]
                    ):
                        self.append_story_event(**row)
                    for row in normalized["checkpoints"]:
                        if (row["playthrough_id"], row["branch_id"]) == branch_key:
                            self.create_checkpoint(**row)
                    pending.remove(branch)
                    progressed = True
                if not progressed:
                    unresolved = [row.get("branch_id") for row in pending]
                    raise StorageValidationError(
                        f"snapshot branch graph has missing parents/checkpoints: {unresolved}"
                    )

            # A snapshot with playthroughs but no branches is valid. Any other
            # branch-owned record in that state is not.
            imported_run_ids = {row["run_id"] for row in self._records["runs"].values()}
            expected_run_ids = {row["run_id"] for row in normalized["runs"]}
            if imported_run_ids != expected_run_ids:
                raise StorageValidationError(
                    "snapshot contains a run for an unknown branch"
                )

            for row in normalized["feature_selections"]:
                self.select_feature(**row)
            self._import_dependency_ordered(
                normalized["provider_calls"],
                identity_field="call_id",
                dependency_field="retry_of_call_id",
                importer=self.record_provider_call,
                label="provider call",
            )
            for row in normalized["assets"]:
                self.register_asset(**row)
            self._import_dependency_ordered(
                normalized["decisions"],
                identity_field="decision_id",
                dependency_field="supersedes_decision_id",
                importer=self.record_decision,
                label="decision",
            )
            for row in normalized["heads"]:
                self.set_head(**row)
            for row in normalized["save_slots"]:
                self.save_slot(**row)

    def integrity_check(self) -> dict[str, Any]:
        with self._lock:
            errors: list[str] = []
            try:
                # Export validates that all values remain JSON-serializable.
                snapshot = self.export_snapshot()
                canonical_sha256(snapshot)
                for checkpoint in self._records["checkpoints"].values():
                    if (
                        canonical_sha256(checkpoint["state"])
                        != checkpoint["state_sha256"]
                    ):
                        errors.append(
                            f"checkpoint digest mismatch: {checkpoint['checkpoint_id']}"
                        )
            except (
                StorageError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:  # pragma: no cover
                errors.append(str(exc))
            return {
                "ok": not errors,
                "backend": self.backend_name,
                "errors": errors,
                "counts": {name: len(rows) for name, rows in self._records.items()},
            }


# Imported late to avoid a circular import in the normal fast path.
from .common import StorageCorruptError
