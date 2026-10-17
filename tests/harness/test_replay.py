from __future__ import annotations

from typing import Any

import pytest

from nining.errors import HarnessInvariantError
from nining.harness import (
    ActionRegistry,
    ActionSpec,
    Decision,
    HarnessLoop,
    Observation,
    replay_records,
)
from nining.trace import InMemoryTraceSink, TraceEmitter

from .conftest import SequenceIds, fixed_clock


def make_deterministic_trace() -> list[dict[str, Any]]:
    sink = InMemoryTraceSink()
    emitter = TraceEmitter(sink, clock=fixed_clock, id_factory=SequenceIds())
    registry = ActionRegistry()
    registry.register(
        ActionSpec(
            "local.echo",
            lambda inputs, context: {
                "echo": inputs["message"],
                "key_seen": context.idempotency_key,
            },
            safe_to_retry=True,
        )
    )
    loop = HarnessLoop(
        registry=registry,
        observer=lambda snapshot: Observation(
            "deterministic observation", {"attempt": snapshot.attempt}
        ),
        decider=lambda snapshot: Decision(
            "local.echo",
            {"message": "hello"},
            "echo the fixture value",
            "echo-operation",
        ),
        trace=emitter,
    )
    loop.run(run_id="replay-run", requirement_ids=("REQ-TRACE",))
    return sink.records


def test_deterministic_trace_and_nonexecuting_replay() -> None:
    first = make_deterministic_trace()
    second = make_deterministic_trace()

    assert first == second
    summary_one = replay_records(first)
    summary_two = replay_records(second)
    assert summary_one == summary_two
    assert summary_one.run_id == "replay-run"
    assert summary_one.attempts == 1
    assert summary_one.stop_reason == "completed"
    assert summary_one.requirement_ids == ("REQ-TRACE",)


def test_replay_rejects_noncontiguous_or_unfinished_trace() -> None:
    records = make_deterministic_trace()
    records[1] = {**records[1], "sequence": 99}
    with pytest.raises(HarnessInvariantError, match="sequence"):
        replay_records(records)

    unfinished = make_deterministic_trace()[:-1]
    with pytest.raises(HarnessInvariantError, match="terminal stop"):
        replay_records(unfinished)

