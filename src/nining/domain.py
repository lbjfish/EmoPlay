"""Immutable domain objects for materials, evidence, context, and scene specs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .canonical import canonical_sha256, format_utc

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class SourceRef:
    file: str
    record_id: str | None = None
    line: int | None = None

    def to_dict(self) -> JsonObject:
        return {"file": self.file, "record_id": self.record_id, "line": self.line}


@dataclass(frozen=True)
class Message:
    message_id: str
    at: datetime
    sequence_no: int
    role: str
    name: str | None
    kind: str
    text: str | None
    trigger_id: str | None
    raw: JsonObject
    source: SourceRef

    def to_evidence(self, visibility: str = "visible") -> JsonObject:
        return {
            "evidence_id": f"message:{self.message_id}",
            "source": self.source.to_dict(),
            "at_utc": format_utc(self.at),
            "visibility": visibility,
            "value": {
                "role": self.role,
                "name": self.name,
                "kind": self.kind,
                "text": self.text,
                "trigger": self.trigger_id,
            },
        }


@dataclass(frozen=True)
class Trigger:
    trigger_id: str
    at: datetime
    kind: str
    what: str
    why: str | None
    say: str | None
    raw: JsonObject
    source: SourceRef


@dataclass(frozen=True)
class StateSnapshot:
    trigger_id: str
    at: datetime
    raw: JsonObject
    source: SourceRef


@dataclass(frozen=True)
class Materials:
    root_name: str
    persona: JsonObject
    user: JsonObject
    messages: tuple[Message, ...]
    triggers: tuple[Trigger, ...]
    states: tuple[StateSnapshot, ...]
    sales_catalog: JsonObject | None = None
    sales_messages: tuple[JsonObject, ...] = ()
    manifest: JsonObject = field(default_factory=dict)

    @property
    def triggers_by_id(self) -> dict[str, Trigger]:
        return {item.trigger_id: item for item in self.triggers}

    @property
    def states_by_trigger(self) -> dict[str, StateSnapshot]:
        return {item.trigger_id: item for item in self.states}


@dataclass(frozen=True)
class ContextPack:
    trigger: Trigger
    state: StateSnapshot | None
    visible_messages: tuple[Message, ...]
    trigger_message: Message | None
    future_messages: tuple[Message, ...]
    conflicts: tuple[JsonObject, ...]
    fulfilled_promises: tuple[str, ...]
    evidence: tuple[JsonObject, ...]

    def to_dict(self) -> JsonObject:
        state_raw = self.state.raw if self.state else None
        return {
            "trigger": {
                "id": self.trigger.trigger_id,
                "at_utc": format_utc(self.trigger.at),
                "kind": self.trigger.kind,
                "what": self.trigger.what,
                "why": self.trigger.why,
                "say": self.trigger.say,
            },
            "state": state_raw,
            "visible_message_ids": [item.message_id for item in self.visible_messages],
            "trigger_message_id": self.trigger_message.message_id if self.trigger_message else None,
            "future_locked_message_ids": [item.message_id for item in self.future_messages],
            "conflicts": list(self.conflicts),
            "fulfilled_promises": list(self.fulfilled_promises),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class SceneSpec:
    payload: JsonObject

    def to_dict(self) -> JsonObject:
        return deepcopy(self.payload)

    @property
    def sha256(self) -> str:
        return canonical_sha256(
            {key: value for key, value in self.payload.items() if key != "spec_sha256"}
        )

