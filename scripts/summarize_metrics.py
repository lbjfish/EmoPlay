#!/usr/bin/env python3
"""Derive report metrics from committed provider logs and the output manifest."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

EXPECTED_PRIMARY_MEDIA = {
    "T1": "image",
    "T2": "audio",
    "T3": "image",
    "T4": "image",
    "T5": "audio",
    "T6": "image",
}
BROWSER_AUDIO_MIME_TYPES = {"audio/wav", "audio/mpeg", "audio/ogg", "audio/webm"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return payload


def _manifest_chapter_media(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    final_media = manifest.get("final_media", {})
    if not isinstance(final_media, dict):
        return records
    for trigger_id in EXPECTED_PRIMARY_MEDIA:
        values = final_media.get(trigger_id, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                records.append(
                    {
                        "trigger_id": trigger_id,
                        "media_type": value.get("media_type"),
                        "mime_type": value.get("mime_type"),
                    }
                )
    return records


def _coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    backgrounds = {
        str(item.get("trigger_id"))
        for item in records
        if item.get("media_type") == "image"
    }
    primary = {
        trigger_id
        for trigger_id, media_type in EXPECTED_PRIMARY_MEDIA.items()
        if any(
            item.get("trigger_id") == trigger_id
            and item.get("media_type") == media_type
            for item in records
        )
    }
    playable_voice = {
        str(item.get("trigger_id"))
        for item in records
        if item.get("trigger_id") in {"T2", "T5"}
        and item.get("media_type") == "audio"
        and item.get("mime_type") in BROWSER_AUDIO_MIME_TYPES
    }
    return {
        "chapter_backgrounds": {
            "count": len(backgrounds),
            "total": 6,
            "percent": round(len(backgrounds) * 100 / 6, 1),
        },
        "primary_trigger_media": {
            "count": len(primary),
            "total": 6,
            "percent": round(len(primary) * 100 / 6, 1),
        },
        "browser_playable_voice": {
            "count": len(playable_voice),
            "total": 2,
            "percent": round(len(playable_voice) * 100 / 2, 1),
        },
    }


def _browser_playback_summary(audit: dict[str, Any]) -> dict[str, Any]:
    chapters = audit.get("chapters", {})
    if not isinstance(chapters, dict):
        chapters = {}
    successful = 0
    details: dict[str, Any] = {}
    for trigger_id in ("T2", "T5"):
        chapter = chapters.get(trigger_id, {})
        if not isinstance(chapter, dict):
            chapter = {}
        playback = chapter.get("playback", {})
        playback_ok = isinstance(playback, dict) and playback.get("ok") is True
        duration = chapter.get("audio_duration_seconds")
        error = chapter.get("audio_error")
        ok = playback_ok and isinstance(duration, (int, float)) and error is None
        successful += int(ok)
        details[trigger_id] = {
            "ok": ok,
            "error": error,
            "duration_seconds": duration,
            "playback_current_time_seconds": (
                playback.get("current_time_seconds")
                if isinstance(playback, dict)
                else None
            ),
        }
    return {
        "playable_count": successful,
        "total": 2,
        "percent": round(successful * 100 / 2, 1),
        "chapters": details,
    }


def summarize(root: Path) -> dict[str, Any]:
    calls = _read_jsonl(root / "logs" / "provider_calls.jsonl")
    manifest = _read_json(root / "outputs" / "manifest.json")
    providers: dict[str, dict[str, Any]] = {}
    for provider in sorted({str(item["provider"]) for item in calls}):
        selected = [item for item in calls if item["provider"] == provider]
        latencies = [
            item["latency_ms"]
            for item in selected
            if isinstance(item.get("latency_ms"), int)
        ]
        providers[provider] = {
            "call_count": len(selected),
            "success_count": sum(item.get("status") == "success" for item in selected),
            "median_latency_ms": round(statistics.median(latencies)) if latencies else None,
            "total_cost_amount_micros": sum(
                item.get("cost_amount_micros") or 0 for item in selected
            ),
            "billing_states": sorted(
                {str(item.get("billing_state")) for item in selected}
            ),
        }
    tuning = {
        str(item.get("tuning_label")): item
        for item in manifest.get("candidates", {}).get("T5", [])
        if item.get("tuning_label")
    }
    before = tuning.get("before-rate-180")
    after = tuning.get("after-rate-220")
    comparison = None
    if before is not None and after is not None:
        before_duration = int(before["duration_ms"])
        after_duration = int(after["duration_ms"])
        comparison = {
            "metric": "T5 preset-voice duration",
            "before": {"rate": 180, "duration_ms": before_duration},
            "after": {"rate": 220, "duration_ms": after_duration},
            "reduction_ms": before_duration - after_duration,
            "reduction_percent": round(
                (before_duration - after_duration) * 100 / before_duration, 1
            ),
            "selection_status": (
                "accepted and losslessly rewrapped as browser WAV"
                if any(
                    item.get("media_type") == "audio"
                    and item.get("mime_type") == "audio/wav"
                    for item in manifest.get("final_media", {}).get("T5", [])
                )
                else "not selected"
            ),
        }
    image_calls = [
        item
        for item in calls
        if item.get("model") == "gpt-image-2" and item.get("status") == "success"
    ]
    image_latencies = [
        int(item["latency_ms"])
        for item in image_calls
        if isinstance(item.get("latency_ms"), int)
    ]
    image_metrics = {
        "successful_call_count": len(image_calls),
        "median_latency_ms": (
            round(statistics.median(image_latencies)) if image_latencies else None
        ),
        "mean_latency_ms": (
            round(statistics.mean(image_latencies)) if image_latencies else None
        ),
        "min_latency_ms": min(image_latencies) if image_latencies else None,
        "max_latency_ms": max(image_latencies) if image_latencies else None,
        "total_estimated_cost_amount_micros": sum(
            item.get("cost_amount_micros") or 0 for item in image_calls
        ),
        "total_tokens": sum(
            item.get("usage", {}).get("total_tokens") or 0 for item in image_calls
        ),
    }

    baseline = _read_json(root / "logs" / "product_coverage_baseline.json")
    baseline_records = baseline.get("chapter_media", [])
    if not isinstance(baseline_records, list):
        baseline_records = []
    before_coverage = _coverage(
        [dict(item) for item in baseline_records if isinstance(item, dict)]
    )
    after_coverage = _coverage(_manifest_chapter_media(manifest))
    coverage_comparison = {
        "before_source_commit": baseline.get("source_commit"),
        "before": before_coverage,
        "after": after_coverage,
        "percentage_point_change": {
            key: round(after_coverage[key]["percent"] - before_coverage[key]["percent"], 1)
            for key in before_coverage
        },
    }

    browser_before = _read_json(root / "logs" / "product_browser_audit_before.json")
    browser_after = _read_json(root / "logs" / "product_browser_audit_after.json")
    browser_comparison = {
        "before": _browser_playback_summary(browser_before),
        "after": _browser_playback_summary(browser_after),
    }
    remote_attempts = [
        {
            "run_id": item.get("run_id"),
            "provider": item.get("provider"),
            "purpose": item.get("purpose"),
            "status": item.get("status"),
            "billing_state": item.get("billing_state"),
            "latency_ms": item.get("latency_ms"),
            "cost_amount_micros": item.get("cost_amount_micros"),
            "error_code": item.get("error_code"),
        }
        for item in calls
        if item.get("provider") in {"azure_openai", "deepseek"}
        and (
            item.get("latency_ms") is not None
            or item.get("status") == "success"
        )
    ]
    priced_remote = any(
        item.get("billing_state") in {"actual", "estimated"}
        and item.get("cost_amount_micros") is not None
        for item in calls
        if item.get("provider") in {"azure_openai", "deepseek"}
    )
    notes = [
        "Null cost is unknown; zero billing state is counted as zero.",
    ]
    if remote_attempts:
        notes.append(
            "A remote call was attempted; failed unknown-billing calls remain null and are not retried automatically."
        )
    else:
        notes.append("No remote provider transport call occurred in this evidence set.")
    return {
        "schema_version": "1.0",
        "source_files": [
            "logs/provider_calls.jsonl",
            "logs/product_coverage_baseline.json",
            "logs/product_browser_audit_before.json",
            "logs/product_browser_audit_after.json",
            "outputs/manifest.json",
        ],
        "providers": providers,
        "gpt_image_2": image_metrics,
        "remote_attempts": remote_attempts,
        "tuning_comparison": comparison,
        "product_coverage_comparison": coverage_comparison,
        "browser_audio_comparison": browser_comparison,
        "remote_provider_costs_included": priced_remote,
        "notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    result = summarize(root)
    output = root / "outputs" / "metrics.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
