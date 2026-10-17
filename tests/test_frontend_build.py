from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from nining import frontend_build

ROOT = Path(__file__).resolve().parents[1]


def _write_frontend_metadata(frontend: Path) -> None:
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "private": True,
                "packageManager": "pnpm@9.15.0",
                "scripts": {"build": "vite build"},
            }
        ),
        encoding="utf-8",
    )
    (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (frontend / "THIRD_PARTY_LICENSES.md").write_text("React MIT\n", encoding="utf-8")
    (frontend / "THIRD_PARTY_NOTICES.md").write_text("Bundled notices\n", encoding="utf-8")


def _write_staged_assets(staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    refs = "\n".join(
        f'    <script type="module" src="/assets/{name}"></script>'
        for name in frontend_build.REQUIRED_ASSETS
        if name.endswith(".js")
    )
    (staging / "index.html").write_text(
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta name="nining-action-token" content="__NINING_ACTION_TOKEN__">
    <link rel="stylesheet" href="/assets/game.css">
{refs}
  </head>
  <body><main id="root"></main></body>
</html>""",
        encoding="utf-8",
    )
    (staging / "game.css").write_text(
        "@media (max-width: 740px) { #root { display: block; } }\n",
        encoding="utf-8",
    )
    for name in frontend_build.REQUIRED_ASSETS:
        if name.endswith(".js"):
            (staging / name).write_text(
                f'const niningChunk = "{name}";\nexport default niningChunk;\n',
                encoding="utf-8",
            )


def test_build_wrapper_validates_and_atomically_publishes_fixed_assets(
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    staging = tmp_path / "staging"
    assets = tmp_path / "assets"
    _write_frontend_metadata(frontend)
    _write_staged_assets(staging)
    assets.mkdir()
    (assets / "app.css").write_text("legacy css\n", encoding="utf-8")
    (assets / "app.js").write_text("legacy js\n", encoding="utf-8")
    (assets / "unrelated.txt").write_text("drop me\n", encoding="utf-8")

    summary = frontend_build.build_and_publish(
        frontend,
        assets,
        staging_dir=staging,
        skip_build=True,
    )

    assert sorted(summary) == sorted(frontend_build.REQUIRED_ASSETS)
    assert all((assets / name).is_file() for name in frontend_build.REQUIRED_ASSETS)
    assert (assets / "app.css").read_text(encoding="utf-8") == "legacy css\n"
    assert (assets / "app.js").read_text(encoding="utf-8") == "legacy js\n"
    assert not (assets / "unrelated.txt").exists()


def test_build_wrapper_rejects_unfixed_or_unsafe_staging(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    _write_staged_assets(staging)
    (staging / "index.html").write_text(
        (staging / "index.html")
        .read_text(encoding="utf-8")
        .replace("__NINING_ACTION_TOKEN__", "missing"),
        encoding="utf-8",
    )

    with pytest.raises(frontend_build.FrontendBuildError) as raised:
        frontend_build.validate_staging(staging)

    assert raised.value.code == "missing_action_token_placeholder"


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("extra.js", "export const x = 1;\n", "unexpected_frontend_assets"),
        ("game.js", "fetch('https://example.invalid/app.js');\n", "external_url_forbidden"),
        ("media-review.js", "const key = 'sk-' + 'x'.repeat(24);\n", "openai_style_key"),
        (
            "god-charts.js",
            "const p = '/' + 'Users' + '/sample/private/file';\n",
            "mac_home",
        ),
    ],
)
def test_build_wrapper_rejects_extra_chunks_external_urls_and_sensitive_text(
    tmp_path: Path,
    filename: str,
    content: str,
    code: str,
) -> None:
    staging = tmp_path / "staging"
    _write_staged_assets(staging)
    if filename == "extra.js":
        (staging / filename).write_text(content, encoding="utf-8")
    else:
        if code == "openai_style_key":
            content = "const key = '" + "sk-" + ("x" * 24) + "';\n"
        if code == "mac_home":
            content = "const p = '" + "/" + "Users" + "/sample/private/file';\n"
        (staging / filename).write_text(content, encoding="utf-8")

    with pytest.raises(frontend_build.FrontendBuildError) as raised:
        frontend_build.validate_staging(staging)

    assert raised.value.code == code


def test_build_wrapper_allows_non_request_library_diagnostics_only(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    _write_staged_assets(staging)
    (staging / "react-core.js").write_text(
        'const svgNamespace = "http://www.w3.org/2000/svg";\n'
        'const errorDocs = "https://react.dev/errors/418";\n',
        encoding="utf-8",
    )

    frontend_build.validate_staging(staging)


def test_frontend_checks_run_lint_typecheck_and_test_in_order(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    frontend = tmp_path / "frontend"
    frontend_build.run_frontend_checks(frontend, runner=runner)

    assert calls == [
        ["pnpm", "--dir", str(frontend), "run", "lint"],
        ["pnpm", "--dir", str(frontend), "run", "typecheck"],
        ["pnpm", "--dir", str(frontend), "run", "test"],
    ]


def test_pnpm_build_uses_the_fixed_frontend_command(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    frontend = tmp_path / "frontend"
    staging = tmp_path / "staging"
    frontend_build.run_pnpm_build(frontend, staging, runner=runner)

    assert calls == [
        [
            "pnpm",
            "--dir",
            str(frontend),
            "exec",
            "vite",
            "build",
            "--outDir",
            str(staging),
            "--emptyOutDir",
            "true",
        ]
    ]


def test_missing_frontend_metadata_fails_before_build(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()

    with pytest.raises(frontend_build.FrontendBuildError) as raised:
        frontend_build.validate_frontend_metadata(frontend)

    assert raised.value.code == "missing_frontend_source"


def test_build_and_publish_runs_checks_before_fixed_vite_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frontend = tmp_path / "frontend"
    staging = tmp_path / "staging"
    assets = tmp_path / "assets"
    _write_frontend_metadata(frontend)
    assets.mkdir()
    calls: list[str] = []

    def fake_checks(_frontend: Path) -> None:
        calls.append("checks")

    def fake_build(_frontend: Path, output: Path) -> None:
        calls.append("build")
        _write_staged_assets(output)

    monkeypatch.setattr(frontend_build, "run_frontend_checks", fake_checks)
    monkeypatch.setattr(frontend_build, "run_pnpm_build", fake_build)

    frontend_build.build_and_publish(
        frontend,
        assets,
        staging_dir=staging,
        skip_build=False,
    )

    assert calls == ["checks", "build"]


def test_frontend_license_inventory_matches_lockfile_and_direct_dependencies() -> None:
    frontend = ROOT / "frontend"
    package = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    inventory = json.loads(
        (frontend / "THIRD_PARTY_LICENSES.json").read_text(encoding="utf-8")
    )
    lockfile = (frontend / "pnpm-lock.yaml").read_bytes()

    assert inventory["lockfile"]["sha256"] == sha256(lockfile).hexdigest()

    runtime_deps = package["dependencies"]
    development_deps = package["devDependencies"]
    rows = {item["name"]: item for item in inventory["dependencies"]}
    assert set(rows) == set(runtime_deps) | set(development_deps)
    assert inventory["directDependencyCount"] == len(rows)
    assert inventory["runtimeDependencyCount"] == len(runtime_deps)

    for name, specifier in {**runtime_deps, **development_deps}.items():
        assert rows[name]["specifier"] == specifier

    for name in runtime_deps:
        assert rows[name]["scope"] == "runtime"
        assert rows[name]["license"].strip()
