from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nining.trace import InMemoryTraceSink, TraceEmitter


class SequenceIds:
    def __init__(self, prefix: str = "event") -> None:
        self.prefix = prefix
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.prefix}-{self.value:03d}"


def fixed_clock() -> datetime:
    return datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


@pytest.fixture
def trace_pair() -> tuple[TraceEmitter, InMemoryTraceSink]:
    sink = InMemoryTraceSink()
    emitter = TraceEmitter(sink, clock=fixed_clock, id_factory=SequenceIds())
    return emitter, sink

