from __future__ import annotations

import json
from typing import Any

from nining.trace import JsonlTraceSink, TraceEmitter, sanitize

from .conftest import SequenceIds, fixed_clock


def test_recursive_redaction_covers_headers_inline_values_entropy_and_private_reasoning() -> None:
    fake_token = "TEST_ONLY_AbCDefghijkLMNopqrSTUV0123456789_xyz"
    source = {
        "Authorization": f"Bearer {fake_token}",
        "api-key": fake_token,
        "nested": {
            "cookie": "session=fake",
            "note": f"Authorization: Bearer {fake_token}",
            "opaque": fake_token,
            "chain_of_thought": "must never persist",
        },
        "run_id": fake_token,
        "idempotency_key": fake_token,
        "content_sha256": "a" * 64,
        "prompt": "raw private prompt",
        "link": "https://internal.example.invalid/path?credential=fake",
    }

    cleaned = sanitize(source)

    assert cleaned["Authorization"] == "[REDACTED]"
    assert cleaned["api-key"] == "[REDACTED]"
    assert cleaned["nested"]["cookie"] == "[REDACTED]"
    assert fake_token not in cleaned["nested"]["note"]
    assert cleaned["nested"]["opaque"] == "[REDACTED:high-entropy]"
    assert cleaned["nested"]["chain_of_thought"] == "[OMITTED:private-reasoning]"
    assert cleaned["run_id"] == fake_token
    assert cleaned["idempotency_key"] == fake_token
    assert cleaned["content_sha256"] == "a" * 64
    assert cleaned["prompt"]["length"] == len("raw private prompt")
    assert "raw private prompt" not in json.dumps(cleaned)
    assert cleaned["link"] == "[REDACTED:URL]"


def test_jsonl_sink_persists_only_sanitized_payload(tmp_path: Any) -> None:
    path = tmp_path / "trace.jsonl"
    token = "TEST_ONLY_ZXcvBNMasdfGHJKqwertYUIO12345678_secret"
    emitter = TraceEmitter(
        JsonlTraceSink(path), clock=fixed_clock, id_factory=SequenceIds()
    )

    emitter.emit(
        run_id="safe-run",
        state="observe",
        attempt=0,
        observation={
            "summary": "fixture",
            "headers": {"api_key": token},
            "provider_message": f"Bearer {token}",
        },
    )

    raw = path.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert token not in raw
    assert record["observation"]["headers"]["api_key"] == "[REDACTED]"
    assert record["sequence"] == 1
    assert record["timestamp"] == "2026-10-15T08:00:00Z"
    assert record["schema_version"] == "1.0"

