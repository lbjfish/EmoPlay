"""Read-only presentation data and explicit control adapters for the local web UI.

The web package deliberately does not own story facts.  ``RepositoryWebFacade``
adapts the material, capability, and playthrough services that already exist in
the repository.  A different application can supply the small
``WebDataProvider`` protocol instead, which keeps HTTP and browser code free of
storage and provider assumptions.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..assets import verify_local_asset
from ..capabilities import capabilities_dict
from ..config import AppConfig
from ..domain import Materials, Message, Trigger
from ..playthrough import PlaythroughManager
from ..spec_builder import build_scene_spec
from ..storage.common import StorageConflictError, StorageError
from ..storage.protocol import StorageBackend
from ..timeline import build_context

JsonObject = dict[str, Any]
AssetProvider = Callable[[str | None], Sequence[Mapping[str, Any]]]
CapabilityProvider = Callable[[], Mapping[str, Mapping[str, Any]]]
MediaResolver = Callable[[Mapping[str, Any]], "MediaResource | None"]
GenerationProvider = Callable[[str, str | None], Mapping[str, Any]]
PositionProvider = Callable[[], Mapping[str, Any]]
PositionAction = Callable[[str, str | None, str | None], Mapping[str, Any]]
ReviewProvider = Callable[[str, str, str, str | None], Mapping[str, Any]]

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MIME_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9!#$&^_.+-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VIEW_MODES = frozenset({"story", "god"})
MUTATION_ACTIONS = frozenset(
    {
        "generate",
        "position",
        "review_accept",
        "review_reject",
    }
)


class WebFacadeError(ValueError):
    """A concise error that is safe to show in the local browser UI."""


@dataclass(frozen=True)
class MediaResource:
    """A local file that the HTTP layer may stream after facade authorization."""

    path: Path
    content_type: str
    size_bytes: int


class WebDataProvider(Protocol):
    """Injectable boundary between the HTTP presentation and repository services."""

    def dashboard(
        self, *, view_mode: str | None, trigger_id: str | None = None
    ) -> JsonObject: ...

    def scene(self, trigger_id: str, *, view_mode: str) -> JsonObject: ...

    def capabilities(self) -> JsonObject: ...

    def assets(self, *, trigger_id: str | None = None) -> list[JsonObject]: ...

    def media(self, asset_id: str) -> MediaResource | None: ...

    def validate_action_precondition(
        self, name: str, payload: Mapping[str, Any]
    ) -> str: ...

    def action(self, name: str, payload: Mapping[str, Any]) -> JsonObject: ...


class RepositoryWebFacade:
    """Adapt current repository services without duplicating their logic.

    ``materials`` and ``config`` may be omitted to make an intentionally empty
    presentation.  Playthrough controls remain unavailable until a manager and
    playthrough ID are supplied; opening the UI never creates or changes state.
    """

    def __init__(
        self,
        *,
        materials: Materials | None = None,
        config: AppConfig | None = None,
        manager: PlaythroughManager | None = None,
        storage: StorageBackend | None = None,
        playthrough_id: str | None = None,
        asset_root: str | Path | None = None,
        asset_provider: AssetProvider | None = None,
        capability_provider: CapabilityProvider | None = None,
        media_resolver: MediaResolver | None = None,
        generation_provider: GenerationProvider | None = None,
        position_provider: PositionProvider | None = None,
        position_action: PositionAction | None = None,
        review_provider: ReviewProvider | None = None,
    ) -> None:
        self._materials = materials
        self._config = config
        self._manager = manager
        self._storage = storage or (manager.storage if manager is not None else None)
        self._playthrough_id = playthrough_id
        self._asset_root = (
            Path(asset_root).expanduser().resolve() if asset_root is not None else None
        )
        self._asset_provider = asset_provider
        self._capability_provider = capability_provider
        self._media_resolver = media_resolver
        self._generation_provider = generation_provider
        self._position_provider = position_provider
        self._position_action = position_action
        self._review_provider = review_provider

    @staticmethod
    def _validate_view_mode(view_mode: str) -> str:
        if view_mode not in _VIEW_MODES:
            raise WebFacadeError("view mode must be story or god")
        return view_mode

    @staticmethod
    def _validate_identifier(value: str, label: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise WebFacadeError(f"invalid {label}")
        return value

    @staticmethod
    def _message(message: Message) -> JsonObject:
        return {
            "message_id": message.message_id,
            "at_utc": message.at.isoformat().replace("+00:00", "Z"),
            "role": message.role,
            "name": message.name,
            "kind": message.kind,
            "text": message.text,
            "trigger_id": message.trigger_id,
        }

    def _triggers(self) -> tuple[Trigger, ...]:
        if self._materials is None:
            return ()
        return self._materials.triggers

    def _position(self) -> JsonObject:
        if self._position_provider is not None:
            try:
                return dict(self._position_provider())
            except Exception as exc:
                raise WebFacadeError("story position is temporarily unavailable") from exc
        return {"trigger_id": None, "view_mode": "story", "checkpoint_id": None}

    def _selected_trigger_id(
        self, requested: str | None, *, view_mode: str, position: Mapping[str, Any]
    ) -> str | None:
        triggers = self._triggers()
        if requested is None:
            positioned = position.get("trigger_id")
            if isinstance(positioned, str) and any(
                item.trigger_id == positioned for item in triggers
            ):
                return positioned
            return triggers[0].trigger_id if triggers else None
        self._validate_identifier(requested, "trigger ID")
        if not any(item.trigger_id == requested for item in triggers):
            raise KeyError(requested)
        positioned = position.get("trigger_id")
        if view_mode == "story" and isinstance(positioned, str):
            ids = [item.trigger_id for item in triggers]
            if positioned in ids and ids.index(requested) > ids.index(positioned):
                raise WebFacadeError("future story position is locked")
        return requested

    def _timeline(
        self,
        *,
        view_mode: str,
        unlocked_trigger_id: str | None,
    ) -> list[JsonObject]:
        """Build chapter navigation from progress, not from the inspected chapter.

        A player may revisit an earlier unlocked chapter without re-locking later
        progress.  The selected chapter only controls the current marker; the
        persisted position is the independent unlock boundary.
        """

        unlocked_index = next(
            (
                index
                for index, trigger in enumerate(self._triggers())
                if trigger.trigger_id == unlocked_trigger_id
            ),
            0 if self._triggers() else None,
        )
        timeline: list[JsonObject] = []
        for index, trigger in enumerate(self._triggers()):
            locked = (
                view_mode == "story"
                and unlocked_index is not None
                and index > unlocked_index
            )
            entry: JsonObject = {
                "trigger_id": trigger.trigger_id,
                "at_utc": trigger.at.isoformat().replace("+00:00", "Z"),
                "kind": trigger.kind,
                "locked": locked,
            }
            if not locked:
                entry.update(
                    {
                        "intent": trigger.what,
                        "why": trigger.why,
                        "has_script": trigger.say is not None,
                    }
                )
            timeline.append(entry)
        return timeline

    def _raw_assets(self, trigger_id: str | None = None) -> list[Mapping[str, Any]]:
        try:
            if self._asset_provider is not None:
                records = self._asset_provider(trigger_id)
            elif self._storage is not None and self._playthrough_id is not None:
                records = self._storage.list_assets(self._playthrough_id)
            else:
                records = ()
        except Exception as exc:  # Provider/storage errors must not become tracebacks.
            raise WebFacadeError("asset status is temporarily unavailable") from exc
        result = [record for record in records if isinstance(record, Mapping)]
        if trigger_id is None:
            return result
        return [record for record in result if record.get("trigger_id") == trigger_id]

    def _path_resource(self, asset: Mapping[str, Any]) -> MediaResource | None:
        if self._asset_root is None:
            return None
        verification = verify_local_asset(self._asset_root, asset)
        if not verification.ok or verification.path is None:
            return None
        content_type = verification.content_type or "application/octet-stream"
        if not _MIME_TYPE.fullmatch(content_type):
            content_type = "application/octet-stream"
        return MediaResource(
            path=verification.path,
            content_type=content_type,
            size_bytes=verification.size_bytes or 0,
        )

    def _media_resource(self, asset: Mapping[str, Any]) -> MediaResource | None:
        try:
            if self._media_resolver is not None:
                return self._media_resolver(asset)
            return self._path_resource(asset)
        except (OSError, ValueError):
            return None

    def _asset_view(self, asset: Mapping[str, Any]) -> JsonObject | None:
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not _IDENTIFIER.fullmatch(asset_id):
            return None
        media_type = asset.get("media_type")
        role = asset.get("role")
        resource = self._media_resource(asset)
        verification = (
            verify_local_asset(self._asset_root, asset).to_public_dict()
            if self._asset_root is not None
            else {"status": "unavailable", "size_bytes": None, "sha256": None}
        )
        latest_decision = None
        review_mutable = asset.get("catalog_source") != "release_manifest"
        if (
            review_mutable
            and self._storage is not None
            and self._playthrough_id is not None
        ):
            decisions = [
                item
                for item in self._storage.list_decisions(self._playthrough_id)
                if item.get("subject_kind") == "asset"
                and item.get("subject_id") == asset_id
            ]
            latest_decision = decisions[-1] if decisions else None
        return {
            "asset_id": asset_id,
            "trigger_id": asset.get("trigger_id"),
            "media_type": media_type if isinstance(media_type, str) else "other",
            "role": role if isinstance(role, str) else "candidate",
            "mime_type": asset.get("mime_type")
            if isinstance(asset.get("mime_type"), str)
            else None,
            "size_bytes": asset.get("size_bytes")
            if isinstance(asset.get("size_bytes"), int)
            else None,
            "width_px": asset.get("width_px")
            if isinstance(asset.get("width_px"), int)
            else None,
            "height_px": asset.get("height_px")
            if isinstance(asset.get("height_px"), int)
            else None,
            "duration_ms": asset.get("duration_ms")
            if isinstance(asset.get("duration_ms"), int)
            else None,
            "provenance": asset.get("provenance")
            if isinstance(asset.get("provenance"), str)
            else None,
            "final_eligible": bool(asset.get("final_eligible", False)),
            "sha256": asset.get("sha256")
            if isinstance(asset.get("sha256"), str)
            else None,
            "verification": verification,
            "review_action": (
                latest_decision.get("action")
                if latest_decision is not None
                else asset.get("review_action")
                if asset.get("review_action") in {"accept", "reject"}
                else None
            ),
            "review_reason": (
                latest_decision.get("reason")
                if latest_decision is not None
                else asset.get("review_reason")
                if isinstance(asset.get("review_reason"), str)
                else None
            ),
            "review_mutable": review_mutable,
            "created_at_utc": asset.get("created_at_utc")
            if isinstance(asset.get("created_at_utc"), str)
            else None,
            "preview_available": resource is not None,
            "preview_url": f"/media/{asset_id}" if resource is not None else None,
        }

    def assets(self, *, trigger_id: str | None = None) -> list[JsonObject]:
        if trigger_id is not None:
            self._validate_identifier(trigger_id, "trigger ID")
        visible: list[JsonObject] = []
        for asset in self._raw_assets(trigger_id):
            item = self._asset_view(asset)
            if item is not None:
                visible.append(item)
        return visible

    def media(self, asset_id: str) -> MediaResource | None:
        self._validate_identifier(asset_id, "asset ID")
        for asset in self._raw_assets():
            if asset.get("asset_id") == asset_id:
                return self._media_resource(asset)
        return None

    def capabilities(self) -> JsonObject:
        try:
            if self._capability_provider is not None:
                report = self._capability_provider()
            elif self._config is not None:
                report = capabilities_dict(self._config)
            else:
                report = {}
        except (
            Exception
        ) as exc:  # A capability report is diagnostic, never fatal to the UI.
            raise WebFacadeError(
                "capability status is temporarily unavailable"
            ) from exc
        normalized: JsonObject = {}
        for kind, value in report.items():
            if not isinstance(kind, str) or not isinstance(value, Mapping):
                continue
            normalized[kind] = {
                "state": value.get("state")
                if isinstance(value.get("state"), str)
                else "unknown",
                "provider": value.get("provider")
                if isinstance(value.get("provider"), str)
                else None,
                "reason": value.get("reason")
                if isinstance(value.get("reason"), str)
                else None,
                "final_eligible": bool(value.get("final_eligible", False)),
            }
        return normalized

    def _controls(self) -> JsonObject:
        unavailable: JsonObject = {
            "state": "unavailable",
            "reason": "No playthrough backend is attached to this presentation.",
            "playthrough_id": None,
            "head": None,
            "generation_available": False,
            "position_available": False,
            "review_available": False,
        }
        if (
            self._manager is None
            or self._storage is None
            or self._playthrough_id is None
        ):
            return unavailable
        try:
            head = self._storage.get_head(self._playthrough_id)
        except Exception:  # noqa: BLE001 - optional backend must degrade safely in the UI.
            return {
                **unavailable,
                "reason": "The configured playthrough backend could not be read.",
            }
        return {
            "state": "available",
            "reason": None,
            "playthrough_id": self._playthrough_id,
            "head": {
                "checkpoint_id": head.get("checkpoint_id"),
            },
            "generation_available": self._generation_provider is not None,
            "position_available": self._position_action is not None,
            "review_available": self._review_provider is not None,
            "position": self._position(),
        }

    def _generation_status(self, trigger_id: str | None) -> JsonObject:
        if self._storage is None or self._playthrough_id is None:
            return {"runs": [], "provider_calls": [], "decisions": []}
        runs = [
            item
            for item in self._storage.list_runs(self._playthrough_id)
            if trigger_id is None
            or any(
                asset.get("run_id") == item.get("run_id")
                and asset.get("trigger_id") == trigger_id
                for asset in self._storage.list_assets(self._playthrough_id)
            )
        ]
        run_ids = {item["run_id"] for item in runs}
        calls = [
            item
            for item in self._storage.list_provider_calls(self._playthrough_id)
            if item.get("run_id") in run_ids
        ]
        decisions = [
            item
            for item in self._storage.list_decisions(self._playthrough_id)
            if item.get("run_id") in run_ids
        ]
        return {
            "runs": [
                {
                    "run_id": item.get("run_id"),
                    "status": item.get("status"),
                    "mode": item.get("mode"),
                    "safe_error": item.get("safe_error"),
                    "created_at_utc": item.get("created_at_utc"),
                    "finished_at_utc": item.get("finished_at_utc"),
                }
                for item in runs
            ],
            "provider_calls": [
                {
                    "call_id": item.get("call_id"),
                    "run_id": item.get("run_id"),
                    "provider": item.get("provider"),
                    "model": item.get("model"),
                    "purpose": item.get("purpose"),
                    "status": item.get("status"),
                    "billing_state": item.get("billing_state"),
                    "latency_ms": item.get("latency_ms"),
                    "cost_amount_micros": item.get("cost_amount_micros"),
                    "error_code": item.get("error_code"),
                }
                for item in calls
            ],
            "decisions": [
                {
                    "decision_id": item.get("decision_id"),
                    "subject_kind": item.get("subject_kind"),
                    "subject_id": item.get("subject_id"),
                    "action": item.get("action"),
                    "reason": item.get("reason"),
                    "decided_by": item.get("decided_by"),
                }
                for item in decisions
            ],
        }

    def dashboard(
        self, *, view_mode: str | None, trigger_id: str | None = None
    ) -> JsonObject:
        position = self._position()
        selected_mode = view_mode or position.get("view_mode") or "story"
        view_mode = self._validate_view_mode(str(selected_mode))
        selected = self._selected_trigger_id(
            trigger_id, view_mode=view_mode, position=position
        )
        scene: JsonObject | None = None
        if selected is not None:
            scene = self.scene(selected, view_mode=view_mode)
        return {
            "view_mode": view_mode,
            "selected_trigger_id": selected,
            "timeline": self._timeline(
                view_mode=view_mode,
                unlocked_trigger_id=(
                    str(positioned)
                    if isinstance((positioned := position.get("trigger_id")), str)
                    else None
                ),
            ),
            "scene": scene,
            "capabilities": self.capabilities(),
            "assets": self.assets(trigger_id=selected),
            "controls": self._controls(),
            "generation_status": self._generation_status(selected),
            "position": position,
            "empty": selected is None,
        }

    def scene(self, trigger_id: str, *, view_mode: str) -> JsonObject:
        view_mode = self._validate_view_mode(view_mode)
        trigger_id = self._validate_identifier(trigger_id, "trigger ID")
        if self._materials is None:
            raise KeyError(trigger_id)
        context = build_context(self._materials, trigger_id)
        spec = build_scene_spec(self._materials, trigger_id).to_dict()
        trigger_message = (
            self._message(context.trigger_message)
            if context.trigger_message is not None
            else None
        )
        future_locks = [
            {
                "message_id": message.message_id,
                "at_utc": message.at.isoformat().replace("+00:00", "Z"),
                "kind": message.kind,
                "trigger_id": message.trigger_id,
            }
            for message in context.future_messages
        ]
        flow: JsonObject = {
            "visible": [self._message(message) for message in context.visible_messages],
            "trigger": trigger_message,
            "future_locked": future_locks,
            "future_count": len(future_locks),
        }
        if view_mode == "god":
            flow["all"] = [
                self._message(message) for message in self._materials.messages
            ]
        generation = spec.get("generation")
        generation_data = generation if isinstance(generation, Mapping) else {}
        return {
            "view_mode": view_mode,
            "trigger": {
                "trigger_id": context.trigger.trigger_id,
                "at_utc": context.trigger.at.isoformat().replace("+00:00", "Z"),
                "kind": context.trigger.kind,
                "intent": context.trigger.what,
                "why": context.trigger.why,
                "say": context.trigger.say,
            },
            "state": deepcopy(context.state.raw) if context.state is not None else None,
            "message_flow": flow,
            "scene_spec": spec,
            "evidence": deepcopy(spec.get("evidence", [])),
            "conflicts": deepcopy(spec.get("conflicts", [])),
            "future_locks": future_locks,
            "assets": self.assets(trigger_id=trigger_id),
            "eligibility": {
                "scene_spec_final_eligible": bool(
                    generation_data.get("final_eligible", False)
                ),
                "human_review_required": bool(spec.get("human_review_required", False)),
            },
        }

    def _require_controls(self) -> tuple[PlaythroughManager, StorageBackend, str]:
        if (
            self._manager is None
            or self._storage is None
            or self._playthrough_id is None
        ):
            raise WebFacadeError("playthrough controls are not configured")
        return self._manager, self._storage, self._playthrough_id

    @staticmethod
    def _required_text(
        payload: Mapping[str, Any], key: str, *, maximum: int = 200
    ) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise WebFacadeError(f"{key} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_text(
        payload: Mapping[str, Any], key: str, *, maximum: int = 200
    ) -> str | None:
        value = payload.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, str) or len(value) > maximum:
            raise WebFacadeError(f"{key} must be a string")
        return value.strip() or None

    def _require_expected_head(
        self,
        storage: StorageBackend,
        playthrough_id: str,
        payload: Mapping[str, Any],
    ) -> str:
        """Require a browser mutation to be anchored to the current checkpoint.

        The HTTP server serializes this check with the subsequent mutation. The
        facade repeats it for direct callers, so every presentation entry point
        rejects a stale browser state before any append-only write.
        """

        expected_checkpoint_id = self._required_text(
            payload, "expected_checkpoint_id", maximum=128
        )
        self._validate_identifier(expected_checkpoint_id, "checkpoint ID")
        head = storage.get_head(playthrough_id)
        if head.get("checkpoint_id") != expected_checkpoint_id:
            raise StorageConflictError("playthrough changed; refresh and retry")
        return expected_checkpoint_id

    def validate_action_precondition(
        self, name: str, payload: Mapping[str, Any]
    ) -> str:
        """Validate the expected checkpoint immediately before a mutation."""

        if name not in MUTATION_ACTIONS:
            raise WebFacadeError("unknown control action")
        if not isinstance(payload, Mapping):
            raise WebFacadeError("action payload must be an object")
        _, storage, playthrough_id = self._require_controls()
        return self._require_expected_head(storage, playthrough_id, payload)

    def action(self, name: str, payload: Mapping[str, Any]) -> JsonObject:
        if not isinstance(payload, Mapping):
            raise WebFacadeError("action payload must be an object")
        if name not in MUTATION_ACTIONS:
            raise WebFacadeError("unknown control action")
        _manager, storage, playthrough_id = self._require_controls()
        expected_checkpoint_id = self._require_expected_head(
            storage, playthrough_id, payload
        )
        if name == "generate":
            if self._generation_provider is None:
                raise WebFacadeError("generation is not configured")
            trigger_id = self._required_text(payload, "trigger_id", maximum=128)
            self._validate_identifier(trigger_id, "trigger ID")
            try:
                result = dict(
                    self._generation_provider(trigger_id, expected_checkpoint_id)
                )
            except StorageError:
                raise
            except Exception as exc:
                raise WebFacadeError(
                    "generation failed or the playthrough changed; refresh and retry"
                ) from exc
        elif name == "position":
            if self._position_action is None:
                raise WebFacadeError("position changes are not configured")
            trigger_id = self._required_text(payload, "trigger_id", maximum=128)
            view_mode = self._optional_text(payload, "view_mode", maximum=20)
            try:
                result = dict(
                    self._position_action(
                        trigger_id, view_mode, expected_checkpoint_id
                    )
                )
            except StorageError:
                raise
            except Exception as exc:
                raise WebFacadeError(
                    "position change failed; refresh and retry"
                ) from exc
        elif name in {"review_accept", "review_reject"}:
            if self._review_provider is None:
                raise WebFacadeError("human review is not configured")
            asset_id = self._required_text(payload, "asset_id", maximum=128)
            reason = self._required_text(payload, "reason", maximum=2000)
            expected_sha256: str | None
            if name == "review_accept":
                expected_sha256 = self._required_text(
                    payload, "expected_sha256", maximum=64
                )
                if not _SHA256.fullmatch(expected_sha256):
                    raise WebFacadeError(
                        "expected_sha256 must be a lowercase SHA-256 digest"
                    )
            else:
                expected_sha256 = self._optional_text(
                    payload, "expected_sha256", maximum=64
                )
            result = dict(
                self._review_provider(
                    asset_id,
                    "accept" if name == "review_accept" else "reject",
                    reason,
                    expected_sha256,
                )
            )
        return {
            "action": name,
            "result": deepcopy(result),
            "controls": self._controls(),
        }
