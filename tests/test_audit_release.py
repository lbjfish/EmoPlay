from __future__ import annotations

import json
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest

from nining.canonical import canonical_sha256
from nining.release_audit import audit_tree, audit_zip, main
from nining.release_smoke import OfflineAppSmokeResult, SmokeStep


def make_tree(root: Path) -> None:
    for name in (
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
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if path.suffix == ".json" else "safe\n", encoding="utf-8")
    for index in range(1, 7):
        path = root / "outputs" / "specs" / f"T{index}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"trigger_id": f"T{index}"}
        payload["spec_sha256"] = canonical_sha256(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = {
        "spec_count": 6,
        "triggers": [f"T{index}" for index in range(1, 7)],
        "spec_sha256": {
            f"T{index}": json.loads(
                (root / "outputs" / "specs" / f"T{index}.json").read_text()
            )["spec_sha256"]
            for index in range(1, 7)
        },
    }
    (root / "outputs" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    report_root = root / "output" / "pdf"
    report_root.mkdir(parents=True, exist_ok=True)
    pdf_bytes = b"%PDF-1.4\n/Type /Page\n%%EOF\n"
    reports = {}
    for name, limit in (("nining-main-report.pdf", 3),):
        (report_root / name).write_bytes(pdf_bytes)
        reports[name] = {
            "pages": 1,
            "page_limit": limit,
            "sha256": sha256(pdf_bytes).hexdigest(),
        }
    (report_root / "manifest.json").write_text(
        json.dumps({"reports": reports}), encoding="utf-8"
    )


def test_clean_tree_passes(tmp_path: Path) -> None:
    make_tree(tmp_path)
    assert audit_tree(tmp_path) == []


def test_final_gate_rejects_structurally_clean_but_incomplete_tree(
    tmp_path: Path,
) -> None:
    make_tree(tmp_path)

    codes = {item.code for item in audit_tree(tmp_path, require_complete=True)}

    assert "final_media_incomplete" in codes
    assert "visual_reference_count" in codes
    assert "rejected_results_incomplete" in codes


def test_secret_and_absolute_path_are_blockers(tmp_path: Path) -> None:
    make_tree(tmp_path)
    (tmp_path / "bad.txt").write_text(
        "api_" + "key=" + "abcdefghijklmnopqrstuvwx" + "\n/" + "Users/example/private/file\n",
        encoding="utf-8",
    )
    codes = {item.code for item in audit_tree(tmp_path)}
    assert "assigned_secret" in codes
    assert "mac_home" in codes


def test_unsafe_zip_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "bad")
    assert any(item.code == "zip_path" for item in audit_zip(archive))


def test_windows_and_backslash_zip_paths_are_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad-windows.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(r"..\escape.txt", "bad")
        handle.writestr("C:" + r"\Users\candidate\secret.txt", "bad")

    findings = audit_zip(archive)

    assert sum(item.code == "zip_path" for item in findings) == 2


def test_zip_duplicate_and_high_ratio_entries_are_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "suspicious.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("Folder/file.txt", "one")
        handle.writestr("folder/./FILE.txt", "two")
        handle.writestr("compressed.txt", "0" * 1_000_000)

    codes = {item.code for item in audit_zip(archive)}

    assert "zip_duplicate_path" in codes
    assert "zip_ratio" in codes


def test_large_text_is_a_blocker_instead_of_being_skipped(tmp_path: Path) -> None:
    make_tree(tmp_path)
    (tmp_path / "large.txt").write_text("x" * 2_000_001, encoding="utf-8")

    codes = {item.code for item in audit_tree(tmp_path)}

    assert "text_too_large_unscanned" in codes


def test_generated_frontend_dependencies_are_ignored_but_source_is_scanned(
    tmp_path: Path,
) -> None:
    make_tree(tmp_path)
    generated = tmp_path / "frontend" / "node_modules" / "fixture" / "bad.ts"
    generated.parent.mkdir(parents=True)
    generated.write_text(
        "api_" + "key=abcdefghijklmnopqrstuvwx\n", encoding="utf-8"
    )
    source = tmp_path / "frontend" / "src" / "bad.tsx"
    source.parent.mkdir(parents=True)
    source.write_text(
        "api_" + "key=zyxwvutsrqponmlkjihgfedc\n", encoding="utf-8"
    )

    findings = audit_tree(tmp_path)

    secret_paths = {
        item.path for item in findings if item.code == "assigned_secret"
    }
    assert secret_paths == {"frontend/src/bad.tsx"}


def test_static_clean_tree_blocks_when_offline_app_smoke_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_tree(tmp_path)

    def fail_smoke(_root: Path) -> OfflineAppSmokeResult:
        return OfflineAppSmokeResult(
            ok=False,
            status="failed",
            steps=(
                SmokeStep(
                    "local_web",
                    "failed",
                    "/" + "Users/example/private api_" + "key=abcdefghijklmnopqrstuvwx",
                ),
            ),
            detail="/" + "Users/example/private api_" + "key=abcdefghijklmnopqrstuvwx",
        )

    monkeypatch.setattr("nining.release_audit.run_offline_app_smoke", fail_smoke)

    findings = audit_tree(tmp_path, run_smoke=True)

    assert [item.code for item in findings] == ["offline_app_smoke"]
    assert "/" + "Users/" not in findings[0].detail
    assert "abcdefghijklmnopqrstuvwx" not in findings[0].detail


def test_final_cli_runs_offline_app_smoke_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_tree(tmp_path)

    def fail_smoke(_root: Path) -> OfflineAppSmokeResult:
        return OfflineAppSmokeResult(
            ok=False,
            status="failed",
            steps=(SmokeStep("session", "failed", "ApplicationSession.open failed"),),
            detail="ApplicationSession.open failed",
        )

    monkeypatch.setattr("nining.release_audit.run_offline_app_smoke", fail_smoke)

    status = main([str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert status == 1
    assert payload["audit_mode"] == "final"
    assert payload["offline_app_smoke"]["status"] == "failed"
    assert "offline_app_smoke" in {item["code"] for item in payload["findings"]}


def test_development_cli_skips_smoke_unless_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    make_tree(tmp_path)
    calls = 0

    def fail_smoke(_root: Path) -> OfflineAppSmokeResult:
        nonlocal calls
        calls += 1
        return OfflineAppSmokeResult(
            ok=False,
            status="failed",
            steps=(SmokeStep("session", "failed", "forced failure"),),
            detail="forced failure",
        )

    monkeypatch.setattr("nining.release_audit.run_offline_app_smoke", fail_smoke)

    skipped_status = main([str(tmp_path), "--allow-incomplete"])
    skipped_payload = json.loads(capsys.readouterr().out)

    assert skipped_status == 0
    assert calls == 0
    assert skipped_payload["audit_mode"] == "development"
    assert skipped_payload["offline_app_smoke"]["status"] == "skipped"

    smoke_status = main([str(tmp_path), "--allow-incomplete", "--smoke"])
    smoke_payload = json.loads(capsys.readouterr().out)

    assert smoke_status == 1
    assert calls == 1
    assert smoke_payload["audit_mode"] == "development"
    assert smoke_payload["offline_app_smoke"]["status"] == "failed"
    assert "offline_app_smoke" in {item["code"] for item in smoke_payload["findings"]}
