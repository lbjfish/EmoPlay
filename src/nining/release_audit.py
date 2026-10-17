"""Read-only release audit for repository trees and optional ZIP archives."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import zipfile
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath

from .canonical import canonical_sha256
from .loader import MaterialError, load_materials
from .release_smoke import OfflineAppSmokeResult, run_offline_app_smoke
from .spec_builder import build_all_scene_specs

IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    ".vite",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "work",
}
TEXT_SUFFIXES = {
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_SCANNED_TEXT_BYTES = 2_000_000
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_FILE_BYTES = 250_000_000
MAX_ZIP_TOTAL_BYTES = 1_000_000_000
MAX_ZIP_COMPRESSION_RATIO = 250
REQUIRED_FILES = {
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    "config.example.toml",
    "config.offline.toml",
    "outputs/manifest.json",
    "outputs/metrics.json",
    "docs/submission/main_report.md",
    "docs/submission/sales_appendix.md",
    "docs/submission/human_review_checklist.md",
    "output/pdf/nining-main-report.pdf",
    "output/pdf/manifest.json",
}
SECRET_PATTERNS = {
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "bearer_value": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}"),
    "assigned_secret": re.compile(
        r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*[\"']?(?P<value>[A-Za-z0-9._~+/-]{12,})"
    ),
    "private_azure_host": re.compile(r"[A-Za-z0-9-]+\.services\.ai\.azure\.com"),
}
ABSOLUTE_PATH_PATTERNS = {
    "mac_home": re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    "linux_home": re.compile(r"/home/[A-Za-z0-9._-]+/"),
    "windows_home": re.compile(r"(?i)[A-Z]:\\Users\\[^\\]+\\"),
}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    detail: str


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if any(part in IGNORED_PARTS for part in path.relative_to(root).parts):
            continue
        if path.is_file() or path.is_symlink():
            yield path


def tracked_files(root: Path) -> set[str]:
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=root,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return {path.relative_to(root).as_posix() for path in iter_files(root)}
    return {item.decode("utf-8") for item in output.split(b"\0") if item}


def _smoke_findings(result: OfflineAppSmokeResult) -> list[Finding]:
    if result.ok:
        return []
    payload = result.to_public_dict()
    return [
        Finding(
            "blocker",
            "offline_app_smoke",
            "application",
            str(payload["detail"]),
        )
    ]


def audit_tree(
    root: Path,
    *,
    require_complete: bool = False,
    run_smoke: bool | None = None,
) -> list[Finding]:
    if run_smoke is None:
        run_smoke = require_complete
    findings: list[Finding] = []
    available = tracked_files(root)
    for required in sorted(REQUIRED_FILES - available):
        findings.append(Finding("blocker", "missing_required", required, "required release file is absent"))

    for path in iter_files(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(Finding("blocker", "symlink", relative, "symlinks are not allowed in release"))
            continue
        if relative.endswith(("-wal", "-shm")):
            findings.append(Finding("blocker", "sqlite_sidecar", relative, "checkpoint and close SQLite before release"))
        if path.name in {".env", "secrets.toml", "config.local.toml"}:
            findings.append(Finding("blocker", "secret_file", relative, "local credential file is forbidden"))
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > MAX_SCANNED_TEXT_BYTES:
            findings.append(
                Finding(
                    "blocker",
                    "text_too_large_unscanned",
                    relative,
                    "text file exceeds the bounded release scanner limit",
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            match = pattern.search(text)
            if name == "assigned_secret" and match:
                value = match.groupdict().get("value", "")
                if value.startswith(("TEST_ONLY_", "self.", "os.", "env.")):
                    match = None
            if match:
                findings.append(Finding("blocker", name, relative, "sensitive value or private endpoint pattern detected"))
        for name, pattern in ABSOLUTE_PATH_PATTERNS.items():
            if pattern.search(text):
                findings.append(Finding("blocker", name, relative, "absolute user path detected"))

    specs = root / "outputs" / "specs"
    expected = {f"T{index}.json" for index in range(1, 7)}
    actual = {path.name for path in specs.glob("*.json")} if specs.is_dir() else set()
    if actual != expected:
        findings.append(
            Finding("blocker", "spec_set", "outputs/specs", f"expected {sorted(expected)}, found {sorted(actual)}")
        )
    loaded_specs: dict[str, dict[str, object]] = {}
    for name in sorted(actual):
        try:
            payload = json.loads((specs / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(Finding("blocker", "invalid_spec", f"outputs/specs/{name}", type(exc).__name__))
            continue
        if not isinstance(payload, dict):
            findings.append(Finding("blocker", "invalid_spec", f"outputs/specs/{name}", "spec must be an object"))
            continue
        loaded_specs[name.removesuffix(".json")] = payload
        if payload.get("trigger_id") != name.removesuffix(".json"):
            findings.append(Finding("blocker", "spec_trigger_mismatch", f"outputs/specs/{name}", "filename and trigger_id differ"))
        claimed = payload.get("spec_sha256")
        computed = canonical_sha256(
            {key: value for key, value in payload.items() if key != "spec_sha256"}
        )
        if claimed != computed:
            findings.append(
                Finding(
                    "blocker",
                    "spec_hash_mismatch",
                    f"outputs/specs/{name}",
                    "spec_sha256 does not match canonical content",
                )
            )

    manifest_path = root / "outputs" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(Finding("blocker", "invalid_manifest", "outputs/manifest.json", type(exc).__name__))
        manifest = None
    if isinstance(manifest, dict):
        expected_hashes = {
            trigger_id: payload.get("spec_sha256")
            for trigger_id, payload in sorted(loaded_specs.items())
        }
        if manifest.get("spec_sha256") != expected_hashes:
            findings.append(
                Finding(
                    "blocker",
                    "manifest_spec_hashes",
                    "outputs/manifest.json",
                    "manifest spec hashes differ from the committed specs",
                )
            )
        if manifest.get("spec_count") != len(loaded_specs):
            findings.append(Finding("blocker", "manifest_spec_count", "outputs/manifest.json", "spec_count is stale"))
        if manifest.get("triggers") != sorted(loaded_specs):
            findings.append(Finding("blocker", "manifest_triggers", "outputs/manifest.json", "trigger list is stale"))
        for section in ("candidates", "final_media"):
            grouped = manifest.get(section, {})
            if not isinstance(grouped, dict):
                findings.append(Finding("blocker", "manifest_media", "outputs/manifest.json", f"{section} must be an object"))
                continue
            for group, values in grouped.items():
                if not isinstance(group, str) or not isinstance(values, list):
                    findings.append(Finding("blocker", "manifest_media", "outputs/manifest.json", f"invalid {section} group"))
                    continue
                for record in values:
                    if not isinstance(record, dict):
                        findings.append(Finding("blocker", "manifest_media", "outputs/manifest.json", f"invalid {section} record"))
                        continue
                    relative = record.get("path")
                    expected_digest = record.get("sha256")
                    if (
                        not isinstance(relative, str)
                        or not relative.startswith("outputs/")
                        or ".." in PurePosixPath(relative).parts
                        or not isinstance(expected_digest, str)
                    ):
                        findings.append(Finding("blocker", "manifest_media", "outputs/manifest.json", f"unsafe {section} path or hash"))
                        continue
                    media_path = root / relative
                    if not media_path.is_file():
                        findings.append(Finding("blocker", "manifest_media_missing", relative, "manifest media is missing"))
                        continue
                    actual_digest = sha256(media_path.read_bytes()).hexdigest()
                    if actual_digest != expected_digest:
                        findings.append(Finding("blocker", "manifest_media_hash", relative, "manifest media hash differs"))
                    if section == "final_media" and (
                        record.get("accepted") is not True
                        or record.get("decided_by") != "human"
                        or not isinstance(record.get("decision_id"), str)
                    ):
                        findings.append(Finding("blocker", "manifest_final_review", relative, "final media lacks a human accept decision"))
        if require_complete:
            blockers = manifest.get("known_blockers")
            if not isinstance(blockers, list) or blockers:
                findings.append(
                    Finding(
                        "blocker",
                        "known_blockers",
                        "outputs/manifest.json",
                        "final release requires an explicit empty known_blockers list",
                    )
                )
            final_media = manifest.get("final_media")
            required_final = {
                "reference_visual",
                "reference_voice",
                "T1",
                "T3",
                "T5",
            }
            present_final = (
                {
                    key
                    for key, values in final_media.items()
                    if isinstance(values, list) and values
                }
                if isinstance(final_media, dict)
                else set()
            )
            missing_final = sorted(required_final - present_final)
            if missing_final:
                findings.append(
                    Finding(
                        "blocker",
                        "final_media_incomplete",
                        "outputs/manifest.json",
                        f"missing human-accepted groups: {missing_final}",
                    )
                )
            visual_values = (
                final_media.get("reference_visual", [])
                if isinstance(final_media, dict)
                else []
            )
            if not isinstance(visual_values, list) or not 1 <= len(visual_values) <= 4:
                findings.append(
                    Finding(
                        "blocker",
                        "visual_reference_count",
                        "outputs/manifest.json",
                        "final release requires one to four visual references",
                    )
                )
            rejected = manifest.get("rejected_results")
            if not isinstance(rejected, list) or len(rejected) < 3:
                findings.append(
                    Finding(
                        "blocker",
                        "rejected_results_incomplete",
                        "outputs/manifest.json",
                        "final release requires at least three human-rejected results",
                    )
                )
            else:
                for record in rejected:
                    if not isinstance(record, dict) or (
                        record.get("action") != "reject"
                        or record.get("decided_by") != "human"
                        or not isinstance(record.get("reason"), str)
                        or not record.get("reason", "").strip()
                    ):
                        findings.append(
                            Finding(
                                "blocker",
                                "rejected_result_review",
                                "outputs/manifest.json",
                                "every rejected result requires a human decision and reason",
                            )
                        )
                        break
    elif manifest is not None:
        findings.append(Finding("blocker", "invalid_manifest", "outputs/manifest.json", "manifest must be an object"))

    sample_materials = root / "materials" / "sample"
    if sample_materials.is_dir():
        try:
            materials = load_materials(sample_materials)
            rebuilt = {
                str(spec.to_dict()["trigger_id"]): spec.to_dict()
                for spec in build_all_scene_specs(materials)
            }
        except (MaterialError, KeyError, TypeError, ValueError) as exc:
            findings.append(
                Finding(
                    "blocker",
                    "offline_rebuild_failed",
                    "materials/sample",
                    type(exc).__name__,
                )
            )
        else:
            if rebuilt != loaded_specs:
                findings.append(
                    Finding(
                        "blocker",
                        "offline_spec_drift",
                        "outputs/specs",
                        "committed specs differ from a current-code offline rebuild",
                    )
                )
            if isinstance(manifest, dict) and manifest.get(
                "materials_manifest_sha256"
            ) != materials.manifest.get("sha256"):
                findings.append(
                    Finding(
                        "blocker",
                        "materials_manifest_drift",
                        "outputs/manifest.json",
                        "materials manifest hash is absent or stale",
                    )
                )
    report_manifest_path = root / "output" / "pdf" / "manifest.json"
    try:
        report_manifest = json.loads(report_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(Finding("blocker", "invalid_report_manifest", "output/pdf/manifest.json", type(exc).__name__))
    else:
        reports = report_manifest.get("reports") if isinstance(report_manifest, dict) else None
        if not isinstance(reports, dict):
            findings.append(Finding("blocker", "invalid_report_manifest", "output/pdf/manifest.json", "reports must be an object"))
        else:
            for name, record in reports.items():
                if not isinstance(name, str) or not isinstance(record, dict):
                    findings.append(Finding("blocker", "invalid_report_manifest", "output/pdf/manifest.json", "invalid report entry"))
                    continue
                pdf_path = root / "output" / "pdf" / name
                if not pdf_path.is_file():
                    findings.append(Finding("blocker", "missing_report", f"output/pdf/{name}", "report PDF is missing"))
                    continue
                data = pdf_path.read_bytes()
                actual_pages = len(re.findall(rb"/Type\s*/Page(?!s)", data))
                if sha256(data).hexdigest() != record.get("sha256"):
                    findings.append(Finding("blocker", "report_hash", f"output/pdf/{name}", "report hash differs"))
                if actual_pages != record.get("pages"):
                    findings.append(Finding("blocker", "report_page_count", f"output/pdf/{name}", "recorded page count differs"))
                limit = record.get("page_limit")
                if not isinstance(limit, int) or actual_pages > limit:
                    findings.append(Finding("blocker", "report_page_limit", f"output/pdf/{name}", "report exceeds its page limit"))
    if run_smoke:
        findings.extend(_smoke_findings(run_offline_app_smoke(root)))
    return findings


def audit_zip(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                findings.append(Finding("blocker", "zip_entry_limit", path.name, "archive has too many entries"))
            total_size = 0
            normalized_names: set[str] = set()
            encrypted = False
            for info in infos:
                pure = PurePosixPath(info.filename)
                portable_name = info.filename.replace("\\", "/")
                portable = PurePosixPath(portable_name)
                windows_absolute = bool(
                    re.match(r"(?i)^[a-z]:/", portable_name)
                    or portable_name.startswith("//")
                )
                if (
                    pure.is_absolute()
                    or portable.is_absolute()
                    or windows_absolute
                    or ".." in portable.parts
                ):
                    findings.append(Finding("blocker", "zip_path", info.filename, "unsafe archive path"))
                normalized = "/".join(part for part in portable.parts if part not in {"", "."}).casefold()
                if normalized in normalized_names:
                    findings.append(Finding("blocker", "zip_duplicate_path", info.filename, "duplicate normalized archive path"))
                normalized_names.add(normalized)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    findings.append(Finding("blocker", "zip_symlink", info.filename, "symlink entry"))
                if info.flag_bits & 0x1:
                    encrypted = True
                    findings.append(Finding("blocker", "zip_encrypted", info.filename, "encrypted entry"))
                total_size += info.file_size
                if info.file_size > MAX_ZIP_FILE_BYTES:
                    findings.append(Finding("blocker", "zip_file_limit", info.filename, "archive entry is too large"))
                if info.compress_size > 0 and info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO:
                    findings.append(Finding("blocker", "zip_ratio", info.filename, "archive entry has a suspicious compression ratio"))
            if total_size > MAX_ZIP_TOTAL_BYTES:
                findings.append(Finding("blocker", "zip_total_limit", path.name, "archive expands beyond the total size limit"))
            if not encrypted:
                bad = archive.testzip()
                if bad:
                    findings.append(Finding("blocker", "zip_crc", bad, "CRC validation failed"))
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        findings.append(Finding("blocker", "invalid_zip", path.name, type(exc).__name__))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--zip", dest="zip_path", type=Path)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="run structural/security checks without claiming final completeness",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="also run the offline application smoke in development audit mode",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    run_smoke = (not args.allow_incomplete) or args.smoke
    findings = audit_tree(
        root,
        require_complete=not args.allow_incomplete,
        run_smoke=False,
    )
    if run_smoke:
        smoke_result = run_offline_app_smoke(root)
        findings.extend(_smoke_findings(smoke_result))
        smoke_payload: dict[str, object] = smoke_result.to_public_dict()
    else:
        smoke_payload = {
            "ok": None,
            "status": "skipped",
            "detail": "development audit skips offline app smoke unless --smoke is set",
            "steps": [],
        }
    if args.zip_path:
        findings.extend(audit_zip(args.zip_path.resolve()))
    payload = {
        "root": root.name,
        "tree_sha256": sha256(
            "\n".join(sorted(tracked_files(root))).encode("utf-8")
        ).hexdigest(),
        "finding_count": len(findings),
        "blockers": sum(item.severity == "blocker" for item in findings),
        "structural_ready": not findings,
        "release_ready": not findings and not args.allow_incomplete,
        "audit_mode": "development" if args.allow_incomplete else "final",
        "offline_app_smoke": smoke_payload,
        "findings": [asdict(item) for item in findings],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if payload["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

