"""Small stdlib HTTP boundary for provider adapters.

Adapters accept this protocol or a callable transport so unit tests can inspect
requests without opening a socket. ``UrllibTransport`` is portable; the opt-in
``CurlIPv4Transport`` matches the proven Azure image endpoint path on macOS.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contract import redact_text, redact_value


@dataclass(frozen=True)
class HttpRequest:
    """A complete HTTP request whose audit form excludes secret material."""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not self.method.strip():
            raise ValueError("HTTP method must be a non-empty string")
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("HTTP URL must be a non-empty string")
        if not isinstance(self.body, bytes):
            raise TypeError("HTTP body must be bytes")
        if self.timeout_seconds <= 0:
            raise ValueError("HTTP timeout_seconds must be positive")
        object.__setattr__(
            self,
            "headers",
            {str(key): str(value) for key, value in self.headers.items()},
        )

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "method": self.method.upper(),
            "url": redact_text(self.url),
            "headers": redact_value(self.headers),
            "body_length": len(self.body),
        }


@dataclass(frozen=True)
class HttpResponse:
    """A raw transport response retained only long enough for adapter parsing."""

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def __post_init__(self) -> None:
        if not isinstance(self.status_code, int) or not 100 <= self.status_code <= 599:
            raise ValueError("HTTP response status_code must be between 100 and 599")
        if not isinstance(self.body, bytes):
            raise TypeError("HTTP response body must be bytes")
        object.__setattr__(
            self,
            "headers",
            {str(key): str(value) for key, value in self.headers.items()},
        )

    def json_object(self) -> Mapping[str, Any]:
        """Decode one JSON object, rejecting arrays and malformed payloads."""

        try:
            decoded = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("response body is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, Mapping):
            raise TypeError("response JSON must be an object")
        return decoded

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


@runtime_checkable
class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


TransportLike = HttpTransport | Callable[[HttpRequest], HttpResponse]


def send(transport: TransportLike, request: HttpRequest) -> HttpResponse:
    """Send via a protocol object or a concise callable fixture."""

    if isinstance(transport, HttpTransport):
        return transport.send(request)
    response = transport(request)
    if not isinstance(response, HttpResponse):
        raise TypeError("transport callable must return HttpResponse")
    return response


class UrllibTransport:
    """Stdlib transport used only when a caller explicitly enables network use."""

    def send(self, request: HttpRequest) -> HttpResponse:
        native = Request(
            request.url,
            data=request.body or None,
            headers=dict(request.headers),
            method=request.method.upper(),
        )
        try:
            with urlopen(native, timeout=request.timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as exc:
            return HttpResponse(
                status_code=exc.code,
                headers=dict(exc.headers.items()) if exc.headers is not None else {},
                body=exc.read(),
            )
        except URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise TimeoutError("provider transport timed out") from exc
            raise ConnectionError("provider transport unavailable") from exc
        except TimeoutError as exc:
            raise TimeoutError("provider transport timed out") from exc


class CurlIPv4Transport:
    """curl-backed IPv4/HTTP1.1 transport without secrets in process arguments.

    A curl config containing the endpoint and headers enters the shell through
    its environment, is exposed to curl through an anonymous file descriptor,
    and is removed from curl's inherited environment. The body travels over
    stdin. No credential or private endpoint enters either process's argv, and
    the static shell program performs no automatic retry.
    """

    _SCRIPT = r'''
set -euo pipefail
exec 3<<<"$NINING_CURL_CONFIG"
unset NINING_CURL_CONFIG
"$NINING_CURL" \
  -4 --http1.1 --compressed --connect-timeout 60 -sS \
  --config /dev/fd/3 \
  --data-binary @- \
  --write-out $'\n__NINING_HTTP_STATUS__:%{http_code}'
'''

    def __init__(
        self,
        *,
        curl_path: str = "/usr/bin/curl",
        shell_path: str = "/bin/zsh",
    ) -> None:
        self.curl_path = curl_path
        self.shell_path = shell_path

    def send(self, request: HttpRequest) -> HttpResponse:
        if not os.access(self.curl_path, os.X_OK) or not os.access(
            self.shell_path, os.X_OK
        ):
            raise ConnectionError("curl IPv4 transport is unavailable")
        config_lines = [
            self._config_option("request", request.method.upper()),
            self._config_option("url", request.url),
            f"max-time = {max(1, round(request.timeout_seconds))}",
        ]
        for key, value in request.headers.items():
            config_lines.append(self._config_option("header", f"{key}: {value}"))
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "NINING_CURL": self.curl_path,
            "NINING_CURL_CONFIG": "\n".join(config_lines) + "\n",
        }
        try:
            completed = subprocess.run(
                [self.shell_path, "-c", self._SCRIPT],
                input=request.body,
                check=False,
                capture_output=True,
                env=environment,
                timeout=request.timeout_seconds + 5,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("provider transport timed out") from exc
        marker = b"\n__NINING_HTTP_STATUS__:"
        body, separator, status_bytes = completed.stdout.rpartition(marker)
        if completed.returncode != 0:
            if completed.returncode == 28:
                raise TimeoutError("provider transport timed out")
            if completed.returncode in {5, 6, 7, 35, 52, 56}:
                raise ConnectionError("provider transport unavailable")
            raise OSError("provider curl transport failed")
        if not separator:
            raise OSError("provider curl transport omitted HTTP status")
        try:
            status = int(status_bytes.strip())
        except ValueError as exc:
            raise OSError("provider curl transport returned invalid HTTP status") from exc
        return HttpResponse(status_code=status, headers={}, body=body)

    @staticmethod
    def _config_option(name: str, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("curl option contains a line break")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{name} = "{escaped}"'
