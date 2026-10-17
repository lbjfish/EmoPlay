from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Self, cast
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import pytest

import nining.web.server as web_server
from nining.config import load_config
from nining.loader import load_materials
from nining.playthrough import PlaythroughManager
from nining.position import StoryPositionService
from nining.review import ReviewService
from nining.storage import MemoryStorage
from nining.storage.protocol import StorageBackend
from nining.web import LocalWebServer, RepositoryWebFacade

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist"
PUBLISHED_ASSETS = ROOT / "src" / "nining" / "web" / "assets"
HASH_A = "a" * 64
REQUIRED_REACT_ASSETS = {
    "index.html",
    "game.css",
    "react-core.js",
    "game.js",
    "effects.js",
    "media-review.js",
    "god-charts.js",
}


def _chrome_path() -> str | None:
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _fixed_bundle_ready(asset_root: Path) -> bool:
    return asset_root.is_dir() and all(
        (asset_root / name).is_file() for name in REQUIRED_REACT_ASSETS
    )


def _started_facade(tmp_path: Path) -> tuple[RepositoryWebFacade, MemoryStorage]:
    materials = load_materials(ROOT / "materials" / "sample")
    config = load_config(ROOT / "config.offline.toml")
    storage = MemoryStorage()
    backend = cast(StorageBackend, storage)
    manager = PlaythroughManager(backend)
    started = manager.start(
        title="react browser shell test",
        materials_manifest_sha256=materials.manifest["sha256"],
    )
    playthrough_id = started["playthrough"]["playthrough_id"]
    run = storage.begin_run(
        playthrough_id=playthrough_id,
        branch_id=started["branch"]["branch_id"],
        mode="mock",
        requested_storage="memory",
        effective_storage="memory",
        materials_manifest_sha256=materials.manifest["sha256"],
        config_sha256=HASH_A,
    )
    media_root = tmp_path / "media"
    media_root.mkdir()
    content = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    media_path = media_root / "placeholder.png"
    media_path.write_bytes(content)
    registered_assets = [storage.register_asset(
        run_id=run["run_id"],
        role="candidate",
        media_type="image",
        relative_path=media_path.name,
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
        provenance="manual",
        final_eligible=True,
        trigger_id=materials.triggers[0].trigger_id,
        mime_type="image/png",
    )]
    for trigger in materials.triggers[1:]:
        chapter_path = media_root / f"{trigger.trigger_id}.png"
        chapter_path.write_bytes(content)
        registered_assets.append(storage.register_asset(
            run_id=run["run_id"],
            role="candidate",
            media_type="image",
            relative_path=chapter_path.name,
            size_bytes=len(content),
            sha256=sha256(content).hexdigest(),
            provenance="manual",
            final_eligible=True,
            trigger_id=trigger.trigger_id,
            mime_type="image/png",
        ))
    review = ReviewService(storage=backend, manager=manager, asset_root=media_root)
    for asset in registered_assets:
        review.review(
            playthrough_id,
            asset_id=str(asset["asset_id"]),
            action="accept",
            reason="real-browser fixture media",
            expected_sha256=str(asset["sha256"]),
        )
    position = StoryPositionService(
        materials=materials,
        storage=backend,
        manager=manager,
    )
    return (
        RepositoryWebFacade(
            materials=materials,
            config=config,
            manager=manager,
            storage=backend,
            playthrough_id=playthrough_id,
            asset_root=media_root,
            position_provider=lambda: position.current(playthrough_id),
            position_action=lambda trigger_id, view_mode, expected_checkpoint_id: position.move(
                playthrough_id,
                trigger_id=trigger_id,
                view_mode=view_mode,
                expected_checkpoint_id=expected_checkpoint_id,
            ),
            review_provider=lambda asset_id, action, reason, expected_sha256: review.review(
                playthrough_id,
                asset_id=asset_id,
                action=action,
                reason=reason,
                expected_sha256=expected_sha256,
            ),
        ),
        storage,
    )


class ChromeProcess:
    def __init__(self, executable: str, user_data_dir: Path) -> None:
        self.user_data_dir = user_data_dir
        self.process = subprocess.Popen(
            [
                executable,
                "--headless=new",
                "--autoplay-policy=no-user-gesture-required",
                "--no-sandbox",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-features=MediaRouter,OptimizationHints,Translate",
                "--disable-gpu",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-default-browser-check",
                "--no-first-run",
                "--remote-debugging-port=0",
                "--window-size=1366,900",
                f"--user-data-dir={user_data_dir}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.port = self._wait_for_port()

    def _wait_for_port(self) -> int:
        active_port = self.user_data_dir / "DevToolsActivePort"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                pytest.skip("Chrome exited before exposing the DevTools port.")
            if active_port.is_file():
                lines = active_port.read_text(encoding="utf-8").splitlines()
                if lines:
                    return int(lines[0])
            time.sleep(0.05)
        self.close()
        pytest.skip("Chrome did not expose a DevTools port in time.")

    def new_page_websocket(self) -> str:
        url = f"http://127.0.0.1:{self.port}/json/new?{quote('about:blank')}"
        request = Request(url, method="PUT")
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        websocket_url = payload.get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            raise TypeError("Chrome did not return a page websocket URL.")
        return websocket_url

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class CdpClient:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self.sock = self._connect(websocket_url)
        self.next_id = 0
        self.console_errors: list[str] = []
        self.page_exceptions: list[str] = []
        self.log_errors: list[str] = []
        self.requests: list[str] = []
        self.http_errors: list[str] = []

    @staticmethod
    def _connect(websocket_url: str) -> socket.socket:
        parsed = urlsplit(websocket_url)
        if parsed.hostname is None or parsed.port is None:
            raise AssertionError("Invalid Chrome websocket URL.")
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise AssertionError("Chrome closed the websocket handshake.")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise AssertionError("Chrome rejected the websocket handshake.")
        return sock

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _send_text(self, payload: dict[str, Any], *, opcode: int = 0x1) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = bytearray([0x80 | opcode])
        length = len(raw)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
        self.sock.sendall(bytes(header) + masked)

    def _send_pong(self, data: bytes) -> None:
        header = bytearray([0x8A])
        length = len(data)
        header.append(0x80 | length)
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self.sock.recv(length - len(chunks))
            if not chunk:
                raise AssertionError("Chrome closed the websocket.")
            chunks.extend(chunk)
        return bytes(chunks)

    def _recv_message(self, timeout: float = 5) -> dict[str, Any]:
        self.sock.settimeout(timeout)
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            masked = bool(second & 0x80)
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(length) if length else b""
            if masked:
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
            if opcode == 0x8:
                raise AssertionError("Chrome closed the websocket.")
            if opcode == 0x9:
                self._send_pong(data)
                continue
            if opcode != 0x1:
                continue
            return json.loads(data.decode("utf-8"))

    def command(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 5
    ) -> dict[str, Any]:
        self.next_id += 1
        command_id = self.next_id
        payload: dict[str, Any] = {"id": command_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send_text(payload)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._recv_message(timeout=max(0.1, deadline - time.monotonic()))
            if message.get("id") == command_id:
                if "error" in message:
                    raise AssertionError(f"CDP command failed: {message['error']}")
                return message.get("result", {})
            self._record_event(message)
        raise AssertionError(f"Timed out waiting for CDP command {method}.")

    def _record_event(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            url = request.get("url")
            if isinstance(url, str):
                self.requests.append(url)
        elif method == "Network.responseReceived":
            response = params.get("response", {})
            url = response.get("url")
            status = response.get("status")
            if isinstance(url, str) and isinstance(status, (int, float)) and status >= 400:
                self.http_errors.append(f"{int(status)} {url}")
        elif method == "Runtime.consoleAPICalled":
            if params.get("type") in {"error", "assert"}:
                self.console_errors.append(self._console_text(params))
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            text = details.get("text")
            self.page_exceptions.append(str(text or details))
        elif method == "Log.entryAdded":
            entry = params.get("entry", {})
            if entry.get("level") == "error":
                self.log_errors.append(str(entry.get("text", entry)))

    @staticmethod
    def _console_text(params: dict[str, Any]) -> str:
        values: list[str] = []
        for arg in params.get("args", []):
            if isinstance(arg, dict):
                if "value" in arg:
                    values.append(str(arg["value"]))
                elif "description" in arg:
                    values.append(str(arg["description"]))
        return " ".join(values) or str(params)

    def evaluate(self, expression: str) -> Any:
        result = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if "exceptionDetails" in result:
            raise AssertionError(f"Evaluation failed: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, *, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        last_value: Any = None
        while time.monotonic() < deadline:
            last_value = self.evaluate(expression)
            if last_value:
                return
            time.sleep(0.1)
        text = self.evaluate("document.body.innerText")
        raise AssertionError(
            f"Timed out waiting for expression {expression!r}; last={last_value!r}; text={text!r}"
        )


def _fatal_console_errors(client: CdpClient) -> list[str]:
    ignored = ("favicon.ico", "Failed to load resource: the server responded with a status of 404")
    errors = client.console_errors + client.page_exceptions + client.log_errors
    return [error for error in errors if not any(term in error for term in ignored)]


def _unexpected_http_errors(client: CdpClient) -> list[str]:
    return [error for error in client.http_errors if not error.endswith("/favicon.ico")]


@pytest.mark.parametrize(
    ("asset_root", "label"),
    [
        (PUBLISHED_ASSETS, "published package assets"),
        (FRONTEND_DIST, "frontend dist assets"),
    ],
    ids=["published-assets", "frontend-dist"],
)
def test_react_story_shell_renders_in_real_chrome_without_external_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, asset_root: Path, label: str
) -> None:
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Google Chrome/Chromium is not available on this system.")
    if not _fixed_bundle_ready(asset_root):
        pytest.skip(f"{label} fixed React bundle is not built yet.")

    facade, storage = _started_facade(tmp_path)
    monkeypatch.setattr(web_server, "_STATIC_DIR", asset_root)
    try:
        with LocalWebServer(facade) as local:
            origin = local.url.rstrip("/")
            with ChromeProcess(chrome, tmp_path / "chrome-profile") as browser, CdpClient(
                browser.new_page_websocket()
            ) as client:
                for method in ("Runtime.enable", "Log.enable", "Network.enable", "Page.enable"):
                    client.command(method)
                client.command("Page.navigate", {"url": local.url})
                story_ready = """
(() => {
  const text = document.body?.innerText || "";
  return text.includes("柠柠的六幕故事")
    && text.includes("第 1 章")
    && text.includes("故事进度")
    && text.includes("准备继续第 2 章")
    && text.includes("继续第 2 章")
    && text.includes("进入导演台");
})()
"""
                client.wait_for(story_ready)
                product_copy_is_clean = client.evaluate(
                    """
(() => {
  const text = document.body?.innerText || "";
  return !text.includes("场景仍需人工审核")
    && !text.includes("证据项：")
    && !text.includes("登记媒体")
    && !text.includes("[非文本消息")
    && !text.includes("书签")
    && !text.includes("切换分支")
    && text.includes("照片与声音")
    && text.includes("柠柠的此刻");
})()
"""
                )
                assert product_copy_is_clean is True

                obsolete_controls_are_absent = client.evaluate(
                    """
(() => {
  const text = document.body?.innerText || "";
  return localStorage.getItem("nining.storybook.localSave.v1") === null
    && !text.includes("保存本地书签")
    && !text.includes("保存命名书签")
    && !text.includes("导入存档")
    && !text.includes("导出存档");
})()
"""
                )
                assert obsolete_controls_are_absent is True

                continued = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("继续第 2 章"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert continued is True
                client.wait_for('document.body.innerText.includes("解锁下一章")')
                unlocked = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("解锁下一章"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert unlocked is True
                client.wait_for(
                    'document.body.innerText.includes("早安语音") && document.body.innerText.includes("准备继续第 3 章")'
                )

                atmosphere_enabled = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("开启雨滴氛围"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert atmosphere_enabled is True
                client.wait_for(
                    'document.body.innerText.includes("关闭雨滴氛围")'
                )
                client.wait_for(
                    """
(() => {
  const layer = document.querySelector('[data-testid="rain-atmosphere"]');
  return Boolean(layer)
    && Number.parseFloat(getComputedStyle(layer).opacity || "0") > 0.1
    && document.querySelector("#nining-rain-atmosphere canvas") !== null;
})()
"""
                )
                assert any(url.endswith("/assets/effects.js") for url in client.requests)

                opened = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("进入导演台"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert opened is True
                client.wait_for(
                    'document.body.innerText.includes("进入导演台？")'
                )
                confirmed = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("确认进入"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert confirmed is True
                client.wait_for(
                    """
(() => {
  const text = document.body?.innerText || "";
  return text.includes("退出导演台")
    && text.includes("导演审计抽屉")
    && text.includes("导演媒体工作台")
    && !text.includes("保存书签")
    && !text.includes("创建分支")
    && !text.includes("切换分支")
    && !text.includes("记录功能选择")
    && !text.includes("Accept · 接受")
    && !text.includes("Reject · 拒绝")
    && !text.includes("核对 SHA 后接受")
    && !text.includes("记录拒绝")
    && !text.includes("能力边界")
    && !text.includes("Provider capability");
})()
"""
                )
                assert any(url.endswith("/api/v1/assets") for url in client.requests)

                chapter_switched = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("第 3 章"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert chapter_switched is True
                client.wait_for(
                    """
(() => {
  const current = document.querySelector(".dialogue-rail > .dialogue-rail__list:not(.dialogue-rail__list--compact)");
  const text = current?.innerText || "";
  return text.includes("旺财是那只狗")
    && text.includes("凶得很")
    && !text.includes("行吧 就一眼");
})()
"""
                )

                layout_is_contained = client.evaluate(
                    """
(() => {
  const root = document.documentElement;
  const desk = document.querySelector(".director-desk");
  const stage = document.querySelector(".story-stage");
  const stageImage = document.querySelector(".story-stage__image-button img");
  if (!desk || !stage || !stageImage) return false;
  const deskRect = desk.getBoundingClientRect();
  const stageRect = stage.getBoundingClientRect();
  const imageRect = stageImage.getBoundingClientRect();
  return root.scrollWidth <= window.innerWidth + 1
    && deskRect.left >= -1
    && deskRect.right <= window.innerWidth + 1
    && imageRect.left >= stageRect.left - 1
    && imageRect.right <= stageRect.right + 1;
})()
"""
                )
                assert layout_is_contained is True

                charts_requested = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("按需加载指标"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert charts_requested is True
                client.wait_for(
                    'document.body.innerText.includes("暂无调用指标")'
                )
                assert any(url.endswith("/assets/god-charts.js") for url in client.requests)

                media_opened = client.evaluate(
                    """
(() => {
  const button = document.querySelector('.director-asset-card button[aria-label^="查看"]');
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert media_opened is True
                client.wait_for(
                    'document.body.innerText.includes("沉浸查看")'
                )
                media_enhanced = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("沉浸查看"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert media_enhanced is True
                client.wait_for(
                    """
document.querySelector(".media-enhancement")
  ?.getAttribute("aria-label") === "已打开本地图片增强复核。按 Escape 返回。"
"""
                )
                assert any(url.endswith("/assets/media-review.js") for url in client.requests)
                client.command(
                    "Input.dispatchKeyEvent",
                    {"type": "keyDown", "key": "Escape", "code": "Escape"},
                )
                client.command(
                    "Input.dispatchKeyEvent",
                    {"type": "keyUp", "key": "Escape", "code": "Escape"},
                )

                exited = client.evaluate(
                    """
(() => {
  const button = [...document.querySelectorAll("button")]
    .find((item) => item.textContent?.includes("退出导演台"));
  button?.click();
  return Boolean(button);
})()
"""
                )
                assert exited is True
                client.wait_for(
                    'document.body.innerText.includes("进入导演台")'
                )

                external = [
                    url
                    for url in client.requests
                    if url.startswith(("http://", "https://"))
                    and not url.startswith(origin)
                ]
                assert external == []
                assert _unexpected_http_errors(client) == []
                fatal_console_errors = _fatal_console_errors(client)
                assert fatal_console_errors == [], "\n".join(fatal_console_errors)
    finally:
        storage.close()
