"""Build clean single-commit submission ZIP artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from nining.release_audit import Finding, audit_tree, audit_zip


@dataclass(frozen=True)
class BuildError(Exception):
    code: str
    message: str
    finding_codes: tuple[str, ...] = ()


def _safe_git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    if extra:
        env.update(extra)
    return env


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=_safe_git_env(env),
    )
    if completed.returncode != 0:
        raise BuildError("command_failed", f"{command[0]} {command[1]} failed")
    return completed.stdout.strip()


def _tracked_files(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        env=_safe_git_env(),
    )
    if output.returncode != 0:
        raise BuildError("git_failed", "git could not list tracked files")
    return sorted(item.decode("utf-8") for item in output.stdout.split(b"\0") if item)


def _copy_snapshot(root: Path, stage: Path) -> None:
    for relative in _tracked_files(root):
        source = root / relative
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _require_clean_worktree(root: Path) -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=False,
        capture_output=True,
        env=_safe_git_env(),
    )
    if completed.returncode != 0:
        raise BuildError("not_git_repository", "root must be a Git repository")
    if completed.stdout.strip():
        raise BuildError("dirty_worktree", "working tree must be clean before packaging")


def _finding_codes(findings: Iterable[Finding]) -> tuple[str, ...]:
    return tuple(sorted({item.code for item in findings}))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _initialize_release_git(stage: Path, source_commit: str) -> str:
    template_root = stage.parent / "empty-git-template"
    template_root.mkdir()
    _run(
        ["git", "init", "-q", "-b", "main", "--template", str(template_root)],
        cwd=stage,
    )
    _run(["git", "config", "user.name", "Nining Release Builder"], cwd=stage)
    _run(["git", "config", "user.email", "release@example.invalid"], cwd=stage)
    _run(["git", "add", "-A"], cwd=stage)
    _run(
        [
            "git",
            "commit",
            "-q",
            "-m",
            f"Release snapshot from {source_commit[:12]}",
        ],
        cwd=stage,
    )
    release_commit = _run(["git", "rev-parse", "HEAD"], cwd=stage)
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=stage,
    )
    if status:
        raise BuildError("release_repo_dirty", "release repository is not clean")
    commit_count = _run(["git", "rev-list", "--count", "HEAD"], cwd=stage)
    if commit_count != "1":
        raise BuildError(
            "release_repo_commit_count",
            "release repository must contain exactly one commit",
        )
    remotes = _run(["git", "remote"], cwd=stage)
    if remotes:
        raise BuildError("release_repo_remote", "release repository must not contain remotes")
    return release_commit


def _iter_package_files(stage: Path):
    for path in sorted(stage.rglob("*")):
        if path.is_file() and path.name != ".DS_Store" and not path.name.startswith("._"):
            yield path


def _write_zip(stage: Path, temporary: Path) -> int:
    count = 0
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for path in _iter_package_files(stage):
            relative = path.relative_to(stage).as_posix()
            archive.write(path, f"submission/{relative}")
            count += 1
    return count


def _require_expected_zip_shape(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = [item.filename for item in archive.infolist()]
    if not names:
        raise BuildError("zip_empty", "ZIP archive must not be empty")
    if any(not name.startswith("submission/") for name in names):
        raise BuildError("zip_prefix", "ZIP entries must live under submission/")
    if "submission/.git/HEAD" not in names:
        raise BuildError(
            "zip_missing_git",
            "ZIP archive must include the release .git directory",
        )


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    temporary.replace(path)


def build(
    root: Path,
    output: Path,
    *,
    allow_incomplete: bool,
    zip_auditor: Callable[[Path], list[Finding]] = audit_zip,
) -> dict[str, object]:
    root = root.resolve()
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise BuildError("output_extension", "output must end with .zip")
    if _is_relative_to(output, root):
        raise BuildError(
            "output_inside_source",
            "output must be outside the source repository",
        )
    _require_clean_worktree(root)
    findings = audit_tree(root, require_complete=not allow_incomplete)
    if findings:
        raise BuildError(
            "release_audit_blocked",
            "release audit blocked packaging",
            _finding_codes(findings),
        )
    source_commit = _run(["git", "rev-parse", "HEAD"], cwd=root)

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_zip: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="nining-release-stage-") as directory:
            stage_parent = Path(directory)
            stage = stage_parent / "submission"
            stage.mkdir()
            _copy_snapshot(root, stage)
            release_commit = _initialize_release_git(stage, source_commit)
            with tempfile.NamedTemporaryFile(
                dir=output.parent,
                prefix=f".{output.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_zip = Path(handle.name)
            file_count = _write_zip(stage, temp_zip)
        _require_expected_zip_shape(temp_zip)
        zip_findings = zip_auditor(temp_zip)
        if zip_findings:
            raise BuildError(
                "zip_audit_blocked",
                "ZIP audit blocked packaging",
                _finding_codes(zip_findings),
            )
        digest = sha256(temp_zip.read_bytes()).hexdigest()
        zip_size_bytes = temp_zip.stat().st_size
        temp_zip.replace(output)
        temp_zip = None
    finally:
        if temp_zip is not None:
            temp_zip.unlink(missing_ok=True)

    manifest = {
        "schema_version": "1.0",
        "source_commit": source_commit,
        "release_commit": release_commit,
        "zip_name": output.name,
        "zip_size_bytes": zip_size_bytes,
        "zip_sha256": digest,
        "file_count": file_count,
        "contains_git_directory": True,
        "audit_mode": "development" if allow_incomplete else "final",
        "release_ready": not allow_incomplete,
    }
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    _write_json_atomic(manifest_path, manifest)
    return manifest


def _error_payload(error: BuildError) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "blocked",
        "error_code": error.code,
        "message": error.message,
    }
    if error.finding_codes:
        payload["finding_codes"] = list(error.finding_codes)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="build a development-only package with release_ready=false",
    )
    args = parser.parse_args(argv)
    try:
        manifest = build(
            args.root,
            args.output.resolve(),
            allow_incomplete=args.allow_incomplete,
        )
    except BuildError as exc:
        print(json.dumps(_error_payload(exc), ensure_ascii=False, sort_keys=True))
        return 2
    except (OSError, subprocess.SubprocessError):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_code": "packaging_error",
                    "message": "packaging failed without writing a release artifact",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {"status": "ok", "result": manifest},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0
