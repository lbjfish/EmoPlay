"""Provider-neutral, secret-safe request, result, and error contracts.

The provider layer deliberately keeps provider-specific HTTP details out of the
rest of the application.  Requests carry a stable idempotency key and hashes
that can be persisted by the storage layer; audit representations never carry
prompts, media bytes, credentials, or endpoint URLs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from ..canonical import canonical_json


class RequestKind(StrEnum):
    """The supported provider operation families."""

    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"


class ProviderErrorKind(StrEnum):
    """Stable, provider-independent error dispositions."""

    AVAILABILITY = "availability"
    DEPLOYMENT = "deployment"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    POLICY = "policy"
    VALIDATION = "validation"
    MALFORMED_RESPONSE = "malformed_response"
    NETWORK_DISABLED = "network_disabled"
    UNKNOWN = "unknown"


class BillingState(StrEnum):
    """Values compatible with the append-only storage provider-call contract."""

    ACTUAL = "actual"
    ESTIMATED = "estimated"
    ZERO = "zero"
    UNKNOWN = "unknown"


_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_ -]?key|cookie|secret|password|passwd|"
    r"access[_ -]?token|refresh[_ -]?token|credential)",
    re.IGNORECASE,
)
_URL = re.compile(r"(?i)\bhttps?://[^\s\"']+")
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_INLINE_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|cookie|secret|password|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_PRIVATE_CONTENT_KEYS = frozenset(
    {"content", "instructions", "message", "messages", "prompt", "script", "text"}
)


def redact_text(value: str) -> str:
    """Remove common credential and endpoint forms from a diagnostic string."""

    redacted = _URL.sub("[REDACTED:URL]", value)
    redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
    return _INLINE_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted
    )


def redact_value(value: Any, *, key: str | None = None, _depth: int = 0) -> Any:
    """Produce a recursive secret-safe value suitable for diagnostics.

    This is intentionally stricter than ordinary JSON serialization: a URL is
    treated as sensitive because private Azure resource names and query-string
    keys routinely appear there.
    """

    if _depth > 24:
        return "[TRUNCATED:max-depth]"
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if (
        key is not None
        and key.lower() in _PRIVATE_CONTENT_KEYS
        and isinstance(value, str)
    ):
        return {
            "sha256": sha256(value.encode("utf-8")).hexdigest(),
            "length": len(value),
        }
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return {"bytes_length": len(value), "sha256": sha256(value).hexdigest()}
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_value(
                item_value, key=str(item_key), _depth=_depth + 1
            )
            for item_key, item_value in sorted(
                value.items(), key=lambda item: str(item[0])
            )
        }
    if isinstance(value, (tuple, list)):
        return [redact_value(item, key=key, _depth=_depth + 1) for item in value]
    return redact_text(str(value))


@dataclass(frozen=True)
class ChatMessage:
    """A deliberately small OpenAI-compatible chat message."""

    role: str
    content: str
    name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("message role must be a non-empty string")
        if not isinstance(self.content, str):
            raise TypeError("message content must be a string")
        if self.name is not None and (
            not isinstance(self.name, str) or not self.name.strip()
        ):
            raise ValueError("message name must be a non-empty string when supplied")

    def to_wire_dict(self) -> dict[str, str]:
        data = {"role": self.role, "content": self.content}
        if self.name is not None:
            data["name"] = self.name
        return data

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "content_sha256": sha256(self.content.encode("utf-8")).hexdigest(),
            "content_length": len(self.content),
        }


@dataclass(frozen=True)
class ImageInput:
    """Bytes supplied to an image edit request, never persisted verbatim."""

    data: bytes
    filename: str = "input.png"
    content_type: str = "image/png"

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("image input data must be non-empty bytes")
        if not isinstance(self.filename, str) or not self.filename.strip():
            raise ValueError("image input filename must be a non-empty string")
        if not isinstance(self.content_type, str) or not self.content_type.startswith(
            "image/"
        ):
            raise ValueError("image input content_type must be an image media type")

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "content_type": self.content_type,
            "bytes_length": len(self.data),
            "sha256": sha256(self.data).hexdigest(),
        }


def _coerce_kind(value: RequestKind | str) -> RequestKind:
    try:
        return RequestKind(value)
    except ValueError as exc:
        raise ValueError(f"unsupported request kind: {value!r}") from exc


def _coerce_messages(
    values: tuple[ChatMessage | Mapping[str, Any], ...],
) -> tuple[ChatMessage, ...]:
    messages: list[ChatMessage] = []
    for value in values:
        if isinstance(value, ChatMessage):
            messages.append(value)
        elif isinstance(value, Mapping):
            messages.append(
                ChatMessage(
                    role=str(value.get("role", "")),
                    content=str(value.get("content", "")),
                    name=(
                        str(value["name"]) if value.get("name") is not None else None
                    ),
                )
            )
        else:
            raise TypeError("messages must contain ChatMessage objects or mappings")
    return tuple(messages)


def _coerce_images(
    values: tuple[ImageInput | bytes, ...],
) -> tuple[ImageInput, ...]:
    images: list[ImageInput] = []
    for index, value in enumerate(values, start=1):
        if isinstance(value, ImageInput):
            images.append(value)
        elif isinstance(value, bytes):
            images.append(ImageInput(value, filename=f"input-{index}.png"))
        else:
            raise TypeError("image_inputs must contain ImageInput objects or bytes")
    return tuple(images)


def _coerce_error_kinds(
    values: Any,
) -> frozenset[ProviderErrorKind]:
    normalized: set[ProviderErrorKind] = set()
    for value in values:
        try:
            normalized.add(ProviderErrorKind(value))
        except ValueError as exc:
            raise ValueError(
                f"unsupported safe fallback error kind: {value!r}"
            ) from exc
    return frozenset(normalized)


@dataclass(frozen=True)
class ProviderRequest:
    """A provider-neutral logical operation.

    ``idempotency_key`` is generated from stable, non-secret request material
    when a caller does not provide one.  It identifies one logical operation,
    so a router can retain it while choosing a different provider after an
    eligible failure without re-sending the same paid request to that provider.
    """

    kind: RequestKind | str
    prompt: str = ""
    messages: tuple[ChatMessage | Mapping[str, Any], ...] = ()
    model: str | None = None
    operation: str = "generate"
    parameters: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    image_inputs: tuple[ImageInput | bytes, ...] = ()
    mask: ImageInput | bytes | None = None
    final_eligible: bool = True
    safe_fallback_for: (
        frozenset[ProviderErrorKind | str] | tuple[ProviderErrorKind | str, ...]
    ) = frozenset()

    def __post_init__(self) -> None:
        kind = _coerce_kind(self.kind)
        if not isinstance(self.prompt, str):
            raise TypeError("prompt must be a string")
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("operation must be a non-empty string")
        if self.model is not None and (
            not isinstance(self.model, str) or not self.model.strip()
        ):
            raise ValueError("model must be a non-empty string when supplied")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("parameters must be a mapping")
        if not isinstance(self.final_eligible, bool):
            raise TypeError("final_eligible must be a boolean")
        messages = _coerce_messages(self.messages)
        images = _coerce_images(self.image_inputs)
        mask = self.mask
        if isinstance(mask, bytes):
            mask = ImageInput(mask, filename="mask.png")
        if mask is not None and not isinstance(mask, ImageInput):
            raise ValueError("mask must be ImageInput or bytes when supplied")
        if kind is RequestKind.TEXT and not (self.prompt or messages):
            raise ValueError("text request requires a prompt or messages")
        if kind is RequestKind.IMAGE and not self.prompt:
            raise ValueError("image request requires a prompt")
        if kind is not RequestKind.IMAGE and (images or mask is not None):
            raise ValueError("image inputs are only valid for image requests")
        if (
            self.operation.lower() == "edit"
            and kind is RequestKind.IMAGE
            and not images
        ):
            raise ValueError("image edit request requires at least one input image")
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str)
            or not self.idempotency_key.strip()
        ):
            raise ValueError("idempotency_key must be a non-empty string when supplied")

        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "image_inputs", images)
        object.__setattr__(self, "mask", mask)
        object.__setattr__(self, "parameters", dict(self.parameters))
        object.__setattr__(
            self, "safe_fallback_for", _coerce_error_kinds(self.safe_fallback_for)
        )
        if self.idempotency_key is None:
            object.__setattr__(
                self, "idempotency_key", self._generated_idempotency_key()
            )

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "kind": self.normalized_kind.value,
            "prompt": self.prompt,
            "messages": [
                message.to_wire_dict() for message in self.normalized_messages
            ],
            "model": self.model,
            "operation": self.operation,
            "parameters": dict(self.parameters),
            "image_inputs": [
                image.to_audit_dict() for image in self.normalized_image_inputs
            ],
            "mask": self.normalized_mask.to_audit_dict()
            if self.normalized_mask is not None
            else None,
            "final_eligible": self.final_eligible,
        }

    def _generated_idempotency_key(self) -> str:
        digest = sha256(
            canonical_json(self._fingerprint_payload()).encode("utf-8")
        ).hexdigest()
        return f"idem_{digest[:32]}"

    @property
    def request_sha256(self) -> str:
        return sha256(
            canonical_json(self._fingerprint_payload()).encode("utf-8")
        ).hexdigest()

    @property
    def prompt_sha256(self) -> str:
        return sha256(self.prompt.encode("utf-8")).hexdigest()

    @property
    def effective_messages(self) -> tuple[ChatMessage, ...]:
        if self.normalized_messages:
            return self.normalized_messages
        return (ChatMessage(role="user", content=self.prompt),)

    @property
    def normalized_kind(self) -> RequestKind:
        return cast(RequestKind, self.kind)

    @property
    def normalized_messages(self) -> tuple[ChatMessage, ...]:
        return cast(tuple[ChatMessage, ...], self.messages)

    @property
    def normalized_image_inputs(self) -> tuple[ImageInput, ...]:
        return cast(tuple[ImageInput, ...], self.image_inputs)

    @property
    def normalized_mask(self) -> ImageInput | None:
        return cast(ImageInput | None, self.mask)

    @property
    def normalized_safe_fallback_for(self) -> frozenset[ProviderErrorKind]:
        return cast(frozenset[ProviderErrorKind], self.safe_fallback_for)

    def allows_fallback(self, error: ProviderError) -> bool:
        """Return whether a router may progress to another adapter.

        Default eligibility is assigned only to operational availability,
        deployment, rate-limit, transient, and timeout failures.  A caller can
        explicitly broaden the rule for a particular logical operation; policy,
        authentication, and validation errors never become eligible implicitly.
        """

        return (
            error.fallback_eligible
            or ProviderErrorKind(error.kind) in self.normalized_safe_fallback_for
        )

    def with_model(self, model: str | None) -> ProviderRequest:
        return replace(self, model=model)

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "kind": self.normalized_kind.value,
            "operation": self.operation,
            "model": self.model,
            "idempotency_key": self.idempotency_key,
            "request_sha256": self.request_sha256,
            "prompt_sha256": self.prompt_sha256,
            "prompt_length": len(self.prompt),
            "messages": [
                message.to_audit_dict() for message in self.normalized_messages
            ],
            "parameters": redact_value(self.parameters),
            "image_inputs": [
                image.to_audit_dict() for image in self.normalized_image_inputs
            ],
            "mask": self.normalized_mask.to_audit_dict()
            if self.normalized_mask is not None
            else None,
            "final_eligible": self.final_eligible,
            "safe_fallback_for": sorted(
                item.value for item in self.normalized_safe_fallback_for
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the safe serializable request form used by audit sinks."""

        return self.to_audit_dict()


@dataclass(frozen=True)
class ProviderError:
    """A public error that never contains a raw HTTP body or secret."""

    kind: ProviderErrorKind | str
    code: str
    message: str
    retryable: bool = False
    fallback_eligible: bool = False
    http_status: int | None = None
    retry_after_seconds: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            kind = ProviderErrorKind(self.kind)
        except ValueError as exc:
            raise ValueError(f"unsupported provider error kind: {self.kind!r}") from exc
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("provider error code must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("provider error message must be a non-empty string")
        if self.http_status is not None and not (100 <= self.http_status <= 599):
            raise ValueError("http_status must be an HTTP status when supplied")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must not be negative")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "details", dict(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": ProviderErrorKind(self.kind).value,
            "code": self.code,
            "message": redact_text(self.message),
            "retryable": self.retryable,
            "fallback_eligible": self.fallback_eligible,
            "http_status": self.http_status,
            "retry_after_seconds": self.retry_after_seconds,
            "details": redact_value(self.details),
        }


@dataclass(frozen=True)
class ProviderAttempt:
    """An audit-safe summary of a single adapter attempt."""

    provider: str
    model: str | None
    success: bool
    billing_state: BillingState | str
    error: ProviderError | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "billing_state", BillingState(self.billing_state))
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("attempt provider must be a non-empty string")
        if self.success and self.error is not None:
            raise ValueError("successful attempt cannot carry an error")
        if not self.success and self.error is None:
            raise ValueError("failed attempt must carry an error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "success": self.success,
            "billing_state": BillingState(self.billing_state).value,
            "error": self.error.to_dict() if self.error is not None else None,
        }


@dataclass(frozen=True)
class ProviderResult:
    """Typed provider outcome; binary media remains in memory, not audit logs."""

    provider: str
    kind: RequestKind | str
    request_sha256: str
    prompt_sha256: str
    idempotency_key: str
    model: str | None = None
    text: str | None = None
    images: tuple[bytes, ...] = ()
    audio: bytes | None = None
    voice_spec: Mapping[str, Any] | None = None
    final_eligible: bool = False
    billing_state: BillingState | str = BillingState.UNKNOWN
    usage: Mapping[str, Any] = field(default_factory=dict)
    error: ProviderError | None = None
    attempts: tuple[ProviderAttempt, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = _coerce_kind(self.kind)
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("result provider must be a non-empty string")
        for name, value in (
            ("request_sha256", self.request_sha256),
            ("prompt_sha256", self.prompt_sha256),
            ("idempotency_key", self.idempotency_key),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.error is None and kind is RequestKind.TEXT and self.text is None:
            raise ValueError("successful text result requires text")
        if self.error is None and kind is RequestKind.IMAGE and not self.images:
            raise ValueError("successful image result requires at least one image")
        if (
            self.error is None
            and kind is RequestKind.VOICE
            and self.voice_spec is None
            and self.audio is None
        ):
            raise ValueError("successful voice result requires audio or a voice_spec")
        if not isinstance(self.final_eligible, bool):
            raise TypeError("final_eligible must be a boolean")
        if self.error is not None and (
            self.text is not None
            or self.images
            or self.audio is not None
            or self.voice_spec is not None
        ):
            raise ValueError("failed provider result cannot include output")
        if not all(isinstance(image, bytes) and image for image in self.images):
            raise ValueError("images must contain non-empty bytes")
        if self.audio is not None and (
            not isinstance(self.audio, bytes) or not self.audio
        ):
            raise ValueError("audio must be non-empty bytes when supplied")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "billing_state", BillingState(self.billing_state))
        object.__setattr__(self, "usage", dict(self.usage))
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.voice_spec is not None:
            object.__setattr__(self, "voice_spec", dict(self.voice_spec))

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def status(self) -> str:
        return "success" if self.ok else "failed"

    @classmethod
    def success(
        cls,
        request: ProviderRequest,
        *,
        provider: str,
        model: str | None = None,
        text: str | None = None,
        images: tuple[bytes, ...] = (),
        audio: bytes | None = None,
        voice_spec: Mapping[str, Any] | None = None,
        final_eligible: bool | None = None,
        billing_state: BillingState | str = BillingState.UNKNOWN,
        usage: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        return cls(
            provider=provider,
            kind=request.kind,
            request_sha256=request.request_sha256,
            prompt_sha256=request.prompt_sha256,
            idempotency_key=request.idempotency_key or "",
            model=model or request.model,
            text=text,
            images=images,
            audio=audio,
            voice_spec=voice_spec,
            final_eligible=request.final_eligible
            if final_eligible is None
            else final_eligible,
            billing_state=billing_state,
            usage=usage or {},
            metadata=metadata or {},
        )

    @classmethod
    def failure(
        cls,
        request: ProviderRequest,
        *,
        provider: str,
        error: ProviderError,
        model: str | None = None,
        billing_state: BillingState | str = BillingState.UNKNOWN,
        metadata: Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        return cls(
            provider=provider,
            kind=request.kind,
            request_sha256=request.request_sha256,
            prompt_sha256=request.prompt_sha256,
            idempotency_key=request.idempotency_key or "",
            model=model or request.model,
            final_eligible=False,
            billing_state=billing_state,
            error=error,
            metadata=metadata or {},
        )

    def with_attempts(self, attempts: tuple[ProviderAttempt, ...]) -> ProviderResult:
        return replace(self, attempts=attempts)

    def to_audit_dict(self) -> dict[str, Any]:
        """Return a persistable representation with media and text redacted."""

        return {
            "provider": self.provider,
            "kind": RequestKind(self.kind).value,
            "status": self.status,
            "request_sha256": self.request_sha256,
            "prompt_sha256": self.prompt_sha256,
            "idempotency_key": self.idempotency_key,
            "model": self.model,
            "text": (
                {
                    "sha256": sha256(self.text.encode("utf-8")).hexdigest(),
                    "length": len(self.text),
                }
                if self.text is not None
                else None
            ),
            "images": [
                {"sha256": sha256(image).hexdigest(), "bytes_length": len(image)}
                for image in self.images
            ],
            "audio": (
                {
                    "sha256": sha256(self.audio).hexdigest(),
                    "bytes_length": len(self.audio),
                }
                if self.audio is not None
                else None
            ),
            "voice_spec": redact_value(self.voice_spec)
            if self.voice_spec is not None
            else None,
            "final_eligible": self.final_eligible,
            "billing_state": BillingState(self.billing_state).value,
            "usage": redact_value(self.usage),
            "error": self.error.to_dict() if self.error is not None else None,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "metadata": redact_value(self.metadata),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the safe serializable result form used by audit sinks."""

        return self.to_audit_dict()
