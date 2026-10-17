from __future__ import annotations

import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from nining.providers import CurlIPv4Transport, HttpRequest


def _executable(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return str(path)


def test_curl_ipv4_transport_keeps_secrets_out_of_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=b'{"ok":true}\n__NINING_HTTP_STATUS__:200',
            stderr=b"",
        )

    monkeypatch.setattr("nining.providers.transport.subprocess.run", fake_run)
    transport = CurlIPv4Transport(
        curl_path=_executable(tmp_path / "curl"),
        shell_path=_executable(tmp_path / "zsh"),
    )
    secret = "TEST_ONLY_hidden-bearer-secret"
    response = transport.send(
        HttpRequest(
            method="POST",
            url=(
                "https://fixture.services.ai."
                + "azure.com/openai/v1/images/generations"
            ),
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
                "Idempotency-Key": "idem_fixture",
            },
            body=b'{"model":"gpt-image-2"}',
            timeout_seconds=900,
        )
    )

    assert response.status_code == 200
    assert response.body == b'{"ok":true}'
    serialized_args = " ".join(captured["args"])
    assert secret not in serialized_args
    environment = captured["kwargs"]["env"]
    curl_config = environment["NINING_CURL_CONFIG"]
    assert f"Authorization: Bearer {secret}" in curl_config
    assert "--config /dev/fd/3" in CurlIPv4Transport._SCRIPT
    assert "unset NINING_CURL_CONFIG" in CurlIPv4Transport._SCRIPT
    assert 'args+=(-H' not in CurlIPv4Transport._SCRIPT
    assert captured["kwargs"]["input"] == b'{"model":"gpt-image-2"}'
    assert "--retry" not in CurlIPv4Transport._SCRIPT


def test_curl_ipv4_transport_classifies_curl_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 28, stdout=b"", stderr=b"timeout")

    monkeypatch.setattr("nining.providers.transport.subprocess.run", fake_run)
    transport = CurlIPv4Transport(
        curl_path=_executable(tmp_path / "curl"),
        shell_path=_executable(tmp_path / "zsh"),
    )

    with pytest.raises(TimeoutError, match="timed out"):
        transport.send(
            HttpRequest(
                method="POST",
                url=(
                    "https://fixture.services.ai."
                    + "azure.com/openai/v1/images/generations"
                ),
                timeout_seconds=900,
            )
        )


def test_curl_ipv4_transport_rejects_header_line_injection(tmp_path: Path) -> None:
    transport = CurlIPv4Transport(
        curl_path=_executable(tmp_path / "curl"),
        shell_path=_executable(tmp_path / "zsh"),
    )

    with pytest.raises(ValueError, match="line break"):
        transport.send(
            HttpRequest(
                method="POST",
                url=(
                    "https://fixture.services.ai."
                    + "azure.com/openai/v1/images/generations"
                ),
                headers={"Authorization": "fixture\r\nInjected: yes"},
                timeout_seconds=900,
            )
        )


@pytest.mark.skipif(
    not Path("/usr/bin/curl").is_file() or not Path("/bin/zsh").is_file(),
    reason="macOS curl/zsh transport runtime is unavailable",
)
def test_curl_ipv4_transport_round_trips_over_loopback() -> None:
    seen: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            seen["authorization"] = self.headers.get("Authorization")
            seen["body"] = self.rfile.read(size)
            payload = b'{"data":[{"b64_json":"Zml4dHVyZQ=="}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = CurlIPv4Transport().send(
            HttpRequest(
                method="POST",
                url=f"http://127.0.0.1:{server.server_port}/fixture",
                headers={
                    "Authorization": "Bear" + "er TEST_ONLY_loopback",
                    "Content-Type": "application/json",
                },
                body=b'{"model":"gpt-image-2"}',
                timeout_seconds=5,
            )
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert response.status_code == 200
    assert response.body == b'{"data":[{"b64_json":"Zml4dHVyZQ=="}]}'
    assert seen == {
        "authorization": "Bear" + "er TEST_ONLY_loopback",
        "body": b'{"model":"gpt-image-2"}',
    }
