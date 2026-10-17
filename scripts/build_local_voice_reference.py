#!/usr/bin/env python3
"""Build a text-designed local preset-voice reference with an audit record."""

from __future__ import annotations

import argparse
import json
import os
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from nining.config import ModelConfig
from nining.providers import MacOSSayVoiceAdapter, ProviderRequest


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    with (root / "voice_profile.example.toml").open("rb") as handle:
        profile = tomllib.load(handle)
    identity = profile["identity"]
    reference = profile["reference"]
    preset = str(identity["preset_voice_id"]).removeprefix("macos:")
    script = str(reference["text"])
    rate = 220
    request = ProviderRequest(
        kind="voice",
        prompt=str(identity["base_instructions"]),
        parameters={
            "script": script,
            "voice": preset,
            "rate": rate,
            "output_format": "aiff",
        },
        final_eligible=True,
    )
    adapter = MacOSSayVoiceAdapter(
        ModelConfig(
            provider="macos_say",
            enabled=True,
            extras={
                "executable": "/usr/bin/say",
                "voice": preset,
                "rate": rate,
                "output_format": "aiff",
            },
        )
    )
    started_at = _utc_now()
    started = time.monotonic_ns()
    result = adapter.execute(request)
    latency_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
    finished_at = _utc_now()
    if not result.ok or result.audio is None:
        print(json.dumps(result.to_audit_dict(), ensure_ascii=False, sort_keys=True))
        return 2
    duration = result.metadata.get("duration_ms")
    if not isinstance(duration, int) or duration > 10_000:
        print(json.dumps({"status": "error", "error": "reference exceeds 10 seconds"}))
        return 2
    output = root / "outputs" / "references" / "voice" / "nining-reference.aiff"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".aiff.tmp")
    temporary.write_bytes(result.audio)
    temporary.replace(output)
    audit = {
        "schema_version": "1.0",
        "purpose": "fictional-character preset voice reference",
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "latency_ms": latency_ms,
        "cost_amount_micros": 0,
        "cost_currency": None,
        "billing_state": "zero",
        "seed": None,
        "seed_supported": False,
        "request": request.to_audit_dict(),
        "result": result.to_audit_dict(),
        "relative_path": "outputs/references/voice/nining-reference.aiff",
        "human_review_required": True,
        "accepted": False,
    }
    audit_path = output.with_suffix(".audit.json")
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    log_path = root / "logs" / "reference_voice.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(audit, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
