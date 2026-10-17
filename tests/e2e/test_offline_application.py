from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from nining.application import GenerationService
from nining.cli import main
from nining.config import load_config
from nining.harness import replay_records
from nining.loader import load_materials
from nining.providers import (
    DeterministicTextAdapter,
    MockAdapter,
    ProviderError,
    ProviderErrorKind,
    ProviderRouter,
)
from nining.session import ApplicationSession
from nining.storage import FileStorage, StorageConflictError
from nining.storage.protocol import StorageBackend
from nining.trace import load_jsonl
from nining.web import LocalWebServer, RepositoryWebFacade

ROOT = Path(__file__).resolve().parents[2]


def _write_offline_config(root: Path) -> Path:
    path = root / "offline.toml"
    path.write_text(
        """
[runtime]
mode = "offline"
allow_network = false
strict_provenance = true
output_root = "outputs"

[storage]
backend = "file"
file_root = "state"
fallback_backend = "file"

[playthrough]
autosave = true
checkpoint_on = ["trigger", "pre_generation", "post_generation"]

[models.text]
provider = "deterministic"

[models.image]
provider = "placeholder"
output_format = "png"

[models.voice]
provider = "prompt_only"
enabled = false

[logging]
directory = "logs"
save_raw_provider_responses = false

[testing]
fixture_root = "fixtures"
block_network_by_default = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode() if payload is not None else None
    request_headers = {"Content-Type": "application/json"} if data is not None else {}
    request_headers.update(headers or {})
    request = Request(url, method=method, data=data, headers=request_headers)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_offline_generation_trace_web_and_restart(tmp_path: Path) -> None:
    config = load_config(_write_offline_config(tmp_path))
    materials = load_materials(ROOT / "materials" / "sample")
    storage = FileStorage(tmp_path / "state")
    backend = cast(StorageBackend, storage)
    service = GenerationService(config=config, materials=materials, storage=backend)

    t1 = service.generate("T1")
    assert t1.status == "completed"
    assert t1.final_eligible is False
    assert "placeholder" in t1.degraded_reasons[0]
    assert len(t1.media_paths) == 1
    placeholder = tmp_path / "outputs" / t1.media_paths[0]
    assert placeholder.read_bytes().startswith(b"\x89PNG")

    run = storage.get_run(t1.run_id)
    assert run["status"] == "completed"
    calls = storage.list_provider_calls(t1.playthrough_id)
    assert [item["provider"] for item in calls] == [
        "deterministic",
        "placeholder",
    ]
    assert all("seed" in item["parameters"] for item in calls)
    assert all(item["cost_amount_micros"] == 0 for item in calls)
    durable_calls = [
        json.loads(line)
        for line in (tmp_path / "logs" / "provider_calls.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [item["provider"] for item in durable_calls] == [
        "deterministic",
        "placeholder",
    ]
    assert "AUTHORITATIVE BRIEF" not in json.dumps(
        durable_calls, ensure_ascii=False
    )
    assets = storage.list_assets(t1.playthrough_id)
    media_asset = next(item for item in assets if item["media_type"] == "image")
    assert media_asset["sha256"] == sha256(placeholder.read_bytes()).hexdigest()

    trace_path = tmp_path / "logs" / "runs" / f"{t1.run_id}.jsonl"
    records = load_jsonl(trace_path)
    run_ids = {str(item["run_id"]) for item in records}
    assert run_ids == {f"{t1.run_id}.prompt", f"{t1.run_id}.media"}
    for trace_run_id in run_ids:
        summary = replay_records(
            [item for item in records if item["run_id"] == trace_run_id]
        )
        assert summary.stop_reason == "completed"
        assert summary.attempts == 1

    facade = RepositoryWebFacade(
        materials=materials,
        config=config,
        manager=service.manager,
        storage=backend,
        playthrough_id=t1.playthrough_id,
        asset_root=service.output_root,
        generation_provider=lambda trigger_id, checkpoint_id: service.generate(
            trigger_id,
            playthrough_id=t1.playthrough_id,
            expected_checkpoint_id=checkpoint_id,
        ).to_dict(),
    )
    with LocalWebServer(facade) as server:
        status, dashboard = _http_json(
            f"{server.url}api/v1/dashboard?view=story&trigger=T1"
        )
        assert status == 200
        assert dashboard["controls"]["generation_available"] is True
        assert dashboard["assets"][0]["preview_available"] is True
        checkpoint_id = dashboard["controls"]["head"]["checkpoint_id"]
        action_headers = {
            "X-Nining-Action-Token": server.action_token,
            "Origin": server.url.rstrip("/"),
        }

        status, generated = _http_json(
            f"{server.url}api/v1/actions/generate",
            method="POST",
            payload={
                "trigger_id": "T5",
                "expected_checkpoint_id": checkpoint_id,
                "operation_id": "op-generate-t5",
            },
            headers=action_headers,
        )
        assert status == 200
        assert generated["result"]["kind"] == "voice"
        assert generated["result"]["final_eligible"] is False

        status, stale = _http_json(
            f"{server.url}api/v1/actions/generate",
            method="POST",
            payload={
                "trigger_id": "T3",
                "expected_checkpoint_id": checkpoint_id,
                "operation_id": "op-generate-stale",
            },
            headers=action_headers,
        )
        assert status == 409
        assert "refresh" in stale["error"]["message"]

    playthrough_id = t1.playthrough_id
    storage.close()
    reopened = FileStorage(tmp_path / "state", read_only=True)
    try:
        assert reopened.get_playthrough(playthrough_id)["status"] == "active"
        assert len(reopened.list_runs(playthrough_id)) == 2
        assert len(reopened.list_provider_calls(playthrough_id)) == 4
        assert reopened.integrity_check()["ok"] is True
    finally:
        reopened.close()


def test_voice_chapter_can_generate_a_separate_review_gated_background(
    tmp_path: Path,
) -> None:
    config = load_config(_write_offline_config(tmp_path))
    materials = load_materials(ROOT / "materials" / "sample")
    storage = FileStorage(tmp_path / "state")
    backend = cast(StorageBackend, storage)
    try:
        service = GenerationService(config=config, materials=materials, storage=backend)
        outcome = service.generate_background("T2")

        assert outcome.kind == "image"
        assert outcome.trigger_id == "T2"
        assert outcome.final_eligible is False
        spec = json.loads((tmp_path / "outputs" / outcome.spec_path).read_text(encoding="utf-8"))
        assert spec["kind"] == "image"
        assert spec["generation"]["background_for_primary_kind"] == "voice"
        assert "Chapter background image" in spec["intent"]
        images = [
            asset
            for asset in storage.list_assets(outcome.playthrough_id)
            if asset["trigger_id"] == "T2" and asset["media_type"] == "image"
        ]
        assert len(images) == 1
    finally:
        storage.close()


def test_generate_cli_anchors_relative_paths_to_config(tmp_path: Path) -> None:
    config_path = _write_offline_config(tmp_path)
    output = StringIO()
    with redirect_stdout(output):
        exit_code = main(
            [
                "generate",
                "--config",
                str(config_path),
                "--materials",
                str(ROOT / "materials" / "sample"),
                "--trigger",
                "T1",
            ]
        )

    payload = json.loads(output.getvalue())
    assert exit_code == 0
    assert payload["status"] == "ok"
    result = payload["result"]
    assert (tmp_path / "outputs" / result["spec_path"]).is_file()
    assert (tmp_path / "outputs" / result["media_paths"][0]).is_file()
    assert (tmp_path / "state" / "format.json").is_file()
    assert (tmp_path / "logs" / "runs" / f"{result['run_id']}.jsonl").is_file()


def test_visual_reference_cli_degrades_to_non_final_placeholder(
    tmp_path: Path,
) -> None:
    config_path = _write_offline_config(tmp_path)
    output = StringIO()
    with redirect_stdout(output):
        exit_code = main(
            [
                "generate-reference",
                "--config",
                str(config_path),
                "--materials",
                str(ROOT / "materials" / "sample"),
            ]
        )

    payload = json.loads(output.getvalue())
    result = payload["result"]
    assert exit_code == 0
    assert result["trigger_id"] == "REFERENCE_VISUAL"
    assert result["final_eligible"] is False
    assert "placeholder" in result["degraded_reasons"][0]
    assert (tmp_path / "outputs" / result["media_paths"][0]).is_file()


def test_failed_provider_run_is_terminal_and_recoverable_from_history(
    tmp_path: Path,
) -> None:
    config = load_config(_write_offline_config(tmp_path))
    materials = load_materials(ROOT / "materials" / "sample")
    storage = FileStorage(tmp_path / "state")
    backend = cast(StorageBackend, storage)
    router = ProviderRouter(
        text_adapters=(DeterministicTextAdapter(),),
        image_adapters=(
            MockAdapter(
                "image",
                outcomes=(
                    ProviderError(
                        ProviderErrorKind.VALIDATION,
                        "fixture_failure",
                        "safe fixture failure",
                    ),
                ),
            ),
        ),
    )
    service = GenerationService(
        config=config,
        materials=materials,
        storage=backend,
        output_root=tmp_path / "outputs",
        trace_root=tmp_path / "logs" / "runs",
        router=router,
    )
    playthrough_id = service.ensure_playthrough()
    try:
        with pytest.raises(RuntimeError, match="safe fixture failure"):
            service.generate("T1", playthrough_id=playthrough_id)
        run = storage.list_runs(playthrough_id)[-1]
        assert run["status"] == "failed"
        assert "safe fixture failure" in run["safe_error"]
        history = service.manager.history(playthrough_id)
        assert history[-1]["event_type"] == "generation_failed"
        assert history[-1]["payload"]["trace_sha256"]
        checkpoint = storage.get_checkpoint(
            playthrough_id, storage.get_head(playthrough_id)["checkpoint_id"]
        )
        assert checkpoint["state"]["cursor"]["phase"] == "generation_failed"
    finally:
        storage.close()


def test_unresolved_run_blocks_a_second_provider_dispatch(tmp_path: Path) -> None:
    config = load_config(_write_offline_config(tmp_path))
    materials = load_materials(ROOT / "materials" / "sample")
    storage = FileStorage(tmp_path / "state")
    backend = cast(StorageBackend, storage)
    service = GenerationService(
        config=config,
        materials=materials,
        storage=backend,
        output_root=tmp_path / "outputs",
        trace_root=tmp_path / "logs" / "runs",
    )
    playthrough_id = service.ensure_playthrough()
    head = storage.get_head(playthrough_id)
    storage.begin_run(
        playthrough_id=playthrough_id,
        branch_id=head["branch_id"],
        mode="offline",
        requested_storage="file",
        effective_storage="file",
        materials_manifest_sha256=materials.manifest["sha256"],
        config_sha256=config.fingerprint({}),
    )
    try:
        with pytest.raises(StorageConflictError, match="unresolved"):
            service.generate("T1", playthrough_id=playthrough_id)
        assert storage.list_provider_calls(playthrough_id) == []
        assert len(storage.list_runs(playthrough_id)) == 1
    finally:
        storage.close()


def test_failed_visual_reference_records_event_decision_and_checkpoint(
    tmp_path: Path,
) -> None:
    config = load_config(_write_offline_config(tmp_path))
    materials = load_materials(ROOT / "materials" / "sample")
    storage = FileStorage(tmp_path / "state")
    backend = cast(StorageBackend, storage)
    router = ProviderRouter(
        text_adapters=(DeterministicTextAdapter(),),
        image_adapters=(
            MockAdapter(
                "image",
                outcomes=(
                    ProviderError(
                        ProviderErrorKind.TIMEOUT,
                        "fixture_timeout",
                        "safe fixture timeout",
                    ),
                ),
            ),
        ),
    )
    service = GenerationService(
        config=config,
        materials=materials,
        storage=backend,
        output_root=tmp_path / "outputs",
        trace_root=tmp_path / "logs" / "runs",
        call_log_path=tmp_path / "logs" / "provider_calls.jsonl",
        router=router,
    )
    playthrough_id = service.ensure_playthrough()
    try:
        with pytest.raises(RuntimeError, match="safe fixture timeout"):
            service.generate_visual_reference(playthrough_id=playthrough_id)
        run = storage.list_runs(playthrough_id)[-1]
        assert run["status"] == "failed"
        history = service.manager.history(playthrough_id)
        assert history[-1]["event_type"] == "visual_reference_failed"
        decisions = storage.list_decisions(playthrough_id)
        assert decisions[-1]["subject_id"] == run["run_id"]
        assert decisions[-1]["action"] == "block"
        checkpoint = storage.get_checkpoint(
            playthrough_id, storage.get_head(playthrough_id)["checkpoint_id"]
        )
        assert checkpoint["state"]["extra"]["trace_sha256"]
    finally:
        storage.close()


def test_application_session_restores_checkpoint_position(tmp_path: Path) -> None:
    config_path = _write_offline_config(tmp_path)
    materials_path = ROOT / "materials" / "sample"
    with ApplicationSession.open(
        config_path=config_path, materials_root=materials_path
    ) as session:
        initial = session.position.current(session.playthrough_id)
        session.position.move(
            session.playthrough_id,
            trigger_id="T2",
            expected_checkpoint_id=str(initial["checkpoint_id"]),
        )
        playthrough_id = session.playthrough_id

    with ApplicationSession.open(
        config_path=config_path,
        materials_root=materials_path,
        playthrough_id=playthrough_id,
    ) as reopened:
        assert reopened.position.current(playthrough_id)["trigger_id"] == "T2"
        assert reopened.facade.dashboard(view_mode=None)["selected_trigger_id"] == "T2"


def test_serve_cli_black_box_readiness_action_and_clean_shutdown(
    tmp_path: Path,
) -> None:
    config_path = _write_offline_config(tmp_path)
    command = [
        sys.executable,
        "-m",
        "nining",
        "serve",
        "--config",
        str(config_path),
        "--materials",
        str(ROOT / "materials" / "sample"),
        "--port",
        "0",
    ]
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    try:
        ready = json.loads(process.stdout.readline())
        assert ready["status"] == "ready"
        assert ready["url"].startswith("http://127.0.0.1:")
        assert ready["network_allowed"] is False
        assert ready["storage_backend"] == "file"
        assert ready["checkpoint_id"]
        assert "--playthrough" in ready["resume"]

        with urlopen(ready["url"], timeout=5) as response:
            html = response.read().decode("utf-8")
        match = re.search(
            r'name="nining-action-token" content="([A-Za-z0-9_-]+)"', html
        )
        assert match is not None
        token = match.group(1)
        status, advanced = _http_json(
            f"{ready['url']}api/v1/actions/position",
            method="POST",
            payload={
                "trigger_id": "T2",
                "view_mode": "story",
                "expected_checkpoint_id": ready["checkpoint_id"],
                "operation_id": "op-black-box-position",
            },
            headers={
                "X-Nining-Action-Token": token,
                "Origin": ready["url"].rstrip("/"),
            },
        )
        assert status == 200
        assert advanced["action"] == "position"
        assert advanced["result"]["position"]["trigger_id"] == "T2"

        status, replayed = _http_json(
            f"{ready['url']}api/v1/actions/position",
            method="POST",
            payload={
                "trigger_id": "T2",
                "view_mode": "story",
                "expected_checkpoint_id": ready["checkpoint_id"],
                "operation_id": "op-black-box-position",
            },
            headers={
                "X-Nining-Action-Token": token,
                "Origin": ready["url"].rstrip("/"),
            },
        )
        assert status == 409
        assert "already completed" in replayed["error"]["message"]

        status, stale = _http_json(
            f"{ready['url']}api/v1/actions/position",
            method="POST",
            payload={
                "trigger_id": "T2",
                "view_mode": "story",
                "expected_checkpoint_id": ready["checkpoint_id"],
                "operation_id": "op-black-box-stale-position",
            },
            headers={
                "X-Nining-Action-Token": token,
                "Origin": ready["url"].rstrip("/"),
            },
        )
        assert status == 409
        assert "refresh" in stale["error"]["message"].lower()
    finally:
        process.send_signal(signal.SIGINT)
        _stdout, stderr = process.communicate(timeout=8)
    assert process.returncode == 0, stderr

    with ApplicationSession.open(
        config_path=config_path,
        materials_root=ROOT / "materials" / "sample",
        playthrough_id=ready["playthrough_id"],
    ) as reopened:
        assert reopened.position.current(ready["playthrough_id"])["trigger_id"] == "T2"


def test_hybrid_cli_starts_without_credentials_and_makes_no_provider_call(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.hybrid.toml"
    config_path.write_text(
        (ROOT / "config.creator.example.toml")
        .read_text(encoding="utf-8")
        .replace('/usr/bin/say', str(tmp_path / "missing-say")),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "nining",
        "serve",
        "--config",
        str(config_path),
        "--materials",
        str(ROOT / "materials" / "sample"),
        "--port",
        "0",
    ]
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in (
        "AZURE_TEXT_ENDPOINT",
        "AZURE_TEXT_API_KEY",
        "AZURE_TEXT_MODEL",
        "AZURE_IMAGE_ENDPOINT",
        "AZURE_IMAGE_API_KEY",
        "AZURE_IMAGE_MODEL",
        "DEEPSEEK_ENDPOINT",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_TEXT_MODEL",
    ):
        env.pop(name, None)

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    try:
        ready = json.loads(process.stdout.readline())
        assert ready["status"] == "ready"
        assert ready["mode"] == "hybrid"
        assert ready["network_allowed"] is True
        assert ready["capabilities"]["text"]["provider"] == "deterministic"
        assert ready["capabilities"]["image"]["provider"] == "placeholder"
        assert ready["capabilities"]["voice"]["provider"] == "prompt_only"

        with urlopen(f"{ready['url']}api/v1/capabilities", timeout=5) as response:
            capabilities = json.load(response)
        assert capabilities["text"]["provider"] == "deterministic"
        assert capabilities["image"]["provider"] == "placeholder"
        assert capabilities["voice"]["provider"] == "prompt_only"
        assert not (tmp_path / "logs" / "provider_calls.jsonl").exists()
    finally:
        process.send_signal(signal.SIGINT)
        _stdout, stderr = process.communicate(timeout=8)
    assert process.returncode == 0, stderr
