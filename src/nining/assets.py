"""Structured verification for registered local assets."""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AssetVerification:
    status: str
    path: Path | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


def verify_local_asset(
    asset_root: str | Path, asset: Mapping[str, Any]
) -> AssetVerification:
    root = Path(asset_root).expanduser().resolve()
    relative = asset.get("relative_path")
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return AssetVerification("unsafe_path")
    unresolved = root / relative
    if unresolved.is_symlink():
        return AssetVerification("unsafe_path")
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return AssetVerification("unsafe_path")
    if not candidate.is_file():
        return AssetVerification("missing")
    actual_size = candidate.stat().st_size
    expected_size = asset.get("size_bytes")
    if not isinstance(expected_size, int) or actual_size != expected_size:
        return AssetVerification("size_mismatch", size_bytes=actual_size)
    expected_hash = asset.get("sha256")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
        return AssetVerification("invalid_record", size_bytes=actual_size)
    digest = sha256()
    with candidate.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    actual_hash = digest.hexdigest()
    if actual_hash != expected_hash:
        return AssetVerification(
            "hash_mismatch", size_bytes=actual_size, sha256=actual_hash
        )
    content_type = asset.get("mime_type")
    if not isinstance(content_type, str) or "/" not in content_type:
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return AssetVerification(
        "ok",
        path=candidate,
        content_type=content_type,
        size_bytes=actual_size,
        sha256=actual_hash,
    )
