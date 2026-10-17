from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path

from nining.release_manifest import import_voice_reference
from nining.storage import FileStorage

HASH_A = "a" * 64


def _write_aiff(path: Path, *, seconds: float) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_rate = 1_000
    frames = b"\x00\x00" * int(frame_rate * seconds)
    frame_count = len(frames) // 2
    comm = (
        b"COMM"
        + (18).to_bytes(4, "big")
        + (1).to_bytes(2, "big")
        + frame_count.to_bytes(4, "big")
        + (16).to_bytes(2, "big")
        + _extended80(frame_rate)
    )
    ssnd_payload = (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + frames
    ssnd = b"SSND" + len(ssnd_payload).to_bytes(4, "big") + ssnd_payload
    body = b"AIFF" + comm + ssnd
    path.write_bytes(b"FORM" + len(body).to_bytes(4, "big") + body)
    return path.read_bytes()


def _extended80(value: int) -> bytes:
    exponent = math.floor(math.log2(value))
    mantissa = int((value / (2**exponent)) * (2**63))
    return (exponent + 16383).to_bytes(2, "big") + mantissa.to_bytes(8, "big")


def _write_manifest(root: Path) -> None:
    path = root / "outputs" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "materials_manifest_sha256": HASH_A,
            }
        ),
        encoding="utf-8",
    )


def _write_audit(path: Path, *, audio: bytes, duration_ms: int) -> None:
    audit = {
        "schema_version": "1.0",
        "started_at_utc": "2026-10-16T00:00:00Z",
        "finished_at_utc": "2026-10-16T00:00:01Z",
        "latency_ms": 1000,
        "cost_amount_micros": None,
        "cost_currency": None,
        "relative_path": "outputs/references/voice/nining-reference.aiff",
        "request": {"request_sha256": "b" * 64, "prompt_sha256": "c" * 64},
        "result": {
            "provider": "macos_say",
            "model": "macos-say:Tingting",
            "request_sha256": "b" * 64,
            "prompt_sha256": "c" * 64,
            "billing_state": "unknown",
            "status": "success",
            "final_eligible": True,
            "usage": {},
            "metadata": {"duration_ms": duration_ms, "mime_type": "audio/aiff"},
            "voice_spec": {"preset": "Tingting"},
            "audio": {
                "bytes_length": len(audio),
                "sha256": sha256(audio).hexdigest(),
            },
        },
    }
    path.write_text(json.dumps(audit), encoding="utf-8")


def test_import_voice_reference_registers_needs_review_once(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _write_manifest(root)
    audio_path = root / "outputs" / "references" / "voice" / "nining-reference.aiff"
    audio = _write_aiff(audio_path, seconds=2.0)
    audit_path = audio_path.with_suffix(".audit.json")
    _write_audit(audit_path, audio=audio, duration_ms=2_000)

    first = import_voice_reference(root=root, storage_root=store_root)
    second = import_voice_reference(root=root, storage_root=store_root)

    assert first["asset"] == second["asset"]
    assert first["decision"] == second["decision"]
    storage = FileStorage(store_root, read_only=True)
    try:
        assets = storage.list_assets()
        decisions = storage.list_decisions()
        calls = storage.list_provider_calls()
    finally:
        storage.close()
    assert len(assets) == 1
    assert len(decisions) == 1
    assert len(calls) == 1
    assert assets[0]["role"] == "reference"
    assert assets[0]["media_type"] == "audio"
    assert assets[0]["provenance"] == "local"
    assert decisions[0]["action"] == "needs_review"
    assert decisions[0]["decided_by"] == "rule"
    assert calls[0]["billing_state"] == "unknown"
    assert calls[0]["cost_amount_micros"] is None


def test_import_voice_reference_rejects_hash_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _write_manifest(root)
    audio_path = root / "outputs" / "references" / "voice" / "nining-reference.aiff"
    audio = _write_aiff(audio_path, seconds=2.0)
    audit_path = audio_path.with_suffix(".audit.json")
    _write_audit(audit_path, audio=audio, duration_ms=2_000)
    audio_path.write_bytes(b"tampered")

    try:
        import_voice_reference(root=root, storage_root=store_root)
    except ValueError as exc:
        assert "hash" in str(exc)
    else:
        raise AssertionError("hash mismatch should fail closed")


def test_import_voice_reference_rejects_over_ten_seconds(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store_root = tmp_path / "store"
    _write_manifest(root)
    audio_path = root / "outputs" / "references" / "voice" / "nining-reference.aiff"
    audio = _write_aiff(audio_path, seconds=10.5)
    audit_path = audio_path.with_suffix(".audit.json")
    _write_audit(audit_path, audio=audio, duration_ms=10_500)

    try:
        import_voice_reference(root=root, storage_root=store_root)
    except ValueError as exc:
        assert "10 seconds" in str(exc)
    else:
        raise AssertionError("long reference voice should fail closed")
