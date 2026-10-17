"""Offline-safe provider adapters and request constructors.

No adapter retries a remote request.  A router may select a different adapter
after a classified eligible failure, but a potentially paid request is sent at
most once by each adapter for a logical idempotency key.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import tempfile
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from ..config import ModelConfig
from .contract import (
    BillingState,
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResult,
    RequestKind,
)
from .transport import HttpRequest, HttpResponse, TransportLike, UrllibTransport, send

DEFAULT_AZURE_TEXT_MODEL = "gpt-5.6-sol"
DEFAULT_AZURE_IMAGE_MODEL = "gpt-image-2"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_AZURE_API_VERSION = "2025-04-01-preview"
DEFAULT_TIMEOUT_SECONDS = 30.0

_POLICY_MARKERS = (
    "content_filter",
    "content policy",
    "content_policy",
    "policy_violation",
    "responsibleai",
    "safety system",
    "safety policy",
)
_SAFE_PROVIDER_CODE = re.compile(r"[^a-z0-9_.-]+")


class ProviderAdapter(Protocol):
    """The common adapter surface consumed by :class:`ProviderRouter`."""

    provider: str
    kind: RequestKind

    def execute(self, request: ProviderRequest) -> ProviderResult: ...


class ProviderConfigurationIssue(ValueError):
    """Safe configuration error without endpoint or credential values."""

    def __init__(self, missing: Sequence[str]) -> None:
        self.missing = tuple(missing)
        super().__init__("provider configuration is incomplete")


def _model_config(provider: str, default_model: str | None) -> ModelConfig:
    return ModelConfig(provider=provider, default_model=default_model)


def _safe_provider_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _SAFE_PROVIDER_CODE.sub("_", value.lower()).strip("_.-")
    return normalized[:120] or None


def _error_body_hints(response: HttpResponse) -> tuple[str | None, bool]:
    """Read only compact classification hints; never pass the body onward."""

    try:
        payload = response.json_object()
    except ValueError:
        body = response.body[:2048].decode("utf-8", errors="replace").lower()
        return None, any(marker in body for marker in _POLICY_MARKERS)
    error = payload.get("error")
    if not isinstance(error, Mapping):
        error = payload
    candidates = [error.get("code"), error.get("type"), error.get("message")]
    flattened = " ".join(str(item).lower() for item in candidates if item is not None)
    code = _safe_provider_code(error.get("code") or error.get("type"))
    return code, any(marker in flattened for marker in _POLICY_MARKERS)


def _retry_after(response: HttpResponse) -> float | None:
    value = response.header("retry-after")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def classify_http_failure(response: HttpResponse) -> tuple[ProviderError, BillingState]:
    """Map an HTTP response to an explicit safe disposition.

    A deployment 404 is treated as availability: the configured deployment can
    be absent while another text provider remains operational.  Generic 4xx
    responses are validation failures and never fall back by default.
    """

    status = response.status_code
    provider_code, policy_block = _error_body_hints(response)
    details: dict[str, Any] = {"http_status": status}
    if provider_code is not None:
        details["provider_code"] = provider_code
    if policy_block:
        return (
            ProviderError(
                ProviderErrorKind.POLICY,
                "policy_blocked",
                "provider policy blocked the request",
                http_status=status,
                details=details,
            ),
            BillingState.ZERO,
        )
    if status == 401:
        return (
            ProviderError(
                ProviderErrorKind.AUTHENTICATION,
                "http_401",
                "provider authentication failed",
                http_status=status,
                details=details,
            ),
            BillingState.ZERO,
        )
    if status == 403:
        return (
            ProviderError(
                ProviderErrorKind.PERMISSION,
                "http_403",
                "provider permission was denied",
                http_status=status,
                details=details,
            ),
            BillingState.ZERO,
        )
    if status == 404:
        return (
            ProviderError(
                ProviderErrorKind.DEPLOYMENT,
                "deployment_not_found",
                "configured provider deployment was not found",
                fallback_eligible=True,
                http_status=status,
                details=details,
            ),
            BillingState.ZERO,
        )
    if status == 408:
        return (
            ProviderError(
                ProviderErrorKind.TIMEOUT,
                "http_408",
                "provider request timed out",
                retryable=True,
                fallback_eligible=True,
                http_status=status,
                details=details,
            ),
            BillingState.UNKNOWN,
        )
    if status == 429:
        return (
            ProviderError(
                ProviderErrorKind.RATE_LIMIT,
                "http_429",
                "provider rate limit reached",
                retryable=True,
                fallback_eligible=True,
                http_status=status,
                retry_after_seconds=_retry_after(response),
                details=details,
            ),
            BillingState.ZERO,
        )
    if status >= 500:
        return (
            ProviderError(
                ProviderErrorKind.TRANSIENT,
                f"http_{status}",
                "provider service is temporarily unavailable",
                retryable=True,
                fallback_eligible=True,
                http_status=status,
                details=details,
            ),
            BillingState.UNKNOWN,
        )
    return (
        ProviderError(
            ProviderErrorKind.VALIDATION,
            f"http_{status}",
            "provider rejected the request",
            http_status=status,
            details=details,
        ),
        BillingState.ZERO,
    )


def _malformed_error() -> ProviderError:
    return ProviderError(
        ProviderErrorKind.MALFORMED_RESPONSE,
        "malformed_response",
        "provider response did not match the expected schema",
    )


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _selected_parameters(request: ProviderRequest, allowed: set[str]) -> dict[str, Any]:
    """Forward only documented operation values, never transport credentials."""

    return {
        key: value
        for key, value in request.parameters.items()
        if isinstance(key, str) and key in allowed
    }


def _azure_url(endpoint: str, model: str, operation: str, api_version: str) -> str:
    parsed = urlsplit(endpoint)
    hostname = (parsed.hostname or "").lower()
    allowed_suffixes = (
        ".openai.azure.com",
        ".services.ai.azure.com",
        ".cognitiveservices.azure.com",
    )
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not any(hostname.endswith(suffix) for suffix in allowed_suffixes)
    ):
        raise ValueError("Azure endpoint must be an approved credential-free HTTPS host")
    path = parsed.path.rstrip("/")
    v1_marker = "/openai/v1"
    if v1_marker in path:
        prefix = path.split(v1_marker, 1)[0]
        target_path = f"{prefix}{v1_marker}/{operation.lstrip('/')}"
        return urlunsplit((parsed.scheme, parsed.netloc, target_path, "", ""))
    deployment_marker = "/openai/deployments/"
    if deployment_marker in path:
        path = path.split(deployment_marker, 1)[0]
    elif path.endswith("/openai"):
        path = path[: -len("/openai")]
    target_path = (
        f"{path}/openai/deployments/{quote(model, safe='')}/{operation.lstrip('/')}"
    )
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            target_path,
            urlencode({"api-version": api_version}),
            "",
        )
    )


def _azure_auth_headers(endpoint: str, api_key: str) -> dict[str, str]:
    parsed = urlsplit(endpoint)
    if "/openai/v1" in parsed.path or (
        parsed.hostname or ""
    ).lower().endswith(".services.ai.azure.com"):
        return {"Authorization": f"Bearer {api_key}"}
    return {"api-key": api_key}


def _deepseek_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.hostname or "").lower() != "api.deepseek.com"
    ):
        raise ValueError("DeepSeek endpoint must be the credential-free official HTTPS host")
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _extract_text(
    payload: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], dict[str, Any]]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("choices missing")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise TypeError("choice is not an object")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise TypeError("choice message missing")
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        chunks = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, Mapping) and item.get("type", "text") == "text"
        ]
        text = "".join(chunks)
    else:
        raise TypeError("choice content missing")
    if not text:
        raise ValueError("choice content empty")
    usage = payload.get("usage")
    if usage is None:
        usage = {}
    if not isinstance(usage, Mapping):
        raise TypeError("usage is not an object")
    metadata: dict[str, Any] = {}
    if isinstance(first.get("finish_reason"), str):
        metadata["finish_reason"] = first["finish_reason"]
    return text, usage, metadata


def _extract_responses_text(
    payload: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], dict[str, Any]]:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct:
        text = direct
    else:
        fragments: list[str] = []
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if (
                        isinstance(part, Mapping)
                        and part.get("type") in {"output_text", "text"}
                        and isinstance(part.get("text"), str)
                    ):
                        fragments.append(str(part["text"]))
        text = "".join(fragments)
    if not text:
        raise ValueError("response output text is missing")
    usage = payload.get("usage")
    if usage is None:
        usage = {}
    if not isinstance(usage, Mapping):
        raise TypeError("usage is not an object")
    return (
        text,
        usage,
        {
            "response_id": payload.get("id")
            if isinstance(payload.get("id"), str)
            else None,
            "response_status": payload.get("status")
            if isinstance(payload.get("status"), str)
            else None,
        },
    )


def _extract_images(
    payload: Mapping[str, Any],
) -> tuple[tuple[bytes, ...], Mapping[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("image data missing")
    images: list[bytes] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise TypeError("image item is not an object")
        encoded = item.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("image item b64_json missing")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("image item b64_json invalid") from exc
        if not decoded:
            raise ValueError("image item decoded empty")
        images.append(decoded)
    usage = payload.get("usage")
    if usage is None:
        usage = {}
    if not isinstance(usage, Mapping):
        raise TypeError("usage is not an object")
    return tuple(images), usage


def _multipart_body(
    *,
    fields: Mapping[str, str],
    files: Sequence[tuple[str, str, str, bytes]],
    boundary: str,
) -> bytes:
    """Construct a deterministic multipart payload with no third-party HTTP lib."""

    chunks: list[bytes] = []
    marker = f"--{boundary}\r\n".encode("ascii")
    for name, value in fields.items():
        chunks.extend(
            (
                marker,
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            )
        )
    for name, filename, content_type, data in files:
        chunks.extend(
            (
                marker,
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                data,
                b"\r\n",
            )
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks)


class _RemoteAdapter:
    """Shared configuration and transport mechanics for paid adapters."""

    provider: str
    kind: RequestKind

    def __init__(
        self,
        *,
        provider: str,
        kind: RequestKind,
        config: ModelConfig,
        environ: Mapping[str, str] | None = None,
        transport: TransportLike | None = None,
        network_allowed: bool = False,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.provider = provider
        self.kind = kind
        self.config = config
        self.environ = os.environ if environ is None else environ
        self.transport = transport
        self.network_allowed = network_allowed
        self._endpoint_override = endpoint
        self._api_key_override = api_key
        self._model_override = model
        configured_timeout = config.extras.get("timeout_seconds")
        chosen_timeout = (
            timeout_seconds if timeout_seconds is not None else configured_timeout
        )
        try:
            self.timeout_seconds = float(chosen_timeout or DEFAULT_TIMEOUT_SECONDS)
        except (TypeError, ValueError) as exc:
            raise ValueError("provider timeout_seconds must be numeric") from exc
        if self.timeout_seconds <= 0:
            raise ValueError("provider timeout_seconds must be positive")

    def _environment_value(self, name: str | None) -> str | None:
        if name is None:
            return None
        value = self.environ.get(name)
        return value if value else None

    def _resolve_model(
        self, request: ProviderRequest, *, default_model: str | None = None
    ) -> str | None:
        return (
            request.model
            or self._model_override
            or self._environment_value(self.config.model_env)
            or self.config.default_model
            or default_model
        )

    def _resolve(
        self, request: ProviderRequest, *, default_model: str | None = None
    ) -> tuple[str, str, str]:
        endpoint = self._endpoint_override or self._environment_value(
            self.config.endpoint_env
        )
        api_key = self._api_key_override or self._environment_value(
            self.config.api_key_env
        )
        model = self._resolve_model(request, default_model=default_model)
        missing: list[str] = []
        if not endpoint:
            missing.append("endpoint")
        if not api_key:
            missing.append("api_key")
        if not model:
            missing.append("model")
        if missing:
            raise ProviderConfigurationIssue(missing)
        assert endpoint is not None
        assert api_key is not None
        assert model is not None
        return endpoint, api_key, model

    def _configuration_failure(
        self, request: ProviderRequest, issue: ProviderConfigurationIssue
    ) -> ProviderResult:
        return ProviderResult.failure(
            request,
            provider=self.provider,
            error=ProviderError(
                ProviderErrorKind.AVAILABILITY,
                "configuration_unavailable",
                "provider configuration is unavailable",
                fallback_eligible=True,
                details={"missing": list(issue.missing)},
            ),
            billing_state=BillingState.ZERO,
        )

    def _network_disabled_failure(
        self, request: ProviderRequest, model: str | None
    ) -> ProviderResult:
        return ProviderResult.failure(
            request,
            provider=self.provider,
            model=model,
            error=ProviderError(
                ProviderErrorKind.NETWORK_DISABLED,
                "network_disabled",
                "network use is disabled for this provider adapter",
                fallback_eligible=True,
            ),
            billing_state=BillingState.ZERO,
        )

    def _dispatch(self, request: HttpRequest) -> HttpResponse:
        if not self.network_allowed:
            raise PermissionError("network disabled")
        if self.transport is not None:
            return send(self.transport, request)
        return UrllibTransport().send(request)

    def _transport_failure(
        self, request: ProviderRequest, model: str | None, exc: Exception
    ) -> ProviderResult:
        if isinstance(exc, PermissionError):
            return self._network_disabled_failure(request, model)
        if isinstance(exc, TimeoutError):
            error = ProviderError(
                ProviderErrorKind.TIMEOUT,
                "transport_timeout",
                "provider transport timed out",
                retryable=True,
                fallback_eligible=True,
            )
            billing_state = BillingState.UNKNOWN
        elif isinstance(exc, ConnectionError | OSError):
            error = ProviderError(
                ProviderErrorKind.TRANSIENT,
                "transport_unavailable",
                "provider transport is unavailable",
                retryable=True,
                fallback_eligible=True,
            )
            billing_state = BillingState.UNKNOWN
        else:
            error = ProviderError(
                ProviderErrorKind.UNKNOWN,
                "transport_error",
                "provider transport failed unexpectedly",
            )
            billing_state = BillingState.UNKNOWN
        return ProviderResult.failure(
            request,
            provider=self.provider,
            model=model,
            error=error,
            billing_state=billing_state,
        )


class DeterministicTextAdapter:
    """Local, zero-cost text fallback with a stable output rule."""

    provider = "deterministic"
    kind = RequestKind.TEXT

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.kind is not RequestKind.TEXT:
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "wrong_request_kind",
                    "deterministic adapter only supports text",
                ),
                billing_state=BillingState.ZERO,
            )
        messages = request.effective_messages
        selected = next(
            (
                message.content
                for message in reversed(messages)
                if message.role == "user"
            ),
            messages[-1].content,
        )
        return ProviderResult.success(
            request,
            provider=self.provider,
            model="deterministic-v1",
            text=selected,
            billing_state=BillingState.ZERO,
            metadata={"mode": "offline"},
        )


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "nG3+JwAAAABJRU5ErkJggg=="
)


class PlaceholderImageAdapter:
    """A local marker image used only when an image provider is unavailable."""

    provider = "placeholder"
    kind = RequestKind.IMAGE

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.kind is not RequestKind.IMAGE:
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "wrong_request_kind",
                    "placeholder adapter only supports image",
                ),
                billing_state=BillingState.ZERO,
            )
        return ProviderResult.success(
            request,
            provider=self.provider,
            model="placeholder-v1",
            images=(_ONE_PIXEL_PNG,),
            final_eligible=False,
            billing_state=BillingState.ZERO,
            metadata={"placeholder": True, "reason": "no final image provider result"},
        )


class PromptOnlyVoiceAdapter:
    """Creates a voice brief without emitting audio."""

    provider = "prompt_only"
    kind = RequestKind.VOICE

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.kind is not RequestKind.VOICE:
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "wrong_request_kind",
                    "prompt-only adapter only supports voice",
                ),
                billing_state=BillingState.ZERO,
            )
        return ProviderResult.success(
            request,
            provider=self.provider,
            model="prompt-only-v1",
            voice_spec={
                "mode": "prompt_only",
                "prompt": request.prompt,
                "parameters": dict(request.parameters),
            },
            final_eligible=False,
            billing_state=BillingState.ZERO,
            metadata={"audio_generated": False},
        )


def _aiff_duration_ms(data: bytes) -> int | None:
    if len(data) < 12 or data[:4] != b"FORM" or data[8:12] not in {b"AIFF", b"AIFC"}:
        return None
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "big")
        content = data[offset + 8 : offset + 8 + chunk_size]
        if chunk_id == b"COMM" and len(content) >= 18:
            frames = int.from_bytes(content[2:6], "big")
            exponent = int.from_bytes(content[8:10], "big")
            sign = -1 if exponent & 0x8000 else 1
            exponent &= 0x7FFF
            mantissa = int.from_bytes(content[10:18], "big")
            if exponent == 0 or mantissa == 0:
                return None
            sample_rate = sign * mantissa * (2.0 ** (exponent - 16383 - 63))
            if sample_rate <= 0:
                return None
            return round(frames * 1000 / sample_rate)
        offset += 8 + chunk_size + (chunk_size % 2)
    return None


LocalVoiceSynthesizer = Callable[[str, str, int, str], bytes]


class MacOSSayVoiceAdapter:
    """Local preset-voice adapter; never clones or references a real speaker."""

    provider = "macos_say"
    kind = RequestKind.VOICE

    def __init__(
        self,
        config: ModelConfig | None = None,
        *,
        synthesizer: LocalVoiceSynthesizer | None = None,
    ) -> None:
        self.config = config or ModelConfig(
            provider="macos_say",
            enabled=True,
            extras={"voice": "Tingting", "rate": 220, "output_format": "aiff"},
        )
        self.synthesizer = synthesizer or self._synthesize

    def _synthesize(self, script: str, voice: str, rate: int, output_format: str) -> bytes:
        executable = str(self.config.extras.get("executable", "/usr/bin/say"))
        executable_path = Path(executable)
        if not executable_path.is_absolute() or not os.access(executable_path, os.X_OK):
            raise FileNotFoundError("local preset voice executable is unavailable")
        if output_format != "aiff":
            raise ValueError("macos_say currently supports only AIFF output")
        with tempfile.TemporaryDirectory(prefix="nining-voice-") as directory:
            root = Path(directory)
            script_path = root / "script.txt"
            output_path = root / "voice.aiff"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [
                    str(executable_path),
                    "-v",
                    voice,
                    "-r",
                    str(rate),
                    "-o",
                    str(output_path),
                    "-f",
                    str(script_path),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=float(self.config.extras.get("timeout_seconds", 30)),
            )
            if completed.returncode != 0 or not output_path.is_file():
                raise RuntimeError("local preset voice synthesis failed")
            return output_path.read_bytes()

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.kind is not RequestKind.VOICE:
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "wrong_request_kind",
                    "macos_say only supports voice requests",
                ),
                billing_state=BillingState.ZERO,
            )
        script = request.parameters.get("script")
        voice = request.parameters.get(
            "voice", self.config.extras.get("voice", "Tingting")
        )
        rate = request.parameters.get("rate", self.config.extras.get("rate", 220))
        output_format = request.parameters.get(
            "output_format", self.config.extras.get("output_format", "aiff")
        )
        if (
            not isinstance(script, str)
            or not script.strip()
            or not isinstance(voice, str)
            or not voice.strip()
            or isinstance(rate, bool)
            or not isinstance(rate, int)
            or not 80 <= rate <= 500
            or output_format != "aiff"
        ):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "invalid_voice_request",
                    "local preset voice parameters are invalid",
                ),
                billing_state=BillingState.ZERO,
            )
        try:
            audio = self.synthesizer(script, voice, rate, output_format)
        except (FileNotFoundError, OSError, RuntimeError, subprocess.SubprocessError):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.AVAILABILITY,
                    "local_voice_unavailable",
                    "local preset voice synthesis is unavailable",
                    fallback_eligible=True,
                ),
                billing_state=BillingState.ZERO,
            )
        duration_ms = _aiff_duration_ms(audio)
        return ProviderResult.success(
            request,
            provider=self.provider,
            model=f"macos-say:{voice}",
            audio=audio,
            voice_spec={
                "mode": "preset_voice",
                "preset": voice,
                "rate": rate,
                "output_format": output_format,
            },
            final_eligible=True,
            billing_state=BillingState.ZERO,
            metadata={
                "mime_type": "audio/aiff",
                "extension": "aiff",
                "duration_ms": duration_ms,
                "audio_generated": True,
                "source": "operating_system_preset",
            },
        )


class MockAdapter:
    """Fixture-only adapter with queued deterministic outcomes."""

    def __init__(
        self,
        kind: RequestKind | str,
        outcomes: Iterable[
            ProviderResult | ProviderError | str | bytes | BaseException
        ] = (),
        *,
        provider: str = "mock",
    ) -> None:
        self.kind = RequestKind(kind)
        self.provider = provider
        self._outcomes = deque(outcomes)

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if request.kind is not self.kind:
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "wrong_request_kind",
                    "mock adapter received a different request kind",
                ),
                billing_state=BillingState.ZERO,
            )
        outcome: ProviderResult | ProviderError | str | bytes | BaseException | None
        outcome = self._outcomes.popleft() if self._outcomes else None
        if isinstance(outcome, ProviderResult):
            return outcome
        if isinstance(outcome, ProviderError):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=outcome,
                billing_state=BillingState.ZERO,
            )
        if isinstance(outcome, BaseException):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.UNKNOWN,
                    "mock_exception",
                    "mock adapter produced an exception",
                ),
                billing_state=BillingState.ZERO,
            )
        if self.kind is RequestKind.TEXT:
            return ProviderResult.success(
                request,
                provider=self.provider,
                model="mock-v1",
                text=outcome if isinstance(outcome, str) else "mock text response",
                final_eligible=False,
                billing_state=BillingState.ZERO,
            )
        if self.kind is RequestKind.IMAGE:
            image = outcome if isinstance(outcome, bytes) else _ONE_PIXEL_PNG
            return ProviderResult.success(
                request,
                provider=self.provider,
                model="mock-v1",
                images=(image,),
                final_eligible=False,
                billing_state=BillingState.ZERO,
            )
        return ProviderResult.success(
            request,
            provider=self.provider,
            model="mock-v1",
            voice_spec={"mode": "mock", "prompt": request.prompt},
            final_eligible=False,
            billing_state=BillingState.ZERO,
        )


class AzureTextAdapter(_RemoteAdapter):
    """Azure OpenAI Responses or chat-completions text adapter."""

    def __init__(
        self,
        config: ModelConfig | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        transport: TransportLike | None = None,
        network_allowed: bool = False,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            provider="azure_openai",
            kind=RequestKind.TEXT,
            config=config or _model_config("azure_openai", DEFAULT_AZURE_TEXT_MODEL),
            environ=environ,
            transport=transport,
            network_allowed=network_allowed,
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    @property
    def api_version(self) -> str:
        value = self.config.extras.get("api_version", DEFAULT_AZURE_API_VERSION)
        return str(value)

    def build_request(self, request: ProviderRequest) -> HttpRequest:
        endpoint, api_key, model = self._resolve(request)
        if request.kind is not RequestKind.TEXT:
            raise ValueError("Azure text adapter requires a text request")
        api_style = str(self.config.extras.get("api_style", "chat_completions"))
        if urlsplit(endpoint).path.rstrip("/").endswith("/responses"):
            api_style = "responses"
        if api_style == "responses":
            payload: dict[str, Any] = {
                "model": model,
                "input": [
                    message.to_wire_dict() for message in request.effective_messages
                ],
                "store": False,
            }
            selected = _selected_parameters(
                request,
                {
                    "temperature",
                    "max_tokens",
                    "max_completion_tokens",
                    "top_p",
                },
            )
            if "max_completion_tokens" in selected:
                selected["max_output_tokens"] = selected.pop(
                    "max_completion_tokens"
                )
            elif "max_tokens" in selected:
                selected["max_output_tokens"] = selected.pop("max_tokens")
            payload.update(selected)
            operation = "responses"
        else:
            payload = {
                "model": model,
                "messages": [
                    message.to_wire_dict() for message in request.effective_messages
                ],
            }
            payload.update(
                _selected_parameters(
                    request,
                    {
                        "temperature",
                        "max_tokens",
                        "max_completion_tokens",
                        "top_p",
                        "seed",
                        "stop",
                        "response_format",
                    },
                )
            )
            operation = "chat/completions"
        return HttpRequest(
            method="POST",
            url=_azure_url(endpoint, model, operation, self.api_version),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Idempotency-Key": request.idempotency_key or "",
                **_azure_auth_headers(endpoint, api_key),
            },
            body=_json_bytes(payload),
            timeout_seconds=self.timeout_seconds,
        )

    def execute(self, request: ProviderRequest) -> ProviderResult:
        try:
            http_request = self.build_request(request)
        except ProviderConfigurationIssue as exc:
            return self._configuration_failure(request, exc)
        except (TypeError, ValueError):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "invalid_request",
                    "Azure text request could not be constructed",
                ),
                billing_state=BillingState.ZERO,
            )
        try:
            response = self._dispatch(http_request)
        except (
            ConnectionError,
            OSError,
            PermissionError,
            TimeoutError,
            TypeError,
        ) as exc:
            return self._transport_failure(
                request,
                self._resolve_model(request),
                exc,
            )
        if not 200 <= response.status_code < 300:
            error, billing_state = classify_http_failure(response)
            return ProviderResult.failure(
                request,
                provider=self.provider,
                model=self._resolve_model(request),
                error=error,
                billing_state=billing_state,
                metadata={"http": http_request.to_audit_dict()},
            )
        try:
            payload = response.json_object()
            api_style = str(
                self.config.extras.get("api_style", "chat_completions")
            )
            if urlsplit(http_request.url).path.endswith("/responses"):
                api_style = "responses"
            text, usage, metadata = (
                _extract_responses_text(payload)
                if api_style == "responses"
                else _extract_text(payload)
            )
        except (TypeError, ValueError):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                model=self._resolve_model(request),
                error=_malformed_error(),
                billing_state=BillingState.UNKNOWN,
                metadata={"http": http_request.to_audit_dict()},
            )
        return ProviderResult.success(
            request,
            provider=self.provider,
            model=self._resolve_model(request),
            text=text,
            billing_state=BillingState.UNKNOWN,
            usage=usage,
            metadata={"http_status": response.status_code, **metadata},
        )


class DeepSeekTextAdapter(_RemoteAdapter):
    """OpenAI-compatible DeepSeek chat adapter used only as a text secondary."""

    def __init__(
        self,
        config: ModelConfig | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        transport: TransportLike | None = None,
        network_allowed: bool = False,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            provider="deepseek",
            kind=RequestKind.TEXT,
            config=config or _model_config("deepseek", DEFAULT_DEEPSEEK_MODEL),
            environ=environ,
            transport=transport,
            network_allowed=network_allowed,
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    def build_request(self, request: ProviderRequest) -> HttpRequest:
        endpoint, api_key, model = self._resolve(request)
        if request.kind is not RequestKind.TEXT:
            raise ValueError("DeepSeek adapter requires a text request")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                message.to_wire_dict() for message in request.effective_messages
            ],
        }
        payload.update(
            _selected_parameters(
                request,
                {
                    "temperature",
                    "max_tokens",
                    "top_p",
                    "seed",
                    "stop",
                    "response_format",
                },
            )
        )
        return HttpRequest(
            method="POST",
            url=_deepseek_url(endpoint),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Idempotency-Key": request.idempotency_key or "",
            },
            body=_json_bytes(payload),
            timeout_seconds=self.timeout_seconds,
        )

    def execute(self, request: ProviderRequest) -> ProviderResult:
        try:
            http_request = self.build_request(request)
        except ProviderConfigurationIssue as exc:
            return self._configuration_failure(request, exc)
        except (TypeError, ValueError):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "invalid_request",
                    "DeepSeek request could not be constructed",
                ),
                billing_state=BillingState.ZERO,
            )
        try:
            response = self._dispatch(http_request)
        except (
            ConnectionError,
            OSError,
            PermissionError,
            TimeoutError,
            TypeError,
        ) as exc:
            return self._transport_failure(
                request,
                self._resolve_model(request),
                exc,
            )
        if not 200 <= response.status_code < 300:
            error, billing_state = classify_http_failure(response)
            return ProviderResult.failure(
                request,
                provider=self.provider,
                model=self._resolve_model(request),
                error=error,
                billing_state=billing_state,
                metadata={"http": http_request.to_audit_dict()},
            )
        try:
            text, usage, metadata = _extract_text(response.json_object())
        except (TypeError, ValueError):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                model=self._resolve_model(request),
                error=_malformed_error(),
                billing_state=BillingState.UNKNOWN,
                metadata={"http": http_request.to_audit_dict()},
            )
        return ProviderResult.success(
            request,
            provider=self.provider,
            model=self._resolve_model(request),
            text=text,
            billing_state=BillingState.UNKNOWN,
            usage=usage,
            metadata={"http_status": response.status_code, **metadata},
        )


class AzureImageAdapter(_RemoteAdapter):
    """Azure gpt-image-2 generation and multipart edit adapter."""

    def __init__(
        self,
        config: ModelConfig | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        transport: TransportLike | None = None,
        network_allowed: bool = False,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            provider="azure_openai",
            kind=RequestKind.IMAGE,
            config=config or _model_config("azure_openai", DEFAULT_AZURE_IMAGE_MODEL),
            environ=environ,
            transport=transport,
            network_allowed=network_allowed,
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    @property
    def api_version(self) -> str:
        value = self.config.extras.get("api_version", DEFAULT_AZURE_API_VERSION)
        return str(value)

    def build_generation_request(self, request: ProviderRequest) -> HttpRequest:
        endpoint, api_key, model = self._resolve(request)
        if request.kind is not RequestKind.IMAGE or request.operation.lower() == "edit":
            raise ValueError("Azure image generation requires a non-edit image request")
        payload: dict[str, Any] = {"model": model, "prompt": request.prompt}
        payload.update(
            _selected_parameters(
                request,
                {
                    "n",
                    "size",
                    "quality",
                    "output_format",
                    "output_compression",
                    "background",
                    "moderation",
                    "user",
                },
            )
        )
        for key in ("quality", "output_format", "output_compression"):
            if key not in payload and key in self.config.extras:
                payload[key] = self.config.extras[key]
        return HttpRequest(
            method="POST",
            url=_azure_url(endpoint, model, "images/generations", self.api_version),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Idempotency-Key": request.idempotency_key or "",
                **_azure_auth_headers(endpoint, api_key),
            },
            body=_json_bytes(payload),
            timeout_seconds=self.timeout_seconds,
        )

    def build_edit_request(self, request: ProviderRequest) -> HttpRequest:
        endpoint, api_key, model = self._resolve(request)
        if request.kind is not RequestKind.IMAGE or request.operation.lower() != "edit":
            raise ValueError("Azure image edit requires an edit image request")
        if not request.image_inputs:
            raise ValueError("Azure image edit requires input images")
        fields: dict[str, str] = {"model": model, "prompt": request.prompt}
        selected = _selected_parameters(
            request,
            {
                "n",
                "size",
                "quality",
                "output_format",
                "output_compression",
                "background",
                "moderation",
                "user",
            },
        )
        for key, value in selected.items():
            if isinstance(value, (str, int, float, bool)):
                fields[key] = (
                    str(value).lower() if isinstance(value, bool) else str(value)
                )
        for key in ("quality", "output_format", "output_compression"):
            if key not in fields and key in self.config.extras:
                fields[key] = str(self.config.extras[key])
        files = [
            ("image[]", image.filename, image.content_type, image.data)
            for image in request.normalized_image_inputs
        ]
        if request.normalized_mask is not None:
            files.append(
                (
                    "mask",
                    request.normalized_mask.filename,
                    request.normalized_mask.content_type,
                    request.normalized_mask.data,
                )
            )
        boundary = f"----nining-{(request.idempotency_key or 'request')[-24:]}"
        return HttpRequest(
            method="POST",
            url=_azure_url(endpoint, model, "images/edits", self.api_version),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "Idempotency-Key": request.idempotency_key or "",
                **_azure_auth_headers(endpoint, api_key),
            },
            body=_multipart_body(fields=fields, files=files, boundary=boundary),
            timeout_seconds=self.timeout_seconds,
        )

    def build_request(self, request: ProviderRequest) -> HttpRequest:
        if request.operation.lower() == "edit":
            return self.build_edit_request(request)
        return self.build_generation_request(request)

    def execute(self, request: ProviderRequest) -> ProviderResult:
        try:
            http_request = self.build_request(request)
        except ProviderConfigurationIssue as exc:
            return self._configuration_failure(request, exc)
        except (TypeError, ValueError):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                error=ProviderError(
                    ProviderErrorKind.VALIDATION,
                    "invalid_request",
                    "Azure image request could not be constructed",
                ),
                billing_state=BillingState.ZERO,
            )
        try:
            response = self._dispatch(http_request)
        except (
            ConnectionError,
            OSError,
            PermissionError,
            TimeoutError,
            TypeError,
        ) as exc:
            return self._transport_failure(
                request,
                self._resolve_model(request),
                exc,
            )
        model = self._resolve_model(request)
        if not 200 <= response.status_code < 300:
            error, billing_state = classify_http_failure(response)
            return ProviderResult.failure(
                request,
                provider=self.provider,
                model=model,
                error=error,
                billing_state=billing_state,
                metadata={"http": http_request.to_audit_dict()},
            )
        try:
            images, usage = _extract_images(response.json_object())
        except (TypeError, ValueError):
            return ProviderResult.failure(
                request,
                provider=self.provider,
                model=model,
                error=_malformed_error(),
                billing_state=BillingState.UNKNOWN,
                metadata={"http": http_request.to_audit_dict()},
            )
        return ProviderResult.success(
            request,
            provider=self.provider,
            model=model,
            images=images,
            billing_state=BillingState.UNKNOWN,
            usage=usage,
            metadata={
                "http_status": response.status_code,
                "operation": request.operation,
            },
        )


# Explicit aliases make the intent discoverable to callers without creating
# another parallel adapter contract.
AzureOpenAITextAdapter = AzureTextAdapter
AzureOpenAIImageAdapter = AzureImageAdapter
DeepSeekAdapter = DeepSeekTextAdapter
DeterministicAdapter = DeterministicTextAdapter
PlaceholderAdapter = PlaceholderImageAdapter
PromptOnlyAdapter = PromptOnlyVoiceAdapter
