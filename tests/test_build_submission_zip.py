from __future__ import annotations

import json
import os
import subprocess
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest

from nining.canonical import canonical_sha256
from nining.release_audit import Finding
from nining.release_package import BuildError, build, main


def run_git(root: Path, *args: str) -> str:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def write_minimal_release_tree(root: Path) -> None:
    text_files = {
        "README.md": "safe\n",
        "pyproject.toml": "[project]\nname = 'fixture'\n",
        "requirements.txt": "\n",
        ".env.example": "AZURE_TEXT_API_KEY=\n",
        ".gitignore": "tmp/\n*.local\n",
        "config.example.toml": "[provider]\n",
        "config.offline.toml": "[provider]\n",
        "docs/submission/main_report.md": "candidate\n",
        "docs/submission/sales_appendix.md": "candidate\n",
        "docs/submission/human_review_checklist.md": "pending\n",
    }
    for relative, content in text_files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    (root / "outputs").mkdir()
    for index in range(1, 7):
        payload = {"trigger_id": f"T{index}"}
        payload["spec_sha256"] = canonical_sha256(payload)
        spec_path = root / "outputs" / "specs" / f"T{index}.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(json.dumps(payload), encoding="utf-8")

    spec_hashes = {
        f"T{index}": json.loads(
            (root / "outputs" / "specs" / f"T{index}.json").read_text(
                encoding="utf-8"
            )
        )["spec_sha256"]
        for index in range(1, 7)
    }
    (root / "outputs" / "manifest.json").write_text(
        json.dumps(
            {
                "spec_count": 6,
                "triggers": [f"T{index}" for index in range(1, 7)],
                "spec_sha256": spec_hashes,
                "known_blockers": ["fixture remains incomplete"],
            }
        ),
        encoding="utf-8",
    )
    (root / "outputs" / "metrics.json").write_text("{}\n", encoding="utf-8")

    report_root = root / "output" / "pdf"
    report_root.mkdir(parents=True)
    pdf_bytes = b"%PDF-1.4\n/Type /Page\n%%EOF\n"
    reports: dict[str, dict[str, int | str]] = {}
    for name, page_limit in (("nining-main-report.pdf", 3),):
        (report_root / name).write_bytes(pdf_bytes)
        reports[name] = {
            "pages": 1,
            "page_limit": page_limit,
            "sha256": sha256(pdf_bytes).hexdigest(),
        }
    (report_root / "manifest.json").write_text(
        json.dumps({"reports": reports}),
        encoding="utf-8",
    )


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    write_minimal_release_tree(root)
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "config", "user.name", "Test Builder")
    run_git(root, "config", "user.email", "builder@example.invalid")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "fixture")
    return root


def extract_zip(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as handle:
        handle.extractall(destination)
    return destination / "submission"


def test_dirty_tree_blocks_with_sanitized_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(tmp_path)
    (root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    output = tmp_path / "out" / "submission.zip"

    status = main([str(root), "--output", str(output), "--allow-incomplete"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert status == 2
    assert payload == {
        "error_code": "dirty_worktree",
        "message": "working tree must be clean before packaging",
        "status": "blocked",
    }
    assert str(root) not in captured.out
    assert captured.err == ""
    assert not output.exists()


def test_strict_default_blocks_incomplete_release(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    output = tmp_path / "out" / "submission.zip"

    with pytest.raises(BuildError) as raised:
        build(root, output, allow_incomplete=False)

    assert raised.value.code == "release_audit_blocked"
    assert "known_blockers" in raised.value.finding_codes
    assert "final_media_incomplete" in raised.value.finding_codes
    assert not output.exists()


def test_output_inside_source_tree_is_rejected(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    output = root / "submission.zip"

    with pytest.raises(BuildError) as raised:
        build(root, output, allow_incomplete=True)

    assert raised.value.code == "output_inside_source"
    assert not output.exists()
    assert not output.with_suffix(output.suffix + ".manifest.json").exists()


def test_allow_incomplete_builds_development_zip_with_manifest(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    source_commit = run_git(root, "rev-parse", "HEAD")
    ignored = root / "tmp" / "ignored.txt"
    ignored.parent.mkdir()
    ignored.write_text("should not be copied\n", encoding="utf-8")
    output = tmp_path / "out" / "submission.zip"

    manifest = build(root, output, allow_incomplete=True)

    assert manifest["audit_mode"] == "development"
    assert manifest["release_ready"] is False
    assert manifest["source_commit"] == source_commit
    assert manifest["zip_name"] == output.name
    assert manifest["zip_sha256"] == sha256(output.read_bytes()).hexdigest()
    assert manifest["zip_size_bytes"] == output.stat().st_size
    assert str(tmp_path) not in json.dumps(manifest)
    assert not (root / "work").exists()

    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest

    with zipfile.ZipFile(output) as archive:
        names = sorted(info.filename for info in archive.infolist())
    assert names
    assert all(name.startswith("submission/") for name in names)
    assert "submission/.git/HEAD" in names
    assert "submission/tmp/ignored.txt" not in names
    assert manifest["file_count"] == len(names)


def test_zip_contains_clean_single_commit_repository(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    output = tmp_path / "out" / "submission.zip"
    manifest = build(root, output, allow_incomplete=True)
    extracted = extract_zip(output, tmp_path / "extract")

    assert run_git(extracted, "rev-list", "--count", "HEAD") == "1"
    assert run_git(extracted, "rev-parse", "HEAD") == manifest["release_commit"]
    assert run_git(extracted, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert run_git(extracted, "remote") == ""
    assert (extracted / "README.md").read_text(encoding="utf-8") == "safe\n"


def test_zip_audit_failure_cleans_temporary_output_and_preserves_existing_zip(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    output = output_dir / "submission.zip"
    output.write_bytes(b"previous")

    def fail_zip_audit(_: Path) -> list[Finding]:
        return [Finding("blocker", "forced_zip_failure", "submission", "forced")]

    with pytest.raises(BuildError) as raised:
        build(root, output, allow_incomplete=True, zip_auditor=fail_zip_audit)

    assert raised.value.code == "zip_audit_blocked"
    assert raised.value.finding_codes == ("forced_zip_failure",)
    assert output.read_bytes() == b"previous"
    assert not output.with_suffix(output.suffix + ".manifest.json").exists()
    assert sorted(path.name for path in output_dir.iterdir()) == ["submission.zip"]
    assert not (root / "work").exists()


def test_no_final_output_is_left_when_zip_audit_fails_without_existing_zip(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    output = tmp_path / "out" / "submission.zip"

    def fail_zip_audit(_: Path) -> list[Finding]:
        return [Finding("blocker", "forced_zip_failure", "submission", "forced")]

    with pytest.raises(BuildError):
        build(root, output, allow_incomplete=True, zip_auditor=fail_zip_audit)

    assert not output.exists()
    assert not output.with_suffix(output.suffix + ".manifest.json").exists()
    assert list(output.parent.glob("*.tmp")) == []
