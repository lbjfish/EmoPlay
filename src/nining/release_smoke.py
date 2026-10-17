"""Offline application smoke test used by the final release gate."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import socket
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
from http import HTTPStatus
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .cli import main as cli_main
from .session import ApplicationSession
from .web import LocalWebServer

_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"/Users/[^\s:'\"]+"),
    re.compile(r"/home/[^\s:'\"]+"),
    re.compile(r"/private/var/[^\s:'\"]+"),
    re.compile(r"/var/folders/[^\s:'\"]+"),
    re.compile(r"/tmp/[^\s:'\"]+"),
    re.compile(r"(?i)[A-Z]:\\Users\\[^\s:'\"]+"),
)
_SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "<redacted>"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}"), "Bearer <redacted>"),
    (
        re.compile(
            r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[\"']?[A-Za-z0-9._~+/-]{12,}"
        ),
        r"\1=<redacted>",
    ),
)


@dataclass(frozen=True)
class SmokeStep:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class OfflineAppSmokeResult:
    ok: bool
    status: str
    steps: tuple[SmokeStep, ...]
    detail: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "status": self.status,
            "detail": _sanitize(self.detail),
            "steps": [
                {
                    **asdict(item),
                    "detail": _sanitize(item.detail),
                }
                for item in self.steps
            ],
        }


def _sanitize(text: object, *, root: Path | None = None, temp_root: Path | None = None) -> str:
    value = str(text)
    for path, replacement in ((root, "<repo>"), (temp_root, "<temp>")):
        if path is not None:
            value = value.replace(str(path), replacement)
    for pattern in _ABSOLUTE_PATH_PATTERNS:
        value = pattern.sub("<path>", value)
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _write_offline_config(root: Path) -> Path:
    path = root / "config.offline.toml"
    path.write_text(
        """
[runtime]
mode = "offline"
allow_network = false
strict_provenance = true
output_root = "outputs"

[storage]
backend = "file"
file_root = "state/files"
fallback_backend = "file"

[playthrough]
autosave = true
checkpoint_on = ["trigger", "pre_generation", "post_generation", "human_decision"]
rewind_continue_policy = "create_branch"
preserve_provider_calls_on_rewind = true

[models.text]
provider = "deterministic"

[models.image]
provider = "placeholder"
output_format = "png"
final_eligible = false

[models.voice]
provider = "prompt_only"
enabled = false
final_eligible = false

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


def _copy_sample_materials(repo_root: Path, temp_root: Path) -> Path:
    source = repo_root / "materials" / "sample"
    if not source.is_dir():
        raise RuntimeError("sample materials are unavailable")
    destination = temp_root / "materials" / "sample"
    shutil.copytree(source, destination)
    return destination


def _cli_json(args: list[str]) -> Any:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli_main(args)
    output = stdout.getvalue().strip()
    if code != 0:
        diagnostic = stderr.getvalue().strip() or output or f"exit {code}"
        raise RuntimeError(f"{args[0]} failed: {diagnostic}")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{args[0]} emitted invalid JSON") from exc


def _is_loopback(host: object) -> bool:
    if not isinstance(host, str):
        return False
    if host == "localhost":
        return True
    try:
        return socket.inet_pton(socket.AF_INET, host).startswith(b"\x7f")
    except OSError:
        pass
    try:
        return socket.inet_pton(socket.AF_INET6, host) == socket.inet_pton(
            socket.AF_INET6, "::1"
        )
    except OSError:
        return False


@contextmanager
def _loopback_only_network() -> Iterator[None]:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address:
            host = address[0]
            if not _is_loopback(host):
                raise PermissionError("offline smoke blocked non-loopback network")
        return original_connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        if isinstance(address, tuple) and address:
            host = address[0]
            if not _is_loopback(host):
                return errno.EPERM
        return original_connect_ex(sock, address)

    socket.socket.connect = guarded_connect  # type: ignore[assignment, method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[assignment, method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[assignment, method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[assignment, method-assign]


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Content-Type": "application/json"} if data is not None else {}
    request_headers.update(headers or {})
    request = Request(url, method=method, data=data, headers=request_headers)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def _http_bytes(url: str) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _scrubbed_environment() -> Callable[[], None]:
    original = os.environ.copy()
    for key in tuple(os.environ):
        lowered = key.lower()
        if any(part in lowered for part in ("key", "token", "secret", "password")):
            os.environ.pop(key, None)

    def restore() -> None:
        os.environ.clear()
        os.environ.update(original)

    return restore


def run_offline_app_smoke(root: Path) -> OfflineAppSmokeResult:
    """Run a real no-provider, loopback-only app smoke in a temporary workspace."""

    repo_root = root.resolve()
    steps: list[SmokeStep] = []
    current_step = "prepare"

    def passed(name: str, detail: str) -> None:
        steps.append(SmokeStep(name=name, status="passed", detail=detail))

    try:
        with tempfile.TemporaryDirectory(prefix="nining-offline-smoke-") as directory:
            temp_root = Path(directory)
            restore_environment = _scrubbed_environment()
            try:
                current_step = "prepare"
                materials = _copy_sample_materials(repo_root, temp_root)
                config_path = _write_offline_config(temp_root)
                output_root = temp_root / "outputs"
                passed("prepare", "temporary offline config and sample materials ready")

                with _loopback_only_network():
                    current_step = "validate_config"
                    config_status = _cli_json(
                        ["validate-config", "--config", str(config_path)]
                    )
                    _expect(config_status.get("status") == "ok", "config was not valid")
                    _expect(config_status.get("mode") == "offline", "config is not offline")
                    passed("validate_config", "offline file config parsed")

                    current_step = "capabilities"
                    capability_status = _cli_json(
                        ["capabilities", "--config", str(config_path)]
                    )
                    capabilities = capability_status.get("capabilities")
                    _expect(isinstance(capabilities, dict), "capabilities missing")
                    image = capabilities.get("image")
                    _expect(isinstance(image, dict), "image capability missing")
                    _expect(image.get("provider") == "placeholder", "image provider is not placeholder")
                    _expect(image.get("final_eligible") is False, "placeholder is final eligible")
                    passed("capabilities", "static capabilities are offline/degraded")

                    current_step = "spec_all"
                    specs = _cli_json(
                        [
                            "spec",
                            "--materials",
                            str(materials),
                            "--all",
                            "--output-dir",
                            str(temp_root / "specs"),
                        ]
                    )
                    if not isinstance(specs, list) or len(specs) != 6:
                        raise RuntimeError("expected six specs")
                    trigger_ids: list[object] = []
                    for item in specs:
                        if not isinstance(item, Mapping):
                            raise TypeError("spec payload is not an object")
                        trigger_ids.append(item.get("trigger_id"))
                    _expect(
                        trigger_ids == [f"T{index}" for index in range(1, 7)],
                        "unexpected trigger set",
                    )
                    passed("spec_all", "all six specs compiled from the sample copy")

                    current_step = "generate_t1"
                    generated = _cli_json(
                        [
                            "generate",
                            "--config",
                            str(config_path),
                            "--materials",
                            str(materials),
                            "--trigger",
                            "T1",
                            "--output-root",
                            str(output_root),
                        ]
                    )
                    result = (
                        generated.get("result") if isinstance(generated, dict) else None
                    )
                    if not isinstance(result, dict):
                        raise TypeError("generate result missing")
                    result_payload = cast(dict[str, Any], result)
                    _expect(
                        result_payload.get("trigger_id") == "T1",
                        "wrong generated trigger",
                    )
                    _expect(
                        result_payload.get("final_eligible") is False,
                        "placeholder result is final eligible",
                    )
                    _expect(
                        any(
                            "placeholder" in str(item)
                            for item in result_payload.get("degraded_reasons", [])
                        ),
                        "placeholder degradation was not recorded",
                    )
                    media_paths = result_payload.get("media_paths")
                    if not isinstance(media_paths, list) or len(media_paths) != 1:
                        raise TypeError("media path missing")
                    media_relative = media_paths[0]
                    if not isinstance(media_relative, str):
                        raise TypeError("media path missing")
                    _expect(
                        (output_root / media_relative).is_file(),
                        "placeholder media missing",
                    )
                    playthrough_id = result_payload.get("playthrough_id")
                    if not isinstance(playthrough_id, str):
                        raise TypeError("playthrough id missing")
                    passed("generate_t1", "T1 generated a non-final placeholder")

                    current_step = "local_web"
                    with ApplicationSession.open(
                        config_path=config_path,
                        materials_root=materials,
                        playthrough_id=playthrough_id,
                        output_root=output_root,
                    ) as session, LocalWebServer(session.facade) as server:
                        _expect(server.host == "127.0.0.1", "server is not loopback")

                        current_step = "browser_shell"
                        status, headers, shell = _http_bytes(server.url)
                        _expect(status == HTTPStatus.OK, "browser shell request failed")
                        _expect(
                            b"__NINING_ACTION_TOKEN__" not in shell,
                            "browser shell retained token placeholder",
                        )
                        _expect(
                            server.action_token.encode("ascii") in shell,
                            "browser shell action capability missing",
                        )
                        _expect(
                            b'/assets/game.js' in shell and b'/assets/game.css' in shell,
                            "browser shell does not reference the React game bundle",
                        )
                        _expect(
                            b'/assets/app.js' not in shell and b'/assets/app.css' not in shell,
                            "browser shell still references the legacy bundle",
                        )
                        csp = headers.get("Content-Security-Policy", "")
                        _expect("script-src 'self'" in csp, "browser shell CSP is incomplete")
                        _expect(
                            "script-src 'unsafe-inline'" not in csp,
                            "browser shell allows inline scripts",
                        )
                        for asset in (
                            "game.css",
                            "react-core.js",
                            "game.js",
                            "effects.js",
                            "media-review.js",
                            "god-charts.js",
                        ):
                            asset_status, asset_headers, asset_body = _http_bytes(
                                f"{server.url}assets/{asset}"
                            )
                            _expect(
                                asset_status == HTTPStatus.OK and bool(asset_body),
                                f"browser asset unavailable: {asset}",
                            )
                            _expect(
                                asset_headers.get("Cache-Control") == "no-store",
                                f"browser asset cache policy missing: {asset}",
                            )
                        passed(
                            "browser_shell",
                            "React shell and every allowlisted static chunk are locally available",
                        )

                        current_step = "local_web"
                        status, dashboard = _http_json(
                            f"{server.url}api/v1/dashboard?view=story&trigger=T1"
                        )
                        _expect(status == HTTPStatus.OK, "dashboard request failed")
                        controls = dashboard.get("controls")
                        if not isinstance(controls, dict):
                            raise TypeError("dashboard controls missing")
                        head = controls.get("head")
                        if not isinstance(head, dict):
                            raise TypeError("dashboard head missing")
                        checkpoint_id = head.get("checkpoint_id")
                        if not isinstance(checkpoint_id, str):
                            raise TypeError("checkpoint id missing")
                        passed("local_web", "loopback dashboard returned current controls")

                        current_step = "position_action"
                        action_headers = {
                            "X-Nining-Action-Token": server.action_token,
                            "Origin": server.url.rstrip("/"),
                        }
                        status, moved = _http_json(
                            f"{server.url}api/v1/actions/position",
                            method="POST",
                            payload={
                                "trigger_id": "T2",
                                "view_mode": "story",
                                "expected_checkpoint_id": checkpoint_id,
                                "operation_id": "release-smoke-position",
                            },
                            headers=action_headers,
                        )
                        _expect(status == HTTPStatus.OK, "position action failed")
                        moved_result = moved.get("result")
                        if not isinstance(moved_result, dict):
                            raise TypeError("position result missing")
                        moved_position = moved_result.get("position")
                        if not isinstance(moved_position, dict):
                            raise TypeError("position payload missing")
                        _expect(
                            moved_position.get("trigger_id") == "T2",
                            "position did not advance",
                        )
                        passed("position_action", "authenticated mutation advanced to T2")

                    current_step = "reopen_storage"
                    with ApplicationSession.open(
                        config_path=config_path,
                        materials_root=materials,
                        playthrough_id=playthrough_id,
                        output_root=output_root,
                    ) as reopened:
                        position = reopened.position.current(playthrough_id)
                        _expect(position.get("trigger_id") == "T2", "position did not persist")
                        _expect(reopened.storage.integrity_check().get("ok") is True, "storage integrity failed")
                    passed("reopen_storage", "file storage restored the mutated position")
            finally:
                restore_environment()
    except Exception as exc:  # noqa: BLE001 - smoke failures become release findings.
        detail = _sanitize(f"{current_step}: {type(exc).__name__}: {exc}", root=repo_root)
        steps.append(SmokeStep(name=current_step, status="failed", detail=detail))
        return OfflineAppSmokeResult(
            ok=False,
            status="failed",
            steps=tuple(steps),
            detail=detail,
        )

    return OfflineAppSmokeResult(
        ok=True,
        status="passed",
        steps=tuple(steps),
        detail="offline app smoke passed",
    )
