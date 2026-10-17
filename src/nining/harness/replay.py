"""Deterministic, non-executing validation and replay of public trace records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nining.errors import HarnessInvariantError
from nining.trace import trace_digest

_ALLOWED_NEXT: dict[str | None, set[str]] = {
    None: {"observe", "error", "bug"},
    "observe": {"decide", "error", "bug"},
    "decide": {"act", "stop", "error", "bug"},
    "act": {"evaluate", "error", "bug"},
    "error": {"evaluate", "stop", "bug"},
    "evaluate": {"correct", "stop", "error", "bug"},
    "correct": {"observe", "stop", "error", "bug"},
    "bug": {"stop"},
    "stop": set(),
}


@dataclass(frozen=True)
class ReplaySummary:
    run_id: str
    record_count: int
    attempts: int
    stop_reason: str
    requirement_ids: tuple[str, ...]
    digest: str


def replay_records(records: Sequence[Mapping[str, Any]]) -> ReplaySummary:
    """Validate state order and reconstruct the terminal public outcome.

    Replaying never invokes an action or provider.  This makes incident review
    deterministic and safe even when the original run contained paid actions.
    """

    if not records:
        raise HarnessInvariantError("cannot replay an empty trace")
    run_ids = {str(record.get("run_id")) for record in records}
    if len(run_ids) != 1:
        raise HarnessInvariantError("trace replay requires exactly one run_id")
    previous: str | None = None
    attempts = 0
    requirements: set[str] = set()
    for expected_sequence, record in enumerate(records, start=1):
        if record.get("sequence") != expected_sequence:
            raise HarnessInvariantError(
                "trace sequence is not contiguous",
                details={"expected": expected_sequence, "actual": record.get("sequence")},
            )
        state = str(record.get("state"))
        if state not in _ALLOWED_NEXT.get(previous, set()):
            raise HarnessInvariantError(
                f"invalid trace transition: {previous!r} -> {state!r}"
            )
        if state == "act":
            attempts += 1
        requirements.update(str(item) for item in record.get("requirement_ids", ()))
        previous = state
    terminal = records[-1]
    if terminal.get("state") != "stop":
        raise HarnessInvariantError("trace has no terminal stop record")
    stop_reason = terminal.get("stop_reason")
    if not isinstance(stop_reason, str) or not stop_reason:
        raise HarnessInvariantError("terminal stop record lacks stop_reason")
    return ReplaySummary(
        run_id=next(iter(run_ids)),
        record_count=len(records),
        attempts=attempts,
        stop_reason=stop_reason,
        requirement_ids=tuple(sorted(requirements)),
        digest=trace_digest(records),
    )

