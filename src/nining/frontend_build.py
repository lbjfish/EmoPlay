#!/usr/bin/env python3
"""Build and publish the fixed Nining web frontend asset bundle."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_FRONTEND_DIR = ROOT / "src" / "nining" / "web" / "frontend"
ROOT_FRONTEND_DIR = ROOT / "frontend"
ASSETS_DIR = ROOT / "src" / "nining" / "web" / "assets"

REQUIRED_ASSETS = (
    "index.html",
    "game.css",
    "react-core.js",
    "game.js",
    "effects.js",
    "media-review.js",
    "god-charts.js",
)
LEGACY_ASSETS = ("app.css", "app.js")
GZIP_BUDGETS = {
    "index.html": 25 * 1024,
    "game.css": 100 * 1024,
    "react-core.js": 260 * 1024,
    "game.js": 260 * 1024,
    "effects.js": 120 * 1024,
    "media-review.js": 180 * 1024,
    "god-charts.js": 180 * 1024,
}
INITIAL_JS_GZIP_BUDGET = 260 * 1024
TOTAL_JS_GZIP_BUDGET = 650 * 1024
LICENSE_FILES = (
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "THIRD_PARTY_LICENSES.md",
    "THIRD_PARTY_LICENSES.txt",
    "licenses/LICENSE",
    "licenses/THIRD_PARTY_LICENSES.md",
)
NOTICE_FILES = (
    "NOTICE",
    "NOTICE.md",
    "NOTICE.txt",
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_NOTICES.txt",
    "licenses/NOTICE",
    "licenses/THIRD_PARTY_NOTICES.md",
)
EXTERNAL_URL = re.compile(
    r"(?:https?|wss?)://[^\s\"'`<>\\]+|(?<!:)//[A-Za-z0-9.-]+\.[A-Za-z][^\s\"'`<>\\]*"
)
NON_REQUEST_LIBRARY_URL_PREFIXES = (
    "http://www.w3.org/",
    "https://bit.ly/3cXEKWf",
    "https://photoswipe.com",
    "https://react.dev/errors/",
    "https://redux-toolkit.js.org/Errors",
    "https://redux.js.org/Errors",
)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
SECRET_PATTERNS = {
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "bearer_value": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}"),
    "assigned_secret": re.compile(
        r"(?i)(?:api[_-]?key|secret|password)\s*[:=]\s*[\"']?[A-Za-z0-9._~+/-]{12,}"
    ),
    "private_azure_host": re.compile(r"[A-Za-z0-9-]+\.services\.ai\.azure\.com"),
}
ABSOLUTE_PATHS = {
    "mac_home": re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    "linux_home": re.compile(r"/home/[A-Za-z0-9._-]+/"),
    "windows_home": re.compile(r"(?i)[A-Z]:\\Users\\[^\\]+\\"),
}
FRONTEND_CHECKS = ("lint", "typecheck", "test")


class FrontendBuildError(Exception):
    """A sanitized frontend build or validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def default_frontend_dir() -> Path:
    """Return the preferred checked-in frontend source directory."""

    if PACKAGE_FRONTEND_DIR.exists():
        return PACKAGE_FRONTEND_DIR
    return ROOT_FRONTEND_DIR


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FrontendBuildError(
            "invalid_text_asset", f"{path.name} must be valid UTF-8 text"
        ) from exc


def _sanitize_error_message(message: str) -> str:
    sanitized = message.replace(str(ROOT), "<repo>")
    sanitized = re.sub(r"/Users/[A-Za-z0-9._-]+/", "<home>/", sanitized)
    sanitized = re.sub(r"(?i)[A-Z]:\\Users\\[^\\]+\\", r"<home>\\", sanitized)
    return sanitized


def _plain_error_line(line: str) -> str:
    return ANSI_ESCAPE.sub("", line.strip())


def validate_frontend_metadata(frontend_dir: Path) -> None:
    """Ensure the build is anchored to a committed pnpm frontend source tree."""

    package_json = frontend_dir / "package.json"
    lockfile = frontend_dir / "pnpm-lock.yaml"
    if not package_json.is_file():
        raise FrontendBuildError(
            "missing_frontend_source", "frontend/package.json is required"
        )
    if not lockfile.is_file():
        raise FrontendBuildError(
            "missing_frontend_lockfile", "frontend/pnpm-lock.yaml is required"
        )
    try:
        package = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FrontendBuildError(
            "invalid_package_json", "frontend/package.json is not valid JSON"
        ) from exc
    package_manager = package.get("packageManager")
    if not isinstance(package_manager, str) or not package_manager.startswith("pnpm@"):
        raise FrontendBuildError(
            "missing_package_manager",
            "frontend/package.json must pin packageManager to pnpm",
        )
    notice_paths = [frontend_dir / name for name in NOTICE_FILES]
    notice_found = any(path.is_file() for path in notice_paths)
    license_found = any((frontend_dir / name).is_file() for name in LICENSE_FILES)
    if not license_found:
        for path in notice_paths:
            if path.is_file() and "license" in path.read_text(encoding="utf-8").lower():
                license_found = True
                break
    if not license_found:
        raise FrontendBuildError(
            "missing_license_inventory",
            "frontend license inventory is required",
        )
    if not notice_found:
        raise FrontendBuildError(
            "missing_notice_inventory",
            "frontend third-party notice inventory is required",
        )


def _run_pnpm_command(
    command: list[str],
    *,
    error_code: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    try:
        runner(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FrontendBuildError(
            "pnpm_unavailable", "pnpm is required to build frontend assets"
        ) from exc
    except subprocess.CalledProcessError as exc:
        output = "\n".join(part for part in (exc.stderr, exc.stdout) if part)
        detail = (output or "pnpm command failed").splitlines()
        useful = "pnpm command failed"
        for line in detail:
            stripped = _plain_error_line(line)
            if (
                "Error:" in stripped
                or "Could not resolve" in stripped
                or "Module not found" in stripped
            ):
                useful = stripped
                break
        else:
            for line in detail:
                stripped = _plain_error_line(line)
                if stripped.startswith("Build failed with"):
                    useful = stripped
                    break
            else:
                for line in reversed(detail):
                    stripped = _plain_error_line(line)
                    if (
                        not stripped
                        or stripped in {"{", "}"}
                        or stripped.startswith(("errors:", "at "))
                    ):
                        continue
                    if "error" in stripped.lower() or "failed" in stripped.lower():
                        useful = stripped
                        break
                    useful = stripped
                    break
        message = _sanitize_error_message(useful)
        raise FrontendBuildError(error_code, message[:300]) from exc


def run_frontend_checks(
    frontend_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Run fixed frontend quality checks before producing build assets."""

    for script in FRONTEND_CHECKS:
        _run_pnpm_command(
            ["pnpm", "--dir", str(frontend_dir), "run", script],
            error_code="pnpm_check_failed",
            runner=runner,
        )


def run_pnpm_build(
    frontend_dir: Path,
    staging_dir: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Run the fixed production build command into a staging directory."""

    command = [
        "pnpm",
        "--dir",
        str(frontend_dir),
        "exec",
        "vite",
        "build",
        "--outDir",
        str(staging_dir),
        "--emptyOutDir",
        "true",
    ]
    _run_pnpm_command(command, error_code="pnpm_build_failed", runner=runner)


def _validate_required_file_set(staging_dir: Path) -> list[Path]:
    files = sorted(
        path.relative_to(staging_dir).as_posix()
        for path in staging_dir.rglob("*")
        if path.is_file()
    )
    expected = sorted(REQUIRED_ASSETS)
    if files != expected:
        raise FrontendBuildError(
            "unexpected_frontend_assets",
            f"expected fixed assets {expected}, found {files}",
        )
    return [staging_dir / name for name in REQUIRED_ASSETS]


def _validate_index(index: str) -> None:
    if "__NINING_ACTION_TOKEN__" not in index:
        raise FrontendBuildError(
            "missing_action_token_placeholder",
            "index.html must preserve __NINING_ACTION_TOKEN__",
        )
    for name in ("game.css", "game.js"):
        if f"/assets/{name}" not in index:
            raise FrontendBuildError(
                "missing_asset_reference", f"index.html must reference {name}"
            )
    for legacy in LEGACY_ASSETS:
        if legacy in index:
            raise FrontendBuildError(
                "legacy_asset_reference", f"index.html must not reference {legacy}"
            )


def _scan_text_asset(path: Path, text: str) -> None:
    if path.suffix == ".map" or path.name.endswith(".map"):
        raise FrontendBuildError("source_map_forbidden", "source maps are forbidden")
    for match in EXTERNAL_URL.finditer(text):
        url = match.group(0)
        if not url.startswith(NON_REQUEST_LIBRARY_URL_PREFIXES):
            raise FrontendBuildError(
                "external_url_forbidden",
                f"{path.name} contains an unapproved external URL",
            )
    for code, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            raise FrontendBuildError(code, f"{path.name} contains a secret pattern")
    for code, pattern in ABSOLUTE_PATHS.items():
        if pattern.search(text):
            raise FrontendBuildError(code, f"{path.name} contains an absolute path")


def _gzip_size(content: bytes) -> int:
    return len(gzip.compress(content, compresslevel=9, mtime=0))


def validate_staging(staging_dir: Path) -> dict[str, dict[str, int]]:
    """Validate and summarize a staged fixed frontend bundle."""

    if not staging_dir.is_dir():
        raise FrontendBuildError("missing_staging_dir", "frontend staging dir is absent")
    summary: dict[str, dict[str, int]] = {}
    gzip_sizes: dict[str, int] = {}
    for path in _validate_required_file_set(staging_dir):
        content = path.read_bytes()
        gzip_size = _gzip_size(content)
        gzip_sizes[path.name] = gzip_size
        budget = GZIP_BUDGETS[path.name]
        if gzip_size > budget:
            raise FrontendBuildError(
                "gzip_budget_exceeded",
                f"{path.name} gzip size {gzip_size} exceeds budget {budget}",
            )
        text = _read_text(path)
        if path.name == "index.html":
            _validate_index(text)
        _scan_text_asset(path, text)
        summary[path.name] = {"bytes": len(content), "gzip_bytes": gzip_size}
    initial_js = gzip_sizes["react-core.js"] + gzip_sizes["game.js"]
    if initial_js > INITIAL_JS_GZIP_BUDGET:
        raise FrontendBuildError(
            "gzip_budget_exceeded",
            f"initial JavaScript gzip size {initial_js} exceeds budget {INITIAL_JS_GZIP_BUDGET}",
        )
    total_js = sum(gzip_sizes[name] for name in REQUIRED_ASSETS if name.endswith(".js"))
    if total_js > TOTAL_JS_GZIP_BUDGET:
        raise FrontendBuildError(
            "gzip_budget_exceeded",
            f"total JavaScript gzip size {total_js} exceeds budget {TOTAL_JS_GZIP_BUDGET}",
        )
    return summary


def publish_assets(
    staging_dir: Path,
    assets_dir: Path,
    *,
    preserve_legacy: bool = True,
) -> None:
    """Atomically publish validated assets into the Python package."""

    validate_staging(staging_dir)
    assets_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = assets_dir.parent / f".{assets_dir.name}.tmp-{uuid.uuid4().hex}"
    backup = assets_dir.parent / f".{assets_dir.name}.bak-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        for name in REQUIRED_ASSETS:
            shutil.copy2(staging_dir / name, temporary / name)
        if preserve_legacy and assets_dir.is_dir():
            for name in LEGACY_ASSETS:
                legacy = assets_dir / name
                if legacy.is_file():
                    shutil.copy2(legacy, temporary / name)
        if assets_dir.exists():
            assets_dir.rename(backup)
        try:
            temporary.rename(assets_dir)
        except Exception:
            if backup.exists() and not assets_dir.exists():
                backup.rename(assets_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def build_and_publish(
    frontend_dir: Path,
    assets_dir: Path,
    *,
    staging_dir: Path | None = None,
    skip_build: bool = False,
    dry_run: bool = False,
    preserve_legacy: bool = True,
) -> dict[str, dict[str, int]]:
    """Build, validate, and optionally publish the frontend bundle."""

    frontend_dir = frontend_dir.resolve()
    assets_dir = assets_dir.resolve()
    validate_frontend_metadata(frontend_dir)

    if staging_dir is None:
        with tempfile.TemporaryDirectory(prefix="nining-frontend-") as raw:
            staging = Path(raw)
            if not skip_build:
                run_frontend_checks(frontend_dir)
                run_pnpm_build(frontend_dir, staging)
            summary = validate_staging(staging)
            if not dry_run:
                publish_assets(staging, assets_dir, preserve_legacy=preserve_legacy)
            return summary

    staging = staging_dir.resolve()
    if not skip_build:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        run_frontend_checks(frontend_dir)
        run_pnpm_build(frontend_dir, staging)
    summary = validate_staging(staging)
    if not dry_run:
        publish_assets(staging, assets_dir, preserve_legacy=preserve_legacy)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-dir", type=Path, default=default_frontend_dir())
    parser.add_argument("--assets-dir", type=Path, default=ASSETS_DIR)
    parser.add_argument("--staging-dir", type=Path)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="validate an existing staging directory without invoking pnpm",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and validate without replacing package assets",
    )
    parser.add_argument(
        "--drop-legacy",
        action="store_true",
        help="do not carry app.css/app.js into the published assets directory",
    )
    args = parser.parse_args(argv)

    try:
        summary = build_and_publish(
            args.frontend_dir,
            args.assets_dir,
            staging_dir=args.staging_dir,
            skip_build=args.skip_build,
            dry_run=args.dry_run,
            preserve_legacy=not args.drop_legacy,
        )
    except FrontendBuildError as exc:
        payload = {
            "status": "blocked",
            "error_code": exc.code,
            "message": exc.message,
        }
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "assets": summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
