"""Build local human-review dashboards from manifest facts."""

from __future__ import annotations

import argparse
import html
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from .storage import FileStorage
from .storage.protocol import StorageBackend


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path.name} must contain a JSON object")
    return payload


def _known_blockers(manifest: Mapping[str, Any]) -> list[str]:
    values = manifest.get("known_blockers", [])
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, str)]


def _voice_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = manifest.get("candidates")
    if not isinstance(candidates, Mapping):
        return []
    rows: list[dict[str, Any]] = []
    for group, items in candidates.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            rows.append(
                {
                    "group": str(group),
                    "label": str(item.get("tuning_label") or "reference"),
                    "duration_ms": item.get("duration_ms"),
                    "sha256": str(item.get("sha256") or ""),
                    "path": str(item.get("path") or ""),
                }
            )
    return rows


def _normalized_manifest_paths(manifest: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()
    for row in _voice_rows(manifest):
        path = str(row.get("path") or "")
        paths.add(path)
        if path.startswith("outputs/"):
            paths.add(path.removeprefix("outputs/"))
    return paths


def _latest_asset_decisions(
    storage: StorageBackend, playthrough_id: str
) -> dict[str, dict[str, Any]]:
    decisions: dict[str, list[dict[str, Any]]] = {}
    for decision in storage.list_decisions(playthrough_id):
        if decision.get("subject_kind") == "asset":
            decisions.setdefault(str(decision.get("subject_id")), []).append(decision)
    return {
        asset_id: max(
            values,
            key=lambda item: (
                str(item.get("created_at_utc") or ""),
                str(item.get("decision_id") or ""),
            ),
        )
        for asset_id, values in decisions.items()
    }


def collect_review_assets(
    *,
    storage_root: Path,
    playthrough_id: str,
    storage_root_label: str,
    asset_root_label: str,
    manifest_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    storage = FileStorage(storage_root, read_only=True)
    try:
        backend = cast(StorageBackend, storage)
        latest = _latest_asset_decisions(backend, playthrough_id)
        rows: list[dict[str, Any]] = []
        for asset in backend.list_assets(playthrough_id):
            media_type = asset.get("media_type")
            role = asset.get("role")
            if media_type not in {"image", "audio", "video"}:
                continue
            if role not in {"reference", "candidate"}:
                continue
            if asset.get("final_eligible") is not True:
                continue
            asset_id = str(asset.get("asset_id"))
            sha = str(asset.get("sha256") or "")
            relative_path = str(asset.get("relative_path") or "")
            latest_decision = latest.get(asset_id)
            in_manifest = (
                manifest_paths is not None
                and (relative_path in manifest_paths or f"outputs/{relative_path}" in manifest_paths)
            )
            base = (
                "PYTHONPATH=src .venv/bin/python scripts/record_human_review.py . "
                f"--storage-root {storage_root_label} "
                f"--asset-root {asset_root_label} "
                f"--playthrough-id {playthrough_id} "
                f"--asset-id {asset_id}"
            )
            rows.append(
                {
                    "asset_id": asset_id,
                    "role": str(role),
                    "media_type": str(media_type),
                    "trigger_id": asset.get("trigger_id"),
                    "duration_ms": asset.get("duration_ms"),
                    "sha256": sha,
                    "relative_path": relative_path,
                    "manifest_candidate": in_manifest,
                    "latest_decision": (
                        str(latest_decision.get("action"))
                        if latest_decision is not None
                        else "none"
                    ),
                    "accept_dry_run": (
                        f"{base} --action accept --reason '<human reason>' "
                        f"--expected-sha256 {sha}"
                    ),
                    "reject_dry_run": (
                        f"{base} --action reject --reason '<human reason>'"
                    ),
                }
            )
        rows = sorted(
            rows,
            key=lambda row: (
                str(row["role"]),
                str(row.get("trigger_id") or ""),
                not bool(row.get("manifest_candidate")),
                str(row["relative_path"]),
                str(row["asset_id"]),
            ),
        )
        first_by_sha: dict[str, str] = {}
        for row in rows:
            sha = str(row.get("sha256") or "")
            if not sha:
                row["duplicate_note"] = ""
                continue
            first = first_by_sha.get(sha)
            if first is None:
                first_by_sha[sha] = str(row["asset_id"])
                row["duplicate_note"] = ""
            else:
                row["duplicate_note"] = f"same bytes as {first}; do not count as a separate rejected result"
        return rows
    finally:
        storage.close()


def render_markdown(
    *,
    manifest: Mapping[str, Any],
    package_manifest: Mapping[str, Any] | None = None,
    voice_review_url: str = "http://127.0.0.1:59103/",
    review_assets: Sequence[Mapping[str, Any]] = (),
    plan_path_label: str = "<human-review>/voice-decision-plan.template.json",
) -> str:
    package_manifest = package_manifest or {}
    blocker_lines = "\n".join(
        f"- {item}" for item in _known_blockers(manifest)
    ) or "- No known blockers listed."
    rows = _voice_rows(manifest)
    row_lines = "\n".join(
        "| {group} | {label} | {duration} | `{sha}` | `{path}` |".format(
            group=row["group"],
            label=row["label"],
            duration=(
                f"{row['duration_ms']} ms"
                if isinstance(row["duration_ms"], int)
                else "n/a"
            ),
            sha=row["sha256"],
            path=row["path"],
        )
        for row in rows
    )
    if not row_lines:
        row_lines = "| n/a | n/a | n/a | n/a | n/a |"
    review_lines = "\n".join(
        "| {asset_id} | {role} | {media_type} | {trigger_id} | {duration} | `{sha}` | `{path}` | {latest} | {note} |".format(
            asset_id=row.get("asset_id", ""),
            role=row.get("role", ""),
            media_type=row.get("media_type", ""),
            trigger_id=row.get("trigger_id") or "reference",
            duration=(
                f"{row['duration_ms']} ms"
                if isinstance(row.get("duration_ms"), int)
                else "n/a"
            ),
            sha=row.get("sha256", ""),
            path=row.get("relative_path", ""),
            latest=row.get("latest_decision", "none"),
            note=row.get("duplicate_note", ""),
        )
        for row in review_assets
    )
    review_commands = "\n\n".join(
            "### {asset_id}\n\n{note}\n\nAccept dry-run:\n\n```bash\n{accept}\n```\n\nReject dry-run:\n\n```bash\n{reject}\n```".format(
            asset_id=row.get("asset_id", ""),
            note=(
                f"Note: {row.get('duplicate_note')}"
                if row.get("duplicate_note")
                else "No duplicate SHA note."
            ),
            accept=row.get("accept_dry_run", ""),
            reject=row.get("reject_dry_run", ""),
        )
        for row in review_assets
    )
    if not review_lines:
        review_lines = "| n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |"
    if not review_commands:
        review_commands = "No reviewable assets were loaded from storage."
    return f"""# Final Blocker Dashboard

Status: not final. This dashboard is a local coordination aid only; it does not authorize provider calls, accept/reject media, build a final ZIP, or submit anything.

## Strict Final Audit Blockers

{blocker_lines}

## Current Engineering State

- Source commit: `{package_manifest.get("source_commit", "unknown")}`
- Development package: `../../packages/nining-submission-dev-incomplete.zip`
- Development package SHA-256: `{package_manifest.get("zip_sha256", "not built")}`
- Development package `release_ready`: `{package_manifest.get("release_ready", "unknown")}`

## Review Aids

- Voice decision pack: `voice-decision-pack.html` and `voice-decision-pack.md`
- Voice decision plan template: `voice-decision-plan.template.json`
- Visual authorization pack: `visual-authorization-pack.html` and `visual-authorization-pack.md`
- Voice review UI: `{voice_review_url}`

## Voice Candidates

| Group | Label | Duration | SHA-256 | File |
|---|---|---:|---|---|
{row_lines}

## Reviewable Storage Assets

| Asset ID | Role | Media | Trigger | Duration | SHA-256 | Relative path | Latest decision | Duplicate note |
|---|---|---|---|---:|---|---|---|---|
{review_lines}

## Dry-run Review Commands

{review_commands}

## Batch Decision Plan

After replacing every placeholder reason in `voice-decision-plan.template.json`, validate the whole plan at once:

```bash
PYTHONPATH=src .venv/bin/python scripts/record_human_review_plan.py . --plan {plan_path_label} --storage-root work/state/local-voice-files --asset-root outputs --playthrough-id play_df2757fafefc46bb9519d730517d032a
```

Only after the dry-run output matches the human decisions should the same command be rerun with `--write`.

## Next Safe Human Steps

1. Listen to the voice candidates and decide accept/reject with concrete reasons.
2. Use `scripts/record_human_review.py` in dry-run mode for each exact decision.
3. Add `--write` only after confirming the file and SHA.
4. Run `scripts/sync_release_manifest.py ... --dry-run` after decisions exist.
5. Separately authorize exactly one Azure `gpt-image-2` reference call if ready to continue visual media.

## Not Allowed Without Fresh Confirmation

- No formal activation, upload, final submission, or GitHub push.
- No live image call without explicit authorization for the specific one-call reference attempt.
- No automatic retry after timeout, 5xx, malformed response, or unknown billing.
- No hand-editing `outputs/manifest.json` to mark candidates final.
"""


def render_html(
    *,
    manifest: Mapping[str, Any],
    package_manifest: Mapping[str, Any] | None = None,
    voice_review_url: str = "http://127.0.0.1:59103/",
    review_assets: Sequence[Mapping[str, Any]] = (),
    plan_path_label: str = "<human-review>/voice-decision-plan.template.json",
) -> str:
    package_manifest = package_manifest or {}
    blockers = _known_blockers(manifest)
    rows = _voice_rows(manifest)
    blockers_html = "".join(f"<li>{html.escape(item)}</li>" for item in blockers)
    if not blockers_html:
        blockers_html = "<li>No known blockers listed.</li>"
    rows_html = "".join(
        "<tr>{}</tr>".format(
            "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in (
                    row["group"],
                    row["label"],
                    (
                        f"{row['duration_ms']} ms"
                        if isinstance(row["duration_ms"], int)
                        else "n/a"
                    ),
                    row["sha256"],
                    row["path"],
                )
            )
        )
        for row in rows
    )
    if not rows_html:
        rows_html = "<tr><td colspan=\"5\">No voice candidates listed.</td></tr>"
    review_rows_html = "".join(
        "<tr>{}</tr>".format(
            "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in (
                    row.get("asset_id", ""),
                    row.get("role", ""),
                    row.get("media_type", ""),
                    row.get("trigger_id") or "reference",
                    (
                        f"{row['duration_ms']} ms"
                        if isinstance(row.get("duration_ms"), int)
                        else "n/a"
                    ),
                    row.get("sha256", ""),
                    row.get("relative_path", ""),
                    row.get("latest_decision", "none"),
                    row.get("duplicate_note", ""),
                )
            )
        )
        for row in review_assets
    )
    if not review_rows_html:
        review_rows_html = "<tr><td colspan=\"9\">No reviewable assets loaded from storage.</td></tr>"
    command_blocks = "".join(
        "<article><h3>{asset_id}</h3><p>{note}</p><p>Accept dry-run:</p><pre>{accept}</pre><p>Reject dry-run:</p><pre>{reject}</pre></article>".format(
            asset_id=html.escape(str(row.get("asset_id", ""))),
            note=html.escape(
                str(row.get("duplicate_note") or "No duplicate SHA note.")
            ),
            accept=html.escape(str(row.get("accept_dry_run", ""))),
            reject=html.escape(str(row.get("reject_dry_run", ""))),
        )
        for row in review_assets
    )
    if not command_blocks:
        command_blocks = "<p>No reviewable assets were loaded from storage.</p>"
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Final Blocker Dashboard</title>
    <style>
      body {{ margin: 0; background: #f6efe3; color: #172b36; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; }}
      main {{ width: min(1100px, calc(100vw - 32px)); margin: auto; padding: 32px 0 64px; }}
      section {{ margin: 14px 0; padding: 18px; border: 1px solid #dfd0b9; border-radius: 10px; background: #fffaf0; box-shadow: 0 14px 32px rgb(55 36 15 / 9%); }}
      table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
      th, td {{ padding: 8px; border-top: 1px solid #dfd0b9; text-align: left; vertical-align: top; }}
      code, pre {{ overflow-wrap: anywhere; color: #17384a; }}
      pre {{ white-space: pre-wrap; padding: 10px; border: 1px dashed #dfd0b9; border-radius: 8px; background: #fffdf8; }}
      .danger {{ color: #9a3b3b; font-weight: 800; }}
    </style>
  </head>
  <body>
    <main>
      <h1>Final Blocker Dashboard</h1>
      <section><p class="danger">Not final. This page does not authorize provider calls or submission.</p></section>
      <section><h2>Strict Final Audit Blockers</h2><ul>{blockers_html}</ul></section>
      <section>
        <h2>Current Engineering State</h2>
        <p>Source commit: <code>{html.escape(str(package_manifest.get("source_commit", "unknown")))}</code></p>
        <p>Development ZIP SHA-256: <code>{html.escape(str(package_manifest.get("zip_sha256", "not built")))}</code></p>
        <p>release_ready: <code>{html.escape(str(package_manifest.get("release_ready", "unknown")))}</code></p>
      </section>
      <section><h2>Review Aids</h2><ul><li><a href="voice-decision-pack.html">Voice decision pack</a></li><li><a href="voice-decision-plan.template.json">Voice decision plan template</a></li><li><a href="visual-authorization-pack.html">Visual authorization pack</a></li><li>Voice review UI: <code>{html.escape(voice_review_url)}</code></li></ul></section>
      <section><h2>Voice Candidates</h2><table><thead><tr><th>Group</th><th>Label</th><th>Duration</th><th>SHA-256</th><th>File</th></tr></thead><tbody>{rows_html}</tbody></table></section>
      <section><h2>Reviewable Storage Assets</h2><table><thead><tr><th>Asset ID</th><th>Role</th><th>Media</th><th>Trigger</th><th>Duration</th><th>SHA-256</th><th>Relative path</th><th>Latest decision</th><th>Duplicate note</th></tr></thead><tbody>{review_rows_html}</tbody></table></section>
      <section><h2>Dry-run Review Commands</h2>{command_blocks}</section>
      <section><h2>Batch Decision Plan</h2><p>After replacing every placeholder reason in <code>voice-decision-plan.template.json</code>, validate the whole plan at once:</p><pre>PYTHONPATH=src .venv/bin/python scripts/record_human_review_plan.py . --plan {html.escape(plan_path_label)} --storage-root work/state/local-voice-files --asset-root outputs --playthrough-id play_df2757fafefc46bb9519d730517d032a</pre><p>Only after the dry-run output matches the human decisions should the same command be rerun with <code>--write</code>.</p></section>
      <section><h2>Next Safe Human Steps</h2><ol><li>Listen to candidates and choose accept/reject with reasons.</li><li>Dry-run <code>scripts/record_human_review.py</code>.</li><li>Add <code>--write</code> only after confirming file and SHA.</li><li>Manifest sync stays dry-run first.</li><li>Visual reference requires a separate one-call authorization.</li></ol></section>
    </main>
  </body>
</html>
"""


def build_dashboard(
    *,
    root: Path,
    output_dir: Path,
    package_manifest_path: Path | None,
    voice_review_url: str,
    storage_root: Path | None = None,
    playthrough_id: str | None = None,
    storage_root_label: str = "work/state/local-voice-files",
    asset_root_label: str = "outputs",
    plan_path_label: str = "<human-review>/voice-decision-plan.template.json",
) -> dict[str, Path]:
    manifest = _read_json(root / "outputs" / "manifest.json")
    package_manifest = (
        _read_json(package_manifest_path)
        if package_manifest_path is not None and package_manifest_path.is_file()
        else {}
    )
    review_assets = (
        collect_review_assets(
            storage_root=storage_root,
            playthrough_id=playthrough_id,
            storage_root_label=storage_root_label,
            asset_root_label=asset_root_label,
            manifest_paths=_normalized_manifest_paths(manifest),
        )
        if storage_root is not None and playthrough_id is not None
        else []
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = output_dir / "final-blocker-dashboard.md"
    html_path = output_dir / "final-blocker-dashboard.html"
    markdown.write_text(
        render_markdown(
            manifest=manifest,
            package_manifest=package_manifest,
            voice_review_url=voice_review_url,
            review_assets=review_assets,
            plan_path_label=plan_path_label,
        ),
        encoding="utf-8",
    )
    html_path.write_text(
        render_html(
            manifest=manifest,
            package_manifest=package_manifest,
            voice_review_url=voice_review_url,
            review_assets=review_assets,
            plan_path_label=plan_path_label,
        ),
        encoding="utf-8",
    )
    return {"markdown": markdown, "html": html_path}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--package-manifest", type=Path)
    parser.add_argument("--voice-review-url", default="http://127.0.0.1:59103/")
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--playthrough-id")
    parser.add_argument("--asset-root-label", default="outputs")
    parser.add_argument(
        "--plan-label",
        default="<human-review>/voice-decision-plan.template.json",
        help="Display label for the voice decision plan path in generated instructions.",
    )
    args = parser.parse_args(argv)
    storage_root_label = str(args.storage_root) if args.storage_root else "work/state/local-voice-files"
    result = build_dashboard(
        root=args.root.resolve(),
        output_dir=args.output_dir.resolve(),
        package_manifest_path=(
            args.package_manifest.resolve() if args.package_manifest else None
        ),
        voice_review_url=args.voice_review_url,
        storage_root=(
            (args.root.resolve() / args.storage_root).resolve()
            if args.storage_root and not args.storage_root.is_absolute()
            else args.storage_root.resolve()
            if args.storage_root
            else None
        ),
        playthrough_id=args.playthrough_id,
        storage_root_label=storage_root_label,
        asset_root_label=args.asset_root_label,
        plan_path_label=args.plan_label,
    )
    print(
        json.dumps(
            {key: str(path) for key, path in result.items()},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0
