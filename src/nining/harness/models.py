"""Value objects shared by the harness loop and action implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from nining.errors import ClassifiedError


class LoopState(StrEnum):
    OBSERVE = "observe"
    DECIDE = "decide"
    ACT = "act"
    EVALUATE = "evaluate"
    CORRECT = "correct"
    STOP = "stop"
    ERROR = "error"
    BUG = "bug"


@dataclass(frozen=True)
class Observation:
    summary: str
    facts: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    """An explicit action choice and concise, public rationale summary."""

    action_name: str | None
    inputs: Mapping[str, Any] = field(default_factory=dict)
    rationale_summary: str = ""
    operation_id: str = "goal"
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if bool(self.action_name) == bool(self.stop_reason):
            raise ValueError("decision requires exactly one of action_name or stop_reason")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionContext:
    run_id: str
    attempt: int
    idempotency_key: str
    requirement_ids: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    value: Mapping[str, Any] = field(default_factory=dict)
    error: ClassifiedError | None = None
    execution_status: str = "completed"
    charged: bool | None = False

    @classmethod
    def success(
        cls,
        value: Mapping[str, Any] | None = None,
        *,
        charged: bool | None = False,
    ) -> ActionResult:
        return cls(True, dict(value or {}), None, "completed", charged)

    @classmethod
    def failure(
        cls,
        error: ClassifiedError,
        *,
        execution_status: str = "unknown",
        charged: bool | None = None,
        value: Mapping[str, Any] | None = None,
    ) -> ActionResult:
        return cls(False, dict(value or {}), error, execution_status, charged)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.error:
            data["error"] = self.error.to_dict()
        return data


@dataclass(frozen=True)
class Evaluation:
    passed: bool
    summary: str
    correctable: bool = False
    checks: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Correction:
    """A proposed automatic correction; policy still decides whether it may run."""

    retry: bool
    rationale_summary: str
    action_name: str | None = None
    inputs: Mapping[str, Any] | None = None
    operation_id: str | None = None
    stop_reason: str | None = None
    automatic: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoopSnapshot:
    run_id: str
    attempt: int
    requirements: tuple[str, ...]
    context: Mapping[str, Any]
    observation: Observation | None = None
    decision: Decision | None = None
    selected_action: str | None = None
    result: ActionResult | None = None
    evaluation: Evaluation | None = None
    correction: Correction | None = None


@dataclass(frozen=True)
class LoopOutcome:
    run_id: str
    succeeded: bool
    stop_reason: str
    attempts: int
    final_result: ActionResult | None
    final_evaluation: Evaluation | None
    requirement_ids: tuple[str, ...]

