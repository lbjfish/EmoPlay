"""Read-only final-media catalog for clean installations without review storage."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .assets import verify_local_asset

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPES = frozenset({"image", "audio", "video"})
_FINAL_ROLES = frozenset({"reference", "candidate", "final"})


def _relative_media_path(output_root: Path, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("release media path is missing")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("release media path is unsafe")
    parts = path.parts
    if parts and parts[0] == output_root.name:
        parts = parts[1:]
    if not parts:
        raise ValueError("release media path is empty")
    return PurePosixPath(*parts).as_posix()


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def load_release_media_catalog(output_root: str | Path) -> tuple[dict[str, Any], ...]:
    """Return verified accepted media from ``outputs/manifest.json``.

    Missing or incomplete manifests simply provide no catalog. A manifest that
    declares itself release-ready is fail-closed: malformed or tampered final
    media prevents the session from presenting a false final asset.
    """

    root = Path(output_root).expanduser().resolve()
    manifest_path = root / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
    except json.JSONDecodeError as exc:
        raise ValueError("release manifest is invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise TypeError("release manifest is not an object")
    if payload.get("release_ready") is not True:
        return ()
    groups = payload.get("final_media")
    if not isinstance(groups, Mapping):
        raise TypeError("release manifest final_media is missing")

    records: list[dict[str, Any]] = []
    asset_ids: set[str] = set()
    for values in groups.values():
        if not isinstance(values, list):
            raise TypeError("release media group is not a list")
        for raw in values:
            if not isinstance(raw, Mapping):
                raise TypeError("release media record is not an object")
            asset_id = raw.get("asset_id")
            media_type = raw.get("media_type")
            digest = raw.get("sha256")
            trigger_id = raw.get("trigger_id")
            role = raw.get("role")
            size_bytes = raw.get("size_bytes")
            mime_type = raw.get("mime_type")
            if not isinstance(asset_id, str) or not _IDENTIFIER.fullmatch(asset_id):
                raise ValueError("release media asset ID is invalid")
            if asset_id in asset_ids:
                raise ValueError("release media asset ID is duplicated")
            if media_type not in _MEDIA_TYPES:
                raise ValueError("release media type is invalid")
            if role not in _FINAL_ROLES:
                raise ValueError("release media role is invalid")
            if not isinstance(size_bytes, int) or size_bytes < 0:
                raise ValueError("release media size is invalid")
            if (
                not isinstance(mime_type, str)
                or not mime_type.startswith(f"{media_type}/")
            ):
                raise ValueError("release media MIME type is invalid")
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise ValueError("release media SHA-256 is invalid")
            if trigger_id is not None and (
                not isinstance(trigger_id, str)
                or not _IDENTIFIER.fullmatch(trigger_id)
            ):
                raise ValueError("release media trigger ID is invalid")
            if raw.get("accepted") is not True or raw.get("decided_by") != "human":
                raise ValueError("release media lacks a human acceptance fact")
            record: dict[str, Any] = {
                "asset_id": asset_id,
                "run_id": raw.get("run_id"),
                "trigger_id": trigger_id,
                "provider_call_id": raw.get("provider_call_id"),
                "role": role,
                "media_type": media_type,
                "relative_path": _relative_media_path(root, raw.get("path")),
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "width_px": _optional_int(raw.get("width_px")),
                "height_px": _optional_int(raw.get("height_px")),
                "duration_ms": _optional_int(raw.get("duration_ms")),
                "sha256": digest,
                "provenance": raw.get("provider")
                if isinstance(raw.get("provider"), str)
                else "release_manifest",
                "final_eligible": True,
                "created_at_utc": raw.get("created_at_utc")
                if isinstance(raw.get("created_at_utc"), str)
                else None,
                "review_action": "accept",
                "review_reason": raw.get("decision_reason")
                if isinstance(raw.get("decision_reason"), str)
                else "Accepted final media from the release manifest.",
                "catalog_source": "release_manifest",
            }
            verification = verify_local_asset(root, record)
            if not verification.ok:
                raise ValueError(
                    f"release media verification failed: {verification.status}"
                )
            records.append(record)
            asset_ids.add(asset_id)
    return tuple(records)
