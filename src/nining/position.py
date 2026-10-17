"""Checkpoint-backed story position and view-mode changes."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .checkpoints import normalize_checkpoint_state
from .domain import Materials
from .playthrough import PlaythroughManager
from .storage.common import StorageConflictError, StorageValidationError
from .storage.protocol import StorageBackend


class StoryPositionService:
    def __init__(
        self,
        *,
        materials: Materials,
        storage: StorageBackend,
        manager: PlaythroughManager,
    ) -> None:
        self.materials = materials
        self.storage = storage
        self.manager = manager

    def current(self, playthrough_id: str) -> dict[str, Any]:
        head = self.storage.get_head(playthrough_id)
        checkpoint_id = head.get("checkpoint_id")
        state: dict[str, Any] = normalize_checkpoint_state({})
        if isinstance(checkpoint_id, str):
            record = self.storage.get_checkpoint(playthrough_id, checkpoint_id)
            raw_state = record.get("state")
            if isinstance(raw_state, Mapping):
                state = normalize_checkpoint_state(raw_state)
        trigger_id = state["cursor"].get("trigger_id")
        if trigger_id is None and self.materials.triggers:
            trigger_id = self.materials.triggers[0].trigger_id
        return {
            "trigger_id": trigger_id,
            "view_mode": state["view_mode"],
            "checkpoint_id": checkpoint_id,
            "branch_id": head["branch_id"],
            "state": state,
        }

    def move(
        self,
        playthrough_id: str,
        *,
        trigger_id: str,
        view_mode: str | None = None,
        expected_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        ids = [item.trigger_id for item in self.materials.triggers]
        if trigger_id not in ids:
            raise KeyError(trigger_id)
        current = self.current(playthrough_id)
        if (
            expected_checkpoint_id is not None
            and current["checkpoint_id"] != expected_checkpoint_id
        ):
            raise StorageConflictError("playthrough changed; refresh before moving")
        selected_mode = view_mode or str(current["view_mode"])
        if selected_mode not in {"story", "god"}:
            raise StorageValidationError("view mode must be story or god")
        current_trigger = current.get("trigger_id")
        current_index = ids.index(str(current_trigger)) if current_trigger in ids else 0
        target_index = ids.index(trigger_id)
        if selected_mode == "story" and target_index > current_index + 1:
            raise StorageValidationError("story position may advance only one trigger")
        state = deepcopy(current["state"])
        state["cursor"] = {**state["cursor"], "trigger_id": trigger_id}
        state["view_mode"] = selected_mode
        result = self.manager.append_event(
            playthrough_id,
            event_type="story_position_changed",
            actor="user",
            payload={"trigger_id": trigger_id, "view_mode": selected_mode},
            checkpoint_kind="trigger",
            checkpoint_label=f"Position {trigger_id} ({selected_mode})",
            checkpoint_state=state,
        )
        return {
            "position": self.current(playthrough_id),
            "event": result["event"],
            "checkpoint": result["checkpoint"],
            "head": result["head"],
        }
