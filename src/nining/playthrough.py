"""Game-style save, rewind, branch, checkout, and feature orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .checkpoints import CheckpointState, normalize_checkpoint_state
from .storage.common import StorageValidationError, new_id
from .storage.protocol import StorageBackend


@dataclass(frozen=True)
class RewindResult:
    playthrough_id: str
    source_checkpoint_id: str
    branch_id: str
    checkpoint_id: str
    inspect_only: bool
    created_branch_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "playthrough_id": self.playthrough_id,
            "source_checkpoint_id": self.source_checkpoint_id,
            "branch_id": self.branch_id,
            "checkpoint_id": self.checkpoint_id,
            "inspect_only": self.inspect_only,
            "created_branch_id": self.created_branch_id,
        }


class PlaythroughManager:
    """Backend-neutral coordinator for user-visible game-state operations."""

    def __init__(self, storage: StorageBackend) -> None:
        self.storage = storage

    def start(
        self,
        *,
        title: str,
        materials_manifest_sha256: str,
        initial_state: Mapping[str, Any] | CheckpointState | None = None,
        metadata: Mapping[str, Any] | None = None,
        default_view_mode: str = "story",
        playthrough_id: str | None = None,
        branch_id: str | None = None,
        checkpoint_id: str | None = None,
        event_id: str | None = None,
        branch_name: str = "main",
    ) -> dict[str, dict[str, Any]]:
        playthrough = self.storage.create_playthrough(
            playthrough_id=playthrough_id,
            title=title,
            materials_manifest_sha256=materials_manifest_sha256,
            default_view_mode=default_view_mode,
            metadata=metadata,
        )
        branch = self.storage.create_branch(
            playthrough_id=playthrough["playthrough_id"],
            branch_id=branch_id,
            name=branch_name,
        )
        event = self.storage.append_story_event(
            playthrough_id=playthrough["playthrough_id"],
            branch_id=branch["branch_id"],
            story_event_id=event_id,
            event_type="playthrough_started",
            actor="system",
            payload={"title": title},
            reversible=False,
        )
        state = normalize_checkpoint_state(
            initial_state
            or CheckpointState(
                view_mode=default_view_mode,
                cursor={"story_event_id": event["story_event_id"]},
            )
        )
        checkpoint = self.storage.create_checkpoint(
            playthrough_id=playthrough["playthrough_id"],
            branch_id=branch["branch_id"],
            checkpoint_id=checkpoint_id,
            head_event_id=event["story_event_id"],
            checkpoint_kind="manual",
            label="Start",
            state=state,
        )
        head = self.storage.set_head(
            playthrough_id=playthrough["playthrough_id"],
            branch_id=branch["branch_id"],
            checkpoint_id=checkpoint["checkpoint_id"],
            head_event_id=event["story_event_id"],
        )
        return {
            "playthrough": playthrough,
            "branch": branch,
            "event": event,
            "checkpoint": checkpoint,
            "head": head,
        }

    def append_event(
        self,
        playthrough_id: str,
        *,
        event_type: str,
        actor: str,
        payload: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        trigger_id: str | None = None,
        reversible: bool = True,
        checkpoint_kind: str | None = None,
        checkpoint_state: Mapping[str, Any] | CheckpointState | None = None,
        checkpoint_label: str | None = None,
    ) -> dict[str, Any]:
        head = self.storage.get_head(playthrough_id)
        event = self.storage.append_story_event(
            playthrough_id=playthrough_id,
            branch_id=head["branch_id"],
            event_type=event_type,
            actor=actor,
            payload=payload,
            run_id=run_id,
            trigger_id=trigger_id,
            reversible=reversible,
            parent_event_id=head["head_event_id"],
        )
        checkpoint = None
        if checkpoint_kind is not None:
            if checkpoint_state is None:
                if head["checkpoint_id"] is None:
                    raise StorageValidationError(
                        "checkpoint_state is required without an earlier checkpoint"
                    )
                checkpoint_state = self.storage.get_checkpoint(
                    playthrough_id, head["checkpoint_id"]
                )["state"]
            checkpoint = self.storage.create_checkpoint(
                playthrough_id=playthrough_id,
                branch_id=head["branch_id"],
                head_event_id=event["story_event_id"],
                generated_by_run_id=run_id,
                checkpoint_kind=checkpoint_kind,
                label=checkpoint_label,
                state=normalize_checkpoint_state(checkpoint_state),
            )
        new_head = self.storage.set_head(
            playthrough_id=playthrough_id,
            branch_id=head["branch_id"],
            checkpoint_id=(
                checkpoint["checkpoint_id"]
                if checkpoint is not None
                else head["checkpoint_id"]
            ),
            head_event_id=event["story_event_id"],
        )
        return {"event": event, "checkpoint": checkpoint, "head": new_head}

    def checkpoint(
        self,
        playthrough_id: str,
        *,
        state: Mapping[str, Any] | CheckpointState,
        checkpoint_kind: str = "manual",
        label: str | None = None,
        generated_by_run_id: str | None = None,
    ) -> dict[str, Any]:
        head = self.storage.get_head(playthrough_id)
        checkpoint = self.storage.create_checkpoint(
            playthrough_id=playthrough_id,
            branch_id=head["branch_id"],
            head_event_id=head["head_event_id"],
            generated_by_run_id=generated_by_run_id,
            checkpoint_kind=checkpoint_kind,
            label=label,
            state=normalize_checkpoint_state(state),
        )
        self.storage.set_head(
            playthrough_id=playthrough_id,
            branch_id=head["branch_id"],
            checkpoint_id=checkpoint["checkpoint_id"],
            head_event_id=head["head_event_id"],
        )
        return checkpoint

    def save(
        self,
        playthrough_id: str,
        *,
        slot_name: str,
        label: str | None = None,
        state: Mapping[str, Any] | CheckpointState | None = None,
    ) -> dict[str, Any]:
        head = self.storage.get_head(playthrough_id)
        checkpoint_id = head["checkpoint_id"]
        if state is not None:
            checkpoint = self.checkpoint(
                playthrough_id,
                state=state,
                checkpoint_kind="manual",
                label=label or slot_name,
            )
            checkpoint_id = checkpoint["checkpoint_id"]
        if checkpoint_id is None:
            raise StorageValidationError("cannot save a slot without a checkpoint")
        return self.storage.save_slot(
            playthrough_id=playthrough_id,
            slot_name=slot_name,
            checkpoint_id=checkpoint_id,
            label=label,
        )

    def _resolve_checkpoint(
        self,
        playthrough_id: str,
        *,
        checkpoint_id: str | None,
        slot_name: str | None,
    ) -> dict[str, Any]:
        if (checkpoint_id is None) == (slot_name is None):
            raise StorageValidationError(
                "provide exactly one of checkpoint_id or slot_name"
            )
        if slot_name is not None:
            checkpoint_id = self.storage.get_save_slot(playthrough_id, slot_name)[
                "checkpoint_id"
            ]
        assert checkpoint_id is not None
        return self.storage.get_checkpoint(playthrough_id, checkpoint_id)

    def rewind(
        self,
        playthrough_id: str,
        *,
        checkpoint_id: str | None = None,
        slot_name: str | None = None,
        inspect_only: bool = True,
        branch_name: str | None = None,
    ) -> RewindResult:
        target = self._resolve_checkpoint(
            playthrough_id, checkpoint_id=checkpoint_id, slot_name=slot_name
        )
        if inspect_only:
            self.storage.set_head(
                playthrough_id=playthrough_id,
                branch_id=target["branch_id"],
                checkpoint_id=target["checkpoint_id"],
                head_event_id=target["head_event_id"],
            )
            return RewindResult(
                playthrough_id,
                target["checkpoint_id"],
                target["branch_id"],
                target["checkpoint_id"],
                True,
            )

        if branch_name is None:
            base_name = f"rewind-{target['checkpoint_id'][-8:]}"
            existing = {
                branch["name"] for branch in self.storage.list_branches(playthrough_id)
            }
            branch_name = base_name
            suffix = 2
            while branch_name in existing:
                branch_name = f"{base_name}-{suffix}"
                suffix += 1
        branch = self.storage.create_branch(
            playthrough_id=playthrough_id,
            name=branch_name,
            parent_branch_id=target["branch_id"],
            forked_from_checkpoint_id=target["checkpoint_id"],
        )
        fork_event = self.storage.append_story_event(
            playthrough_id=playthrough_id,
            branch_id=branch["branch_id"],
            event_type="branch_forked",
            actor="system",
            payload={
                "source_branch_id": target["branch_id"],
                "source_checkpoint_id": target["checkpoint_id"],
            },
            reversible=False,
            parent_event_id=target["head_event_id"],
        )
        fork_checkpoint = self.storage.create_checkpoint(
            playthrough_id=playthrough_id,
            branch_id=branch["branch_id"],
            head_event_id=fork_event["story_event_id"],
            checkpoint_kind="manual",
            label=f"Fork from {target['checkpoint_id']}",
            state=target["state"],
        )
        self.storage.set_head(
            playthrough_id=playthrough_id,
            branch_id=branch["branch_id"],
            checkpoint_id=fork_checkpoint["checkpoint_id"],
            head_event_id=fork_event["story_event_id"],
        )
        return RewindResult(
            playthrough_id,
            target["checkpoint_id"],
            branch["branch_id"],
            fork_checkpoint["checkpoint_id"],
            False,
            branch["branch_id"],
        )

    def checkout(
        self,
        playthrough_id: str,
        *,
        branch_id: str,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        self.storage.get_branch(playthrough_id, branch_id)
        if checkpoint_id is None:
            checkpoints = self.storage.list_checkpoints(
                playthrough_id, branch_id=branch_id
            )
            if not checkpoints:
                raise StorageValidationError("branch has no checkpoint to check out")
            checkpoint = checkpoints[-1]
        else:
            checkpoint = self.storage.get_checkpoint(playthrough_id, checkpoint_id)
            if checkpoint["branch_id"] != branch_id:
                raise StorageValidationError("checkpoint is not on requested branch")
        return self.storage.set_head(
            playthrough_id=playthrough_id,
            branch_id=branch_id,
            checkpoint_id=checkpoint["checkpoint_id"],
            head_event_id=checkpoint["head_event_id"],
        )

    def select_feature(
        self,
        playthrough_id: str,
        *,
        feature_key: str,
        option_key: str,
        config: Mapping[str, Any] | None = None,
        selected_by: str = "user",
        effective: bool = True,
    ) -> dict[str, Any]:
        head = self.storage.get_head(playthrough_id)
        selection_id = new_id("selection")
        selection = self.storage.select_feature(
            playthrough_id=playthrough_id,
            branch_id=head["branch_id"],
            checkpoint_id=head["checkpoint_id"],
            selection_id=selection_id,
            feature_key=feature_key,
            option_key=option_key,
            config=config,
            selected_by=selected_by,
            effective=effective,
        )
        event = self.storage.append_story_event(
            playthrough_id=playthrough_id,
            branch_id=head["branch_id"],
            event_type="feature_selected",
            actor="user" if selected_by == "user" else "system",
            payload={
                "selection_id": selection_id,
                "feature_key": feature_key,
                "option_key": option_key,
                "effective": bool(effective),
            },
            parent_event_id=head["head_event_id"],
        )
        new_head = self.storage.set_head(
            playthrough_id=playthrough_id,
            branch_id=head["branch_id"],
            checkpoint_id=head["checkpoint_id"],
            head_event_id=event["story_event_id"],
        )
        return {"selection": selection, "event": event, "head": new_head}

    def history(
        self, playthrough_id: str, *, branch_id: str | None = None
    ) -> list[dict[str, Any]]:
        if branch_id is None:
            branch_id = self.storage.get_head(playthrough_id)["branch_id"]
        return self.storage.list_story_events(
            playthrough_id, branch_id, include_ancestry=True
        )
