"""Human review service binding decisions to verified immutable assets."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .assets import verify_local_asset
from .checkpoints import normalize_checkpoint_state
from .playthrough import PlaythroughManager
from .storage.common import StorageValidationError
from .storage.protocol import StorageBackend


class ReviewService:
    def __init__(
        self,
        *,
        storage: StorageBackend,
        manager: PlaythroughManager,
        asset_root: str | Path,
    ) -> None:
        self.storage = storage
        self.manager = manager
        self.asset_root = Path(asset_root).expanduser().resolve()

    def _asset(self, playthrough_id: str, asset_id: str) -> dict[str, Any]:
        for asset in self.storage.list_assets(playthrough_id):
            if asset.get("asset_id") == asset_id:
                return asset
        raise KeyError(asset_id)

    def _current_state(self, playthrough_id: str) -> dict[str, Any]:
        head = self.storage.get_head(playthrough_id)
        checkpoint_id = head.get("checkpoint_id")
        if not isinstance(checkpoint_id, str):
            raise StorageValidationError("playthrough has no checkpoint")
        checkpoint = self.storage.get_checkpoint(playthrough_id, checkpoint_id)
        state = checkpoint.get("state")
        if not isinstance(state, Mapping):
            raise StorageValidationError("checkpoint state is unavailable")
        return normalize_checkpoint_state(state)

    def _latest_decision(
        self, playthrough_id: str, asset_id: str
    ) -> dict[str, Any] | None:
        matching = [
            item
            for item in self.storage.list_decisions(playthrough_id)
            if item.get("subject_kind") == "asset"
            and item.get("subject_id") == asset_id
        ]
        return matching[-1] if matching else None

    def review(
        self,
        playthrough_id: str,
        *,
        asset_id: str,
        action: str,
        reason: str,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"accept", "reject"}:
            raise StorageValidationError("review action must be accept or reject")
        if not isinstance(reason, str) or not reason.strip():
            raise StorageValidationError("review reason is required")
        asset = self._asset(playthrough_id, asset_id)
        verification = verify_local_asset(self.asset_root, asset)
        if not verification.ok:
            raise StorageValidationError(
                f"asset verification failed: {verification.status}"
            )
        if action == "accept":
            if expected_sha256 != asset.get("sha256"):
                raise StorageValidationError("asset hash changed; refresh review state")
            if asset.get("media_type") not in {"image", "audio", "video"}:
                raise StorageValidationError("only media assets can be accepted")
            if asset.get("role") in {"placeholder", "draft", "rejected"}:
                raise StorageValidationError("this asset role cannot be accepted")
            if asset.get("final_eligible") is not True:
                raise StorageValidationError("asset is not technically final eligible")

        previous = self._latest_decision(playthrough_id, asset_id)
        decision = self.storage.record_decision(
            run_id=str(asset["run_id"]),
            trigger_id=(
                str(asset["trigger_id"])
                if asset.get("trigger_id") is not None
                else None
            ),
            subject_kind="asset",
            subject_id=asset_id,
            action=action,
            reason=reason.strip(),
            decided_by="human",
            supersedes_decision_id=(
                str(previous["decision_id"]) if previous is not None else None
            ),
        )
        state = self._current_state(playthrough_id)
        accepted = list(state["accepted_assets"])
        if action == "accept" and asset_id not in accepted:
            accepted.append(asset_id)
        if action == "reject" and asset_id in accepted:
            accepted.remove(asset_id)
        state["accepted_assets"] = accepted
        state["extra"] = {
            **deepcopy(state["extra"]),
            "last_review_decision_id": decision["decision_id"],
        }
        update = self.manager.append_event(
            playthrough_id,
            event_type=f"asset_{action}ed",
            actor="user",
            payload={
                "asset_id": asset_id,
                "decision_id": decision["decision_id"],
                "sha256": asset["sha256"],
            },
            run_id=str(asset["run_id"]),
            trigger_id=(
                str(asset["trigger_id"])
                if asset.get("trigger_id") is not None
                else None
            ),
            checkpoint_kind="manual",
            checkpoint_label=f"Human {action}: {asset_id}",
            checkpoint_state=state,
        )
        return {
            "asset": deepcopy(asset),
            "verification": verification.to_public_dict(),
            "decision": decision,
            "checkpoint": update["checkpoint"],
            "head": update["head"],
        }
