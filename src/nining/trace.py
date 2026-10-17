"""Sanitized, append-only JSONL traces for observable harness decisions."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

TRACE_SCHEMA_VERSION = "1.0"
REDACTED = "[REDACTED]"
PRIVATE_REASONING_OMITTED = "[OMITTED:private-reasoning]"

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_ -]?key|cookie|secret|password|passwd|"
    r"access[_ -]?token|refresh[_ -]?token|credential)",
    re.IGNORECASE,
)
_PRIVATE_REASONING_KEY = re.compile(
    r"(?:chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|scratchpad|"
    r"reasoning[_ -]?tokens)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_INLINE_SECRET = re.compile(
    r"(?i)\b(authorization|api[_ -]?key|cookie|secret|password|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_URL = re.compile(r"(?i)\bhttps?://[^\s\"']+")
_PRIVATE_CONTENT_KEY = re.compile(
    r"(?:^|_)(?:content|instructions?|messages?|prompt|provider_message|response_body)(?:$|_)",
    re.IGNORECASE,
)
_TOKENISH = re.compile(r"^[A-Za-z0-9_+/.=-]+$")
_SAFE_HIGH_ENTROPY_KEYS = {
    "run_id",
    "event_id",
    "occurrence_id",
    "bug_id",
    "idempotency_key",
    "requirement_id",
    "requirement_ids",
    "sha256",
    "digest",
    "checksum",
    "content_hash",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def random_id() -> str:
    return uuid4().hex


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _entropy(value: str) -> float:
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    size = len(value)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def _looks_high_entropy(value: str) -> bool:
    if len(value) < 24 or len(value) > 8192 or not _TOKENISH.fullmatch(value):
        return False
    classes = sum(
        bool(re.search(pattern, value))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[_+/.=-]")
    )
    return classes >= 3 and _entropy(value) >= 3.6


def _safe_high_entropy_key(key: str | None) -> bool:
    if key is None:
        return False
    lowered = key.lower()
    return lowered in _SAFE_HIGH_ENTROPY_KEYS or lowered.endswith(
        ("_sha256", "_digest", "_checksum", "_hash")
    )


def _sanitize_string(value: str, key: str | None) -> str:
    value = _URL.sub("[REDACTED:URL]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", value)
    if not _safe_high_entropy_key(key) and _looks_high_entropy(value):
        return "[REDACTED:high-entropy]"
    return value


def sanitize(value: Any, *, key: str | None = None, _depth: int = 0) -> Any:
    """Return a JSON-safe structure with secrets and private reasoning removed."""

    if _depth > 30:
        return "[TRUNCATED:max-depth]"
    if key is not None and _PRIVATE_REASONING_KEY.search(key):
        return PRIVATE_REASONING_OMITTED
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if (
        key is not None
        and not _safe_high_entropy_key(key)
        and _PRIVATE_CONTENT_KEY.search(key)
        and isinstance(value, str)
    ):
        return {
            "sha256": sha256(value.encode("utf-8")).hexdigest(),
            "length": len(value),
        }
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return _sanitize_string(value, key)
    if isinstance(value, bytes):
        return {"bytes_length": len(value), "sha256": sha256(value).hexdigest()}
    if isinstance(value, Path):
        return _sanitize_string(str(value), key)
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return sanitize(value.value, key=key, _depth=_depth + 1)
    if is_dataclass(value) and not isinstance(value, type):
        return sanitize(asdict(value), key=key, _depth=_depth + 1)
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize(
                item_value, key=str(item_key), _depth=_depth + 1
            )
            for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item, key=key, _depth=_depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        sanitized = [sanitize(item, key=key, _depth=_depth + 1) for item in value]
        return sorted(sanitized, key=canonical_json)
    return _sanitize_string(str(value), key)


@dataclass(frozen=True)
class TraceRecord:
    """Stable trace schema; absent phase payloads remain explicit ``null`` fields."""

    sequence: int
    timestamp: str
    run_id: str
    event_id: str
    state: str
    attempt: int
    requirement_ids: tuple[str, ...] = ()
    observation: Mapping[str, Any] | None = None
    decision: Mapping[str, Any] | None = None
    action: Mapping[str, Any] | None = None
    result: Mapping[str, Any] | None = None
    evaluation: Mapping[str, Any] | None = None
    correction: Mapping[str, Any] | None = None
    stop_reason: str | None = None
    error: Mapping[str, Any] | None = None
    bug: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = TRACE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


class TraceSink(Protocol):
    def emit(self, record: TraceRecord) -> None: ...


class NullTraceSink:
    def emit(self, record: TraceRecord) -> None:
        del record


class InMemoryTraceSink:
    """Test and embedding sink retaining already-sanitized dictionaries."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def emit(self, record: TraceRecord) -> None:
        self.records.append(record.to_dict())


class JsonlTraceSink:
    """Thread-safe append-only JSONL writer."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def emit(self, record: TraceRecord) -> None:
        line = canonical_json(record.to_dict()) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())


class TraceEmitter:
    """Assign deterministic sequence, time, and IDs before sending records."""

    def __init__(
        self,
        sink: TraceSink,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = random_id,
    ) -> None:
        self.sink = sink
        self.clock = clock
        self.id_factory = id_factory
        self._sequence_by_run: dict[str, int] = {}

    def emit(
        self,
        *,
        run_id: str,
        state: str,
        attempt: int,
        requirement_ids: Sequence[str] = (),
        observation: Mapping[str, Any] | None = None,
        decision: Mapping[str, Any] | None = None,
        action: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        evaluation: Mapping[str, Any] | None = None,
        correction: Mapping[str, Any] | None = None,
        stop_reason: str | None = None,
        error: Mapping[str, Any] | None = None,
        bug: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> TraceRecord:
        sequence = self._sequence_by_run.get(run_id, 0) + 1
        self._sequence_by_run[run_id] = sequence
        timestamp = self.clock()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        record = TraceRecord(
            sequence=sequence,
            timestamp=timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            run_id=run_id,
            event_id=self.id_factory(),
            state=state,
            attempt=attempt,
            requirement_ids=tuple(sorted(set(requirement_ids))),
            observation=observation,
            decision=decision,
            action=action,
            result=result,
            evaluation=evaluation,
            correction=correction,
            stop_reason=stop_reason,
            error=error,
            bug=bug,
            metadata=metadata or {},
        )
        self.sink.emit(record)
        return record


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise TypeError(f"trace line {line_number} must be an object")
            records.append(item)
    return records


def trace_digest(records: Iterable[Mapping[str, Any]]) -> str:
    payload = "\n".join(canonical_json(sanitize(record)) for record in records)
    return sha256(payload.encode("utf-8")).hexdigest()

