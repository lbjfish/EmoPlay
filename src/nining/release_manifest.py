"""Build release manifests from append-only storage facts."""

from __future__ import annotations

import difflib
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from .assets import verify_local_asset
from .canonical import canonical_json
from .checkpoints import normalize_checkpoint_state
from .storage.common import StorageValidationError, canonical_dumps
from .storage.file import FileStorage

REQUIRED_FINAL_MEDIA = {
    "reference_visual": {"image"},
    "reference_voice": {"audio"},
    "T1": {"image"},
    "T2": {"image", "audio"},
    "T3": {"image"},
    "T4": {"image"},
    "T5": {"image", "audio"},
    "T6": {"image"},
}
REQUIRED_FINAL_GROUPS = tuple(REQUIRED_FINAL_MEDIA)
GROUP_ORDER = {group: index for index, group in enumerate(REQUIRED_FINAL_GROUPS)}
ACCEPTABLE_FINAL_ROLES = {
    "reference_visual": {"reference"},
    "reference_voice": {"reference"},
    "T1": {"candidate", "final"},
    "T2": {"candidate", "final"},
    "T3": {"candidate", "final"},
    "T4": {"candidate", "final"},
    "T5": {"candidate", "final"},
    "T6": {"candidate", "final"},
}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
_PRIVATE_REVIEW_REASON_PREFIXES = (
    "\u7528\u6237\u5df2\u6388\u6743\u540e\u7eed\u5a92\u4f53\u6309\u63a8\u8350\u9ed8\u8ba4\u63a5\u53d7",
    "\u7528\u6237\u6388\u6743\u540e\u7eed\u9ed8\u8ba4\u5904\u7406",
    "\u7528\u6237\u786e\u8ba4",
)


def _public_review_reason(value: Any) -> str:
    text = str(value or "").strip()
    for prefix in _PRIVATE_REVIEW_REASON_PREFIXES:
        if text.startswith(prefix):
            detail = text[len(prefix) :].lstrip(" ;；,:：")
            return f"人工审核：{detail}" if detail else "人工审核完成。"
    return text
REFERENCE_VOICE_MAX_MS = 10_000


@dataclass(frozen=True)
class ManifestBuildResult:
    manifest: dict[str, Any]
    known_blockers: tuple[str, ...]
    changed: bool
    diff: str

    @property
    def release_ready(self) -> bool:
        return not self.known_blockers


@dataclass
class _MergedFacts:
    records: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    record_sources: dict[str, dict[str, set[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set))
    )
    conflicts: list[str] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _identity(collection: str, record: Mapping[str, Any]) -> str:
    if collection == "playthroughs":
        return str(record.get("playthrough_id"))
    if collection == "branches":
        return f"{record.get('playthrough_id')}\0{record.get('branch_id')}"
    if collection == "story_events":
        return str(record.get("story_event_id"))
    if collection == "checkpoints":
        return str(record.get("checkpoint_id"))
    if collection == "feature_selections":
        return str(record.get("selection_id"))
    if collection == "runs":
        return str(record.get("run_id"))
    if collection == "provider_calls":
        return str(record.get("call_id"))
    if collection == "assets":
        return str(record.get("asset_id"))
    if collection == "decisions":
        return str(record.get("decision_id"))
    if collection == "heads":
        return str(record.get("playthrough_id"))
    if collection == "save_slots":
        return f"{record.get('playthrough_id')}\0{record.get('slot_name')}"
    raise ValueError(f"unknown collection: {collection}")


def _stable_record(record: Mapping[str, Any]) -> str:
    return canonical_dumps(record)


def load_file_storage_snapshots(roots: Sequence[str | Path]) -> list[dict[str, Any]]:
    """Open and validate FileStorage roots read-only, then return snapshots."""

    snapshots: list[dict[str, Any]] = []
    for root in roots:
        storage = FileStorage(root, read_only=True)
        try:
            integrity = storage.integrity_check()
            if integrity.get("ok") is not True:
                raise StorageValidationError("FileStorage integrity check failed")
            snapshots.append(storage.export_snapshot())
        finally:
            storage.close()
    return snapshots


def _merge_snapshots(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    store_labels: Sequence[str],
) -> _MergedFacts:
    facts = _MergedFacts()
    for index, snapshot in enumerate(snapshots):
        store_label = store_labels[index]
        collections = snapshot.get("collections")
        if not isinstance(collections, Mapping):
            facts.conflicts.append(f"{store_label}: snapshot collections missing")
            continue
        for collection, rows in collections.items():
            if not isinstance(rows, list):
                facts.conflicts.append(f"{store_label}: {collection} is not a list")
                continue
            for raw in rows:
                if not isinstance(raw, Mapping):
                    facts.conflicts.append(f"{store_label}: {collection} has non-object")
                    continue
                record = dict(raw)
                identity = _identity(str(collection), record)
                existing = facts.records[str(collection)].get(identity)
                if existing is not None and _stable_record(existing) != _stable_record(record):
                    facts.conflicts.append(
                        f"{collection}:{identity}: conflicting records across stores"
                    )
                    continue
                facts.records[str(collection)][identity] = record
                facts.record_sources[str(collection)][identity].add(store_label)
    return facts


def _latest_decisions(decisions: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        if decision.get("subject_kind") == "asset":
            by_asset[str(decision.get("subject_id"))].append(dict(decision))
    latest: dict[str, dict[str, Any]] = {}
    for asset_id, items in by_asset.items():
        latest[asset_id] = max(
            items,
            key=lambda item: (
                str(item.get("created_at_utc") or ""),
                str(item.get("decision_id") or ""),
            ),
        )
    return latest


def _current_accepted_assets(facts: _MergedFacts) -> dict[str, set[str]]:
    accepted: dict[str, set[str]] = {}
    checkpoints = facts.records["checkpoints"]
    for head in facts.records["heads"].values():
        playthrough_id = head.get("playthrough_id")
        checkpoint_id = head.get("checkpoint_id")
        if not isinstance(playthrough_id, str) or not isinstance(checkpoint_id, str):
            continue
        checkpoint = checkpoints.get(checkpoint_id)
        if checkpoint is None:
            continue
        try:
            state = normalize_checkpoint_state(checkpoint.get("state", {}))
        except StorageValidationError:
            continue
        accepted[playthrough_id] = set(state["accepted_assets"])
    return accepted


def _run_for_asset(facts: _MergedFacts, asset: Mapping[str, Any]) -> dict[str, Any] | None:
    run_id = asset.get("run_id")
    return facts.records["runs"].get(str(run_id)) if isinstance(run_id, str) else None


def _provider_call_for_asset(
    facts: _MergedFacts, asset: Mapping[str, Any]
) -> dict[str, Any] | None:
    call_id = asset.get("provider_call_id")
    return (
        facts.records["provider_calls"].get(str(call_id))
        if isinstance(call_id, str)
        else None
    )


def _group_for_asset(asset: Mapping[str, Any]) -> str | None:
    role = asset.get("role")
    media_type = asset.get("media_type")
    trigger_id = asset.get("trigger_id")
    if role == "reference" and media_type == "image":
        return "reference_visual"
    if role == "reference" and media_type == "audio":
        return "reference_voice"
    if trigger_id in {"T1", "T2", "T3", "T4", "T5", "T6"}:
        return str(trigger_id)
    return None


def _manifest_path(root: Path, asset_root: Path, relative_path: str) -> str:
    try:
        output_prefix = asset_root.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        output_prefix = asset_root.name
    return PurePosixPath(output_prefix, relative_path).as_posix()


def _asset_sort_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        GROUP_ORDER.get(str(record.get("group")), 99),
        str(record.get("created_at_utc") or ""),
        str(record.get("asset_id") or ""),
    )


def _final_media_record(
    *,
    root: Path,
    asset_root: Path,
    asset: Mapping[str, Any],
    decision: Mapping[str, Any],
    group: str,
    provider_call: Mapping[str, Any] | None,
) -> dict[str, Any]:
    record = {
        "group": group,
        "asset_id": asset["asset_id"],
        "run_id": asset["run_id"],
        "trigger_id": asset.get("trigger_id"),
        "role": asset.get("role"),
        "media_type": asset.get("media_type"),
        "path": _manifest_path(root, asset_root, str(asset["relative_path"])),
        "mime_type": asset.get("mime_type"),
        "size_bytes": asset.get("size_bytes"),
        "sha256": asset.get("sha256"),
        "duration_ms": asset.get("duration_ms"),
        "width_px": asset.get("width_px"),
        "height_px": asset.get("height_px"),
        "accepted": True,
        "decided_by": "human",
        "decision_id": decision.get("decision_id"),
        "decision_reason": _public_review_reason(decision.get("reason")),
        "created_at_utc": asset.get("created_at_utc"),
        "decision_created_at_utc": decision.get("created_at_utc"),
    }
    if provider_call is not None:
        record.update(
            {
                "provider_call_id": provider_call.get("call_id"),
                "provider": provider_call.get("provider"),
                "model": provider_call.get("model"),
                "billing_state": provider_call.get("billing_state"),
                "cost_amount_micros": provider_call.get("cost_amount_micros"),
                "cost_currency": provider_call.get("cost_currency"),
            }
        )
    return record


def _rejected_record(
    *,
    root: Path,
    asset_root: Path,
    asset: Mapping[str, Any],
    decision: Mapping[str, Any],
    provider_call: Mapping[str, Any] | None,
) -> dict[str, Any]:
    record = {
        "asset_id": asset["asset_id"],
        "run_id": asset["run_id"],
        "trigger_id": asset.get("trigger_id"),
        "role": asset.get("role"),
        "media_type": asset.get("media_type"),
        "path": _manifest_path(root, asset_root, str(asset["relative_path"])),
        "sha256": asset.get("sha256"),
        "duration_ms": asset.get("duration_ms"),
        "action": "reject",
        "decided_by": "human",
        "decision_id": decision.get("decision_id"),
        "reason": _public_review_reason(decision.get("reason")),
        "decision_created_at_utc": decision.get("created_at_utc"),
    }
    if provider_call is not None:
        record.update(
            {
                "provider_call_id": provider_call.get("call_id"),
                "provider": provider_call.get("provider"),
                "model": provider_call.get("model"),
                "billing_state": provider_call.get("billing_state"),
                "cost_amount_micros": provider_call.get("cost_amount_micros"),
                "cost_currency": provider_call.get("cost_currency"),
            }
        )
    return record


def _append_blocker(blockers: list[str], code: str, detail: str) -> None:
    blockers.append(f"{code}: {detail}")


def _blocked_unknown_billing_calls(facts: _MergedFacts) -> list[str]:
    blocked_subjects = {
        str(decision.get("subject_id"))
        for decision in facts.records["decisions"].values()
        if decision.get("action") == "block"
        and decision.get("subject_kind") in {"run", "provider_call"}
    }
    unresolved: list[str] = []
    for call in facts.records["provider_calls"].values():
        if call.get("billing_state") != "unknown" or call.get("status") == "success":
            continue
        call_id = str(call.get("call_id"))
        run_id = str(call.get("run_id"))
        if call_id not in blocked_subjects and run_id not in blocked_subjects:
            unresolved.append(call_id)
    return sorted(unresolved)


def _derive_release_sections(
    *,
    root: Path,
    asset_root: Path,
    facts: _MergedFacts,
    expected_materials_sha256: str | None,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str]]:
    blockers: list[str] = []
    final_media: dict[str, list[dict[str, Any]]] = {group: [] for group in REQUIRED_FINAL_GROUPS}
    rejected_results: list[dict[str, Any]] = []
    latest = _latest_decisions(list(facts.records["decisions"].values()))
    accepted_by_playthrough = _current_accepted_assets(facts)
    release_materials: set[str] = set()
    rejected_keys: set[tuple[str, str]] = set()

    for conflict in sorted(facts.conflicts):
        _append_blocker(blockers, "storage_conflict", conflict)
    for call_id in _blocked_unknown_billing_calls(facts):
        _append_blocker(blockers, "unknown_unreconciled_billing", call_id)

    for run_record in facts.records["runs"].values():
        if run_record.get("status") not in TERMINAL_RUN_STATUSES:
            _append_blocker(
                blockers,
                "unresolved_run",
                f"{run_record.get('run_id')} is {run_record.get('status')}",
            )

    for asset in facts.records["assets"].values():
        asset_id = str(asset.get("asset_id"))
        decision = latest.get(asset_id)
        if decision is None:
            continue
        asset_run = _run_for_asset(facts, asset)
        if asset_run is None:
            _append_blocker(blockers, "asset_missing_run", asset_id)
            continue
        materials_sha = asset_run.get("materials_manifest_sha256")
        if isinstance(materials_sha, str):
            release_materials.add(materials_sha)
        provider_call = _provider_call_for_asset(facts, asset)
        if decision.get("decided_by") != "human":
            continue
        if decision.get("action") not in {"accept", "reject"}:
            continue

        verification = verify_local_asset(asset_root, asset)
        if not verification.ok:
            _append_blocker(
                blockers,
                "asset_tamper",
                f"{asset_id} verification failed: {verification.status}",
            )
            continue

        if decision["action"] == "reject":
            rejected_key = (str(asset.get("sha256")), str(asset.get("media_type")))
            if rejected_key in rejected_keys:
                continue
            rejected_keys.add(rejected_key)
            rejected_results.append(
                _rejected_record(
                    root=root,
                    asset_root=asset_root,
                    asset=asset,
                    decision=decision,
                    provider_call=provider_call,
                )
            )
            continue

        group = _group_for_asset(asset)
        if group is None:
            _append_blocker(blockers, "accepted_asset_group_unknown", asset_id)
            continue
        if asset.get("media_type") not in REQUIRED_FINAL_MEDIA[group]:
            _append_blocker(blockers, "accepted_asset_media_type", asset_id)
            continue
        if asset.get("role") not in ACCEPTABLE_FINAL_ROLES[group]:
            _append_blocker(blockers, "accepted_asset_role", asset_id)
            continue
        if asset.get("final_eligible") is not True:
            _append_blocker(blockers, "accepted_asset_ineligible", asset_id)
            continue
        playthrough_id = str(asset_run.get("playthrough_id"))
        if asset_id not in accepted_by_playthrough.get(playthrough_id, set()):
            _append_blocker(blockers, "accepted_asset_not_in_current_checkpoint", asset_id)
            continue
        if group == "reference_voice" and not isinstance(asset.get("duration_ms"), int):
            _append_blocker(blockers, "reference_voice_duration_missing", asset_id)
            continue
        if group == "reference_voice" and asset["duration_ms"] > REFERENCE_VOICE_MAX_MS:
            _append_blocker(blockers, "reference_voice_duration", asset_id)
            continue
        final_media[group].append(
            _final_media_record(
                root=root,
                asset_root=asset_root,
                asset=asset,
                decision=decision,
                group=group,
                provider_call=provider_call,
            )
        )

    if len(release_materials) > 1:
        _append_blocker(
            blockers,
            "materials_manifest_mismatch",
            f"release decisions span {sorted(release_materials)}",
        )
    if (
        expected_materials_sha256 is not None
        and release_materials
        and release_materials != {expected_materials_sha256}
    ):
        _append_blocker(
            blockers,
            "materials_manifest_mismatch",
            "reviewed assets do not match outputs manifest",
        )

    for group in REQUIRED_FINAL_GROUPS:
        final_media[group] = sorted(final_media[group], key=_asset_sort_key)
        deduplicated: list[dict[str, Any]] = []
        seen_media: set[tuple[str, str]] = set()
        for record in final_media[group]:
            identity = (str(record.get("media_type")), str(record.get("sha256")))
            if identity not in seen_media:
                deduplicated.append(record)
                seen_media.add(identity)
        final_media[group] = deduplicated
        if not final_media[group]:
            _append_blocker(blockers, "missing_final_group", group)
            continue
        for media_type in sorted(REQUIRED_FINAL_MEDIA[group]):
            matching = [
                record
                for record in final_media[group]
                if record.get("media_type") == media_type
            ]
            if not matching:
                _append_blocker(
                    blockers,
                    "missing_final_media",
                    f"{group} requires {media_type}",
                )
            elif group != "reference_visual" and len(matching) != 1:
                _append_blocker(
                    blockers,
                    "multiple_final_group",
                    f"{group} {media_type} has {len(matching)} accepted assets",
                )
    visual_count = len(final_media["reference_visual"])
    if not 1 <= visual_count <= 4:
        _append_blocker(
            blockers,
            "visual_reference_count",
            f"expected 1..4, found {visual_count}",
        )
    rejected_results = sorted(
        rejected_results,
        key=lambda item: (
            str(item.get("decision_created_at_utc") or ""),
            str(item.get("decision_id") or ""),
        ),
    )
    if len(rejected_results) < 3:
        _append_blocker(
            blockers,
            "rejected_results_incomplete",
            f"expected at least 3, found {len(rejected_results)}",
        )

    compact_final_media = {
        group: values for group, values in final_media.items() if values
    }
    return compact_final_media, rejected_results, sorted(set(blockers))


def _read_existing_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"manifest is not an object: {path}")
    return payload


def render_manifest(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"


def diff_manifest(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> str:
    before_text = render_manifest(before or {})
    after_text = render_manifest(after)
    if before_text == after_text:
        return ""
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile="current manifest",
            tofile="derived manifest",
        )
    )


def build_release_manifest(
    *,
    root: str | Path,
    storage_roots: Sequence[str | Path] = (),
    storage_snapshots: Sequence[Mapping[str, Any]] = (),
    asset_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> ManifestBuildResult:
    """Build a manifest from FileStorage roots and optional snapshots.

    ``storage_snapshots`` is intentionally accepted so future storage backends
    can feed the same fail-closed derivation without going through FileStorage.
    """

    root_path = Path(root).expanduser().resolve()
    asset_root_path = (
        Path(asset_root).expanduser().resolve()
        if asset_root is not None
        else root_path / "outputs"
    )
    output_manifest = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path is not None
        else root_path / "outputs" / "manifest.json"
    )
    try:
        asset_root_relative = asset_root_path.relative_to(root_path).as_posix()
        output_manifest.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(
            "asset root and manifest must stay inside the release root"
        ) from exc
    existing = _read_existing_manifest(output_manifest)
    expected_materials = existing.get("materials_manifest_sha256")
    expected_materials_sha256 = (
        expected_materials if isinstance(expected_materials, str) else None
    )
    file_snapshots = load_file_storage_snapshots(storage_roots)
    snapshots = [*file_snapshots, *[dict(item) for item in storage_snapshots]]
    labels = [f"file_store:{index}" for index in range(len(storage_roots))] + [
        f"snapshot:{index}" for index in range(len(storage_snapshots))
    ]
    facts = _merge_snapshots(snapshots, store_labels=labels)
    final_media, rejected_results, blockers = _derive_release_sections(
        root=root_path,
        asset_root=asset_root_path,
        facts=facts,
        expected_materials_sha256=expected_materials_sha256,
    )
    manifest: dict[str, Any] = {
        **existing,
        "schema_version": str(existing.get("schema_version") or "1.0"),
        "final_media": final_media,
        "rejected_results": rejected_results,
        "known_blockers": blockers,
        "release_ready": not blockers,
        "release_manifest_source": {
            "storage_sources": labels,
            "asset_root": asset_root_relative,
            "asset_count": len(facts.records["assets"]),
            "decision_count": len(facts.records["decisions"]),
        },
    }
    manifest.pop("generated_at_utc", None)
    if generated_at_utc is not None:
        manifest["generated_at_utc"] = generated_at_utc
    diff = diff_manifest(existing, manifest)
    return ManifestBuildResult(
        manifest=manifest,
        known_blockers=tuple(blockers),
        changed=bool(diff),
        diff=diff,
    )


def write_manifest_atomic(path: str | Path, manifest: Mapping[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(render_manifest(manifest))
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON file is not an object: {path}")
    return payload


def _safe_relative_to_output(root: Path, asset_root: Path, relative_path: str) -> str:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("reference voice path is unsafe")
    output_prefix = asset_root.relative_to(root).as_posix()
    if relative_path == output_prefix or relative_path.startswith(f"{output_prefix}/"):
        return PurePosixPath(relative_path).relative_to(output_prefix).as_posix()
    return pure.as_posix()


def _aiff_duration_ms(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"FORM" or data[8:12] not in {b"AIFF", b"AIFC"}:
        raise ValueError("reference audio is not an AIFF file")
    offset = 12
    frames: int | None = None
    rate: float | None = None
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "big")
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > len(data):
            raise ValueError("AIFF chunk exceeds file size")
        if chunk_id == b"COMM":
            if chunk_size < 18:
                raise ValueError("AIFF COMM chunk is too short")
            frames = int.from_bytes(data[chunk_start + 2 : chunk_start + 6], "big")
            rate = _extended80_to_float(data[chunk_start + 8 : chunk_start + 18])
            break
        offset = chunk_end + (chunk_size % 2)
    if frames is None or rate is None:
        raise ValueError("AIFF COMM chunk is missing")
    if rate <= 0:
        raise ValueError("AIFF sample rate must be positive")
    return round((frames / rate) * 1000)


def _extended80_to_float(data: bytes) -> float:
    if len(data) != 10:
        raise ValueError("extended float must be 10 bytes")
    first = int.from_bytes(data[:2], "big")
    sign = -1 if first & 0x8000 else 1
    exponent = first & 0x7FFF
    mantissa = int.from_bytes(data[2:], "big")
    if exponent == 0 and mantissa == 0:
        return 0.0
    if exponent == 0x7FFF:
        raise ValueError("AIFF sample rate is not finite")
    return sign * mantissa * (2.0 ** (exponent - 16383 - 63))


def _audit_audio_record(audit: Mapping[str, Any]) -> Mapping[str, Any]:
    result = audit.get("result")
    if not isinstance(result, Mapping):
        raise TypeError("audit result is missing")
    audio = result.get("audio")
    if not isinstance(audio, Mapping):
        raise TypeError("audit result audio is missing")
    return audio


def _audit_duration_ms(audit: Mapping[str, Any]) -> int:
    result = audit.get("result")
    metadata = result.get("metadata") if isinstance(result, Mapping) else None
    duration = metadata.get("duration_ms") if isinstance(metadata, Mapping) else None
    if not isinstance(duration, int):
        raise TypeError("audit duration_ms is missing")
    return duration


def _reference_audit_values(audit: Mapping[str, Any]) -> dict[str, Any]:
    result = audit.get("result")
    request = audit.get("request")
    if not isinstance(result, Mapping):
        raise TypeError("audit result is missing")
    if not isinstance(request, Mapping):
        request = {}
    return {
        "provider": result.get("provider") or "local",
        "model": result.get("model"),
        "request_sha256": result.get("request_sha256")
        or request.get("request_sha256")
        or sha256(canonical_json(audit).encode("utf-8")).hexdigest(),
        "prompt_sha256": result.get("prompt_sha256") or request.get("prompt_sha256"),
        "parameters": {
            "audit_schema_version": audit.get("schema_version"),
            "voice_spec": result.get("voice_spec"),
        },
        "usage": result.get("usage") if isinstance(result.get("usage"), Mapping) else {},
        "billing_state": result.get("billing_state") or audit.get("billing_state") or "unknown",
        "cost_amount_micros": audit.get("cost_amount_micros"),
        "cost_currency": audit.get("cost_currency"),
        "status": result.get("status") or "success",
        "started_at_utc": audit.get("started_at_utc"),
        "finished_at_utc": audit.get("finished_at_utc"),
        "latency_ms": audit.get("latency_ms"),
        "final_eligible": bool(result.get("final_eligible")),
        "mime_type": (
            result.get("metadata", {}).get("mime_type")
            if isinstance(result.get("metadata"), Mapping)
            else "audio/aiff"
        )
        or "audio/aiff",
    }


def _manifest_materials_sha(root: Path) -> str:
    manifest = _read_existing_manifest(root / "outputs" / "manifest.json")
    value = manifest.get("materials_manifest_sha256")
    if isinstance(value, str) and len(value) == 64:
        return value
    raise ValueError("outputs manifest lacks a materials SHA-256")


def _ensure_import_context(
    storage: Any,
    *,
    root: Path,
    started_at_utc: str,
    finished_at_utc: str,
    playthrough_id: str | None = None,
) -> tuple[str, str, str]:
    expected_materials_sha = _manifest_materials_sha(root)
    if playthrough_id is not None:
        playthrough = storage.get_playthrough(playthrough_id)
        head = storage.get_head(playthrough_id)
        if playthrough.get("materials_manifest_sha256") != expected_materials_sha:
            raise ValueError("playthrough materials differ from the release manifest")
        return str(playthrough["playthrough_id"]), str(head["branch_id"]), str(
            playthrough["materials_manifest_sha256"]
        )
    playthroughs = storage.list_playthroughs()
    if playthroughs:
        playthrough = playthroughs[-1]
        head = storage.get_head(str(playthrough["playthrough_id"]))
        if playthrough.get("materials_manifest_sha256") != expected_materials_sha:
            raise ValueError("playthrough materials differ from the release manifest")
        return str(playthrough["playthrough_id"]), str(head["branch_id"]), str(
            playthrough["materials_manifest_sha256"]
        )
    materials_sha = expected_materials_sha
    storage.create_playthrough(
        playthrough_id="play_reference_voice_import",
        title="Reference voice import",
        materials_manifest_sha256=materials_sha,
        created_at_utc=started_at_utc,
        updated_at_utc=finished_at_utc,
    )
    storage.create_branch(
        playthrough_id="play_reference_voice_import",
        branch_id="branch_reference_voice_import_main",
        name="main",
        created_at_utc=started_at_utc,
    )
    event = storage.append_story_event(
        playthrough_id="play_reference_voice_import",
        branch_id="branch_reference_voice_import_main",
        story_event_id="event_reference_voice_import_start",
        event_type="playthrough_started",
        actor="system",
        payload={"title": "Reference voice import"},
        reversible=False,
        created_at_utc=started_at_utc,
    )
    checkpoint = storage.create_checkpoint(
        playthrough_id="play_reference_voice_import",
        branch_id="branch_reference_voice_import_main",
        checkpoint_id="checkpoint_reference_voice_import_start",
        head_event_id=event["story_event_id"],
        checkpoint_kind="manual",
        label="Start",
        state=normalize_checkpoint_state({}),
        created_at_utc=started_at_utc,
    )
    storage.set_head(
        playthrough_id="play_reference_voice_import",
        branch_id="branch_reference_voice_import_main",
        checkpoint_id=checkpoint["checkpoint_id"],
        head_event_id=event["story_event_id"],
        updated_at_utc=finished_at_utc,
    )
    return (
        "play_reference_voice_import",
        "branch_reference_voice_import_main",
        materials_sha,
    )


def import_voice_reference(
    *,
    root: str | Path,
    storage_root: str | Path,
    audio_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    asset_root: str | Path | None = None,
    playthrough_id: str | None = None,
) -> dict[str, Any]:
    """Import an existing AIFF reference and audit into FileStorage.

    This function never generates audio and never records a human decision.
    """

    root_path = Path(root).expanduser().resolve()
    asset_root_path = (
        Path(asset_root).expanduser().resolve()
        if asset_root is not None
        else root_path / "outputs"
    )
    audio = (
        Path(audio_path).expanduser().resolve()
        if audio_path is not None
        else root_path / "outputs" / "references" / "voice" / "nining-reference.aiff"
    )
    audit_file = (
        Path(audit_path).expanduser().resolve()
        if audit_path is not None
        else audio.with_suffix(".audit.json")
    )
    try:
        actual_relative_path = audio.relative_to(asset_root_path).as_posix()
        audit_file.relative_to(asset_root_path)
    except ValueError as exc:
        raise ValueError(
            "reference audio and audit must stay inside the output root"
        ) from exc
    audit = _read_json(audit_file)
    audit_audio = _audit_audio_record(audit)
    actual = audio.read_bytes()
    actual_hash = sha256(actual).hexdigest()
    expected_hash = audit_audio.get("sha256")
    if expected_hash != actual_hash:
        raise ValueError("reference audio hash differs from audit")
    expected_bytes = audit_audio.get("bytes_length")
    if expected_bytes != len(actual):
        raise ValueError("reference audio byte length differs from audit")
    measured_duration_ms = _aiff_duration_ms(audio)
    audit_duration_ms = _audit_duration_ms(audit)
    if abs(measured_duration_ms - audit_duration_ms) > 1:
        raise ValueError("reference audio duration differs from audit")
    if measured_duration_ms > REFERENCE_VOICE_MAX_MS:
        raise ValueError("reference voice exceeds 10 seconds")
    relative_path = _safe_relative_to_output(
        root_path,
        asset_root_path,
        str(audit.get("relative_path") or audio.relative_to(asset_root_path).as_posix()),
    )
    if relative_path != actual_relative_path:
        raise ValueError("reference audit path differs from the audio file")
    values = _reference_audit_values(audit)
    started_at = str(values["started_at_utc"] or utc_now())
    finished_at = str(values["finished_at_utc"] or started_at)
    storage = FileStorage(storage_root)
    try:
        play_id, branch_id, materials_sha = _ensure_import_context(
            storage,
            root=root_path,
            started_at_utc=started_at,
            finished_at_utc=finished_at,
            playthrough_id=playthrough_id,
        )
        suffix = actual_hash[:16]
        run = storage.begin_run(
            run_id=f"run_reference_voice_import_{suffix}",
            playthrough_id=play_id,
            branch_id=branch_id,
            mode="local",
            requested_storage="file",
            effective_storage=storage.backend_name,
            materials_manifest_sha256=materials_sha,
            config_sha256=str(values["request_sha256"]),
            status="completed",
            code_version=None,
            capabilities={"voice": {"state": "local", "final_eligible": values["final_eligible"]}},
            created_at_utc=started_at,
            finished_at_utc=finished_at,
        )
        call = storage.record_provider_call(
            call_id=f"call_reference_voice_import_{suffix}",
            run_id=run["run_id"],
            purpose="import reference voice",
            provider=str(values["provider"]),
            model=values["model"] if values["model"] is None else str(values["model"]),
            mode="local",
            request_sha256=str(values["request_sha256"]),
            prompt_sha256=(
                str(values["prompt_sha256"])
                if values["prompt_sha256"] is not None
                else None
            ),
            parameters=values["parameters"],
            started_at_utc=started_at,
            finished_at_utc=finished_at,
            latency_ms=(
                values["latency_ms"] if isinstance(values["latency_ms"], int) else None
            ),
            usage=values["usage"],
            cost_amount_micros=(
                values["cost_amount_micros"]
                if isinstance(values["cost_amount_micros"], int)
                else None
            ),
            cost_currency=(
                str(values["cost_currency"])
                if values["cost_currency"] is not None
                else None
            ),
            billing_state=str(values["billing_state"]),
            status=str(values["status"]),
        )
        asset = storage.register_asset(
            asset_id=f"asset_reference_voice_{suffix}",
            run_id=run["run_id"],
            provider_call_id=call["call_id"],
            role="reference",
            media_type="audio",
            relative_path=relative_path,
            mime_type=str(values["mime_type"]),
            size_bytes=len(actual),
            duration_ms=measured_duration_ms,
            sha256=actual_hash,
            provenance="local",
            final_eligible=bool(values["final_eligible"]),
            created_at_utc=finished_at,
        )
        decision = storage.record_decision(
            decision_id=f"decision_reference_voice_needs_review_{suffix}",
            run_id=run["run_id"],
            subject_kind="asset",
            subject_id=asset["asset_id"],
            action="needs_review",
            reason="imported reference voice requires explicit human review",
            decided_by="rule",
            created_at_utc=finished_at,
        )
        return {
            "asset": asset,
            "decision": decision,
            "provider_call": call,
            "run": run,
            "duration_ms": measured_duration_ms,
            "needs_review": True,
        }
    finally:
        storage.close()
