"""CLI helpers for recording explicit human media decisions."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from .assets import verify_local_asset
from .playthrough import PlaythroughManager
from .review import ReviewService
from .storage import FileStorage, StorageError
from .storage.common import assert_no_secrets
from .storage.protocol import StorageBackend


class HumanReviewCommandError(ValueError):
    """Safe command error for review decision entry."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _asset(storage: StorageBackend, playthrough_id: str, asset_id: str) -> dict[str, Any]:
    for item in storage.list_assets(playthrough_id):
        if item.get("asset_id") == asset_id:
            return item
    raise HumanReviewCommandError(
        "asset_not_found", "asset_id was not found in the selected playthrough"
    )


def _preview(
    *,
    storage: StorageBackend,
    asset_root: Path,
    playthrough_id: str,
    asset_id: str,
    action: str,
    reason: str,
    expected_sha256: str | None,
) -> dict[str, Any]:
    if action not in {"accept", "reject"}:
        raise HumanReviewCommandError(
            "invalid_action", "action must be accept or reject"
        )
    if not reason.strip():
        raise HumanReviewCommandError("missing_reason", "reason is required")
    try:
        assert_no_secrets({"reason": reason})
    except StorageError as exc:
        raise HumanReviewCommandError(
            "unsafe_reason", "reason appears to contain a secret-bearing field"
        ) from exc
    asset = _asset(storage, playthrough_id, asset_id)
    integrity = storage.integrity_check()
    if integrity.get("ok") is not True:
        raise HumanReviewCommandError(
            "storage_integrity_failed", "storage integrity check failed"
        )
    head = storage.get_head(playthrough_id)
    run = storage.get_run(str(asset["run_id"]))
    if run.get("branch_id") != head.get("branch_id"):
        raise HumanReviewCommandError(
            "asset_not_on_current_branch",
            "asset run is not on the current playthrough branch",
        )
    verification = verify_local_asset(asset_root, asset)
    if not verification.ok:
        raise HumanReviewCommandError(
            "asset_verification_failed",
            f"asset verification failed: {verification.status}",
        )
    asset_sha = asset.get("sha256")
    if action == "accept" and expected_sha256 != asset_sha:
        raise HumanReviewCommandError(
            "hash_mismatch",
            "accept requires expected_sha256 to match the current asset SHA-256",
        )
    if action == "accept":
        if asset.get("media_type") not in {"image", "audio", "video"}:
            raise HumanReviewCommandError(
                "not_media", "only image, audio, and video assets can be accepted"
            )
        if asset.get("role") in {"placeholder", "draft", "rejected"}:
            raise HumanReviewCommandError(
                "role_not_acceptable", "this asset role cannot be accepted"
            )
        if asset.get("final_eligible") is not True:
            raise HumanReviewCommandError(
                "not_final_eligible", "asset is not technically final eligible"
            )
    return {
        "status": "dry_run",
        "would_write": False,
        "playthrough_id": playthrough_id,
        "asset": {
            "asset_id": asset_id,
            "run_id": asset.get("run_id"),
            "trigger_id": asset.get("trigger_id"),
            "role": asset.get("role"),
            "media_type": asset.get("media_type"),
            "relative_path": asset.get("relative_path"),
            "sha256": asset_sha,
            "duration_ms": asset.get("duration_ms"),
            "final_eligible": asset.get("final_eligible"),
        },
        "verification": verification.to_public_dict(),
        "head": {
            "branch_id": head.get("branch_id"),
            "checkpoint_id": head.get("checkpoint_id"),
        },
        "decision": {
            "action": action,
            "reason": reason.strip(),
            "decided_by": "human",
            "expected_sha256": expected_sha256,
        },
    }


def record_human_review(
    *,
    storage_root: Path,
    asset_root: Path,
    playthrough_id: str,
    asset_id: str,
    action: str,
    reason: str,
    expected_sha256: str | None,
    write: bool,
) -> dict[str, Any]:
    marker = storage_root / "format.json"
    if not storage_root.is_dir() or not marker.is_file():
        raise HumanReviewCommandError(
            "storage_missing",
            "storage_root must point to an existing FileStorage root",
        )
    storage = FileStorage(storage_root, read_only=not write)
    backend = cast(StorageBackend, storage)
    try:
        preview = _preview(
            storage=backend,
            asset_root=asset_root,
            playthrough_id=playthrough_id,
            asset_id=asset_id,
            action=action,
            reason=reason,
            expected_sha256=expected_sha256,
        )
        if not write:
            return preview
        manager = PlaythroughManager(backend)
        review = ReviewService(storage=backend, manager=manager, asset_root=asset_root)
        result = review.review(
            playthrough_id,
            asset_id=asset_id,
            action=action,
            reason=reason,
            expected_sha256=expected_sha256,
        )
        return {
            "status": "written",
            "would_write": True,
            "playthrough_id": playthrough_id,
            "decision": result["decision"],
            "checkpoint": {
                "checkpoint_id": result["checkpoint"].get("checkpoint_id"),
                "state_sha256": result["checkpoint"].get("state_sha256"),
            },
            "head": result["head"],
            "asset": preview["asset"],
        }
    finally:
        storage.close()


def _safe_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or record one explicit human media review decision."
    )
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--storage-root", required=True, type=Path)
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument("--playthrough-id", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--action", choices=("accept", "reject"), required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually append the human decision. Without this flag the command only validates.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        result = record_human_review(
            storage_root=(root / args.storage_root).resolve()
            if not args.storage_root.is_absolute()
            else args.storage_root.resolve(),
            asset_root=(root / (args.asset_root or Path("outputs"))).resolve()
            if args.asset_root is None or not args.asset_root.is_absolute()
            else args.asset_root.resolve(),
            playthrough_id=args.playthrough_id,
            asset_id=args.asset_id,
            action=args.action,
            reason=args.reason,
            expected_sha256=args.expected_sha256,
            write=args.write,
        )
    except HumanReviewCommandError as exc:
        print(
            _safe_json(
                {
                    "status": "blocked",
                    "error_code": exc.code,
                    "message": exc.message,
                }
            )
        )
        return 2
    except (OSError, StorageError, TypeError, ValueError):
        print(
            _safe_json(
                {
                    "status": "blocked",
                    "error_code": "human_review_failed",
                    "message": "human review evidence failed validation",
                }
            )
        )
        return 2
    print(_safe_json(result))
    return 0
