"""Validate or apply a batch of explicit human media review decisions."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from .human_review import HumanReviewCommandError, record_human_review
from .storage import StorageError


class HumanReviewPlanError(ValueError):
    """Safe batch-plan validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _read_plan(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise HumanReviewPlanError("invalid_plan", "plan must be a JSON object")
    return payload, sha256(raw).hexdigest()


def _decisions(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = plan.get("decisions")
    if not isinstance(value, list) or not value:
        raise HumanReviewPlanError(
            "missing_decisions", "plan.decisions must be a non-empty array"
        )
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise HumanReviewPlanError(
                "invalid_decision", f"decision {index + 1} must be an object"
            )
        asset_id = item.get("asset_id")
        action = item.get("action")
        reason = item.get("reason")
        expected_sha256 = item.get("expected_sha256")
        if not isinstance(asset_id, str) or not asset_id:
            raise HumanReviewPlanError(
                "invalid_asset_id", f"decision {index + 1} requires asset_id"
            )
        if asset_id in seen:
            raise HumanReviewPlanError(
                "duplicate_asset", f"asset appears more than once: {asset_id}"
            )
        seen.add(asset_id)
        if action not in {"accept", "reject"}:
            raise HumanReviewPlanError(
                "invalid_action", f"decision {index + 1} action must be accept or reject"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise HumanReviewPlanError(
                "missing_reason", f"decision {index + 1} requires a reason"
            )
        if "<" in reason or ">" in reason:
            raise HumanReviewPlanError(
                "placeholder_reason",
                f"decision {index + 1} still contains a placeholder reason",
            )
        if expected_sha256 is not None and not isinstance(expected_sha256, str):
            raise HumanReviewPlanError(
                "invalid_expected_sha256",
                f"decision {index + 1} expected_sha256 must be a string",
            )
        decisions.append(
            {
                "asset_id": asset_id,
                "action": action,
                "reason": reason,
                "expected_sha256": expected_sha256,
            }
        )
    return decisions


def _preflight(
    *,
    decisions: Sequence[Mapping[str, Any]],
    storage_root: Path,
    asset_root: Path,
    playthrough_id: str,
) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for decision in decisions:
        previews.append(
            record_human_review(
                storage_root=storage_root,
                asset_root=asset_root,
                playthrough_id=playthrough_id,
                asset_id=str(decision["asset_id"]),
                action=str(decision["action"]),
                reason=str(decision["reason"]),
                expected_sha256=(
                    str(decision["expected_sha256"])
                    if decision.get("expected_sha256") is not None
                    else None
                ),
                write=False,
            )
        )
    return previews


def _summary(
    *,
    plan_sha256: str,
    playthrough_id: str,
    previews: Sequence[Mapping[str, Any]],
    written: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    reject_shas: set[str] = set()
    duplicate_reject_shas: set[str] = set()
    accept_count = 0
    reject_count = 0
    for preview in previews:
        decision = preview.get("decision")
        asset = preview.get("asset")
        if not isinstance(decision, Mapping) or not isinstance(asset, Mapping):
            continue
        action = decision.get("action")
        if action == "accept":
            accept_count += 1
        if action == "reject":
            reject_count += 1
            asset_sha = asset.get("sha256")
            if isinstance(asset_sha, str):
                if asset_sha in reject_shas:
                    duplicate_reject_shas.add(asset_sha)
                reject_shas.add(asset_sha)
    return {
        "plan_sha256": plan_sha256,
        "playthrough_id": playthrough_id,
        "status": "written" if written else "dry_run",
        "would_write": not bool(written),
        "decision_count": len(previews),
        "accept_count": accept_count,
        "reject_count": reject_count,
        "distinct_rejected_sha_count": len(reject_shas),
        "duplicate_reject_sha256": sorted(duplicate_reject_shas),
        "previews": list(previews),
        "written": list(written),
    }


def record_human_review_plan(
    *,
    plan_path: Path,
    storage_root: Path,
    asset_root: Path,
    playthrough_id: str,
    write: bool,
) -> dict[str, Any]:
    plan, plan_sha = _read_plan(plan_path)
    decisions = _decisions(plan)
    previews = _preflight(
        decisions=decisions,
        storage_root=storage_root,
        asset_root=asset_root,
        playthrough_id=playthrough_id,
    )
    if not write:
        return _summary(
            plan_sha256=plan_sha,
            playthrough_id=playthrough_id,
            previews=previews,
        )
    written: list[dict[str, Any]] = []
    for decision in decisions:
        written.append(
            record_human_review(
                storage_root=storage_root,
                asset_root=asset_root,
                playthrough_id=playthrough_id,
                asset_id=str(decision["asset_id"]),
                action=str(decision["action"]),
                reason=str(decision["reason"]),
                expected_sha256=(
                    str(decision["expected_sha256"])
                    if decision.get("expected_sha256") is not None
                    else None
                ),
                write=True,
            )
        )
    return _summary(
        plan_sha256=plan_sha,
        playthrough_id=playthrough_id,
        previews=previews,
        written=written,
    )


def _safe_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or record a batch of explicit human media review decisions."
    )
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--storage-root", required=True, type=Path)
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument("--playthrough-id", required=True)
    parser.add_argument("--write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        result = record_human_review_plan(
            plan_path=(root / args.plan).resolve()
            if not args.plan.is_absolute()
            else args.plan.resolve(),
            storage_root=(root / args.storage_root).resolve()
            if not args.storage_root.is_absolute()
            else args.storage_root.resolve(),
            asset_root=(root / (args.asset_root or Path("outputs"))).resolve()
            if args.asset_root is None or not args.asset_root.is_absolute()
            else args.asset_root.resolve(),
            playthrough_id=args.playthrough_id,
            write=args.write,
        )
    except (HumanReviewPlanError, HumanReviewCommandError) as exc:
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
    except (OSError, StorageError, TypeError, ValueError, json.JSONDecodeError):
        print(
            _safe_json(
                {
                    "status": "blocked",
                    "error_code": "human_review_plan_failed",
                    "message": "human review plan failed validation",
                }
            )
        )
        return 2
    print(_safe_json(result))
    return 0
