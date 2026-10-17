"""Explicit action registry with deterministic capability-aware fallback."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from nining.errors import ActionNotRegisteredError, CapabilityUnavailableError

from .models import ActionContext, ActionResult

ActionHandler = Callable[[Mapping[str, Any], ActionContext], ActionResult | Mapping[str, Any]]
_ACTION_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")


@dataclass(frozen=True)
class ActionSpec:
    name: str
    handler: ActionHandler
    description: str = ""
    capability: str | None = None
    accepted_capability_states: tuple[str, ...] = ("available",)
    fallbacks: tuple[str, ...] = ()
    paid: bool = False
    safe_to_retry: bool = False
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ACTION_NAME.fullmatch(self.name):
            raise ValueError(f"invalid action name: {self.name!r}")
        if self.name in self.fallbacks:
            raise ValueError("an action cannot fall back to itself")


@dataclass(frozen=True)
class ActionResolution:
    requested_name: str
    selected_name: str
    spec: ActionSpec
    used_fallback: bool
    reason: str
    considered: tuple[str, ...] = field(default_factory=tuple)


def _capability_state(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("state", "unavailable"))
    return str(getattr(value, "state", "unavailable"))


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> None:
        if spec.name in self._actions:
            raise ValueError(f"action already registered: {spec.name}")
        self._actions[spec.name] = spec

    def action(
        self,
        name: str,
        **metadata: Any,
    ) -> Callable[[ActionHandler], ActionHandler]:
        def decorator(handler: ActionHandler) -> ActionHandler:
            self.register(ActionSpec(name=name, handler=handler, **metadata))
            return handler

        return decorator

    def get(self, name: str) -> ActionSpec:
        try:
            return self._actions[name]
        except KeyError as exc:
            raise ActionNotRegisteredError(
                f"action is not registered: {name}", details={"action_name": name}
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._actions))

    def _available(self, spec: ActionSpec, capabilities: Mapping[str, Any]) -> tuple[bool, str]:
        if spec.capability is None:
            return True, "action requires no external capability"
        state = _capability_state(capabilities.get(spec.capability))
        if state in spec.accepted_capability_states:
            return True, f"capability {spec.capability} is {state}"
        return False, f"capability {spec.capability} is {state}"

    def resolve(
        self,
        requested_name: str,
        capabilities: Mapping[str, Any] | None = None,
    ) -> ActionResolution:
        capabilities = capabilities or {}
        requested = self.get(requested_name)
        queue: list[str] = [requested.name, *requested.fallbacks]
        considered: list[str] = []
        seen: set[str] = set()

        while queue:
            name = queue.pop(0)
            if name in seen:
                continue
            seen.add(name)
            considered.append(name)
            spec = self.get(name)
            available, reason = self._available(spec, capabilities)
            if available:
                return ActionResolution(
                    requested_name=requested_name,
                    selected_name=name,
                    spec=spec,
                    used_fallback=name != requested_name,
                    reason=(
                        reason
                        if name == requested_name
                        else f"fallback selected after unavailable primary: {reason}"
                    ),
                    considered=tuple(considered),
                )
            queue.extend(spec.fallbacks)

        raise CapabilityUnavailableError(
            f"no available action for {requested_name}",
            details={"requested_action": requested_name, "considered": considered},
        )

