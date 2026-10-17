"""Public, structured error taxonomy for the observable harness.

The structures in this module are deliberately suitable for logs and user-facing
diagnostics.  They contain a concise summary and remediation metadata, never a
traceback or private model reasoning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    """Stable categories used by retry and reporting policies."""

    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    VALIDATION = "validation"
    CAPABILITY = "capability"
    CONFIGURATION = "configuration"
    SAFETY = "safety"
    CONFLICT = "conflict"
    BUG = "bug"
    UNKNOWN = "unknown"


class ErrorSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ClassifiedError:
    """An exception converted into a deterministic public representation."""

    category: ErrorCategory
    code: str
    message: str
    retryable: bool
    retry_after_seconds: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HarnessError(Exception):
    """Base exception carrying an explicit machine-readable disposition."""

    category = ErrorCategory.UNKNOWN
    code = "harness_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})
        self.retry_after_seconds = retry_after_seconds


class RetryableActionError(HarnessError):
    category = ErrorCategory.TRANSIENT
    code = "retryable_action_error"
    retryable = True


class NonRetryableActionError(HarnessError):
    category = ErrorCategory.VALIDATION
    code = "non_retryable_action_error"


class CapabilityUnavailableError(HarnessError):
    category = ErrorCategory.CAPABILITY
    code = "capability_unavailable"


class ActionNotRegisteredError(HarnessError):
    category = ErrorCategory.CONFIGURATION
    code = "action_not_registered"


class HarnessInvariantError(HarnessError):
    category = ErrorCategory.BUG
    code = "harness_invariant"


@dataclass(frozen=True)
class ErrorEvent:
    """Append-only error event embedded in a trace record."""

    error: ClassifiedError
    phase: str
    action_name: str | None
    attempt: int
    handled: bool
    occurrence_id: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["error"] = self.error.to_dict()
        return data


@dataclass(frozen=True)
class BugEvent:
    """A reproducible, public defect record without stack traces or secrets."""

    bug_id: str
    summary: str
    phase: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    reproducible: bool | None = None
    requirement_ids: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    workaround: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _http_status(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def classify_exception(exc: BaseException) -> ClassifiedError:
    """Classify without consulting mutable global state.

    HTTP-like exceptions are recognized by a ``status_code`` attribute.  The
    eventual trace sanitizer remains responsible for redacting the message and
    details before persistence.
    """

    if isinstance(exc, HarnessError):
        return ClassifiedError(
            category=exc.category,
            code=exc.code,
            message=str(exc),
            retryable=exc.retryable,
            retry_after_seconds=exc.retry_after_seconds,
            details=exc.details,
        )

    status = _http_status(exc)
    if status is not None:
        details = {"http_status": status}
        if status == 401:
            return ClassifiedError(
                ErrorCategory.AUTHENTICATION,
                "http_401",
                str(exc),
                False,
                details=details,
            )
        if status == 403:
            return ClassifiedError(
                ErrorCategory.PERMISSION,
                "http_403",
                str(exc),
                False,
                details=details,
            )
        if status == 408:
            return ClassifiedError(
                ErrorCategory.TIMEOUT,
                "http_408",
                str(exc),
                True,
                details=details,
            )
        if status == 409:
            return ClassifiedError(
                ErrorCategory.CONFLICT,
                "http_409",
                str(exc),
                False,
                details=details,
            )
        if status == 429:
            retry_after = getattr(exc, "retry_after_seconds", None)
            return ClassifiedError(
                ErrorCategory.RATE_LIMIT,
                "http_429",
                str(exc),
                True,
                retry_after_seconds=retry_after,
                details=details,
            )
        if status >= 500:
            return ClassifiedError(
                ErrorCategory.TRANSIENT,
                f"http_{status}",
                str(exc),
                True,
                details=details,
            )
        return ClassifiedError(
            ErrorCategory.VALIDATION,
            f"http_{status}",
            str(exc),
            False,
            details=details,
        )

    if isinstance(exc, TimeoutError):
        return ClassifiedError(
            ErrorCategory.TIMEOUT, "timeout", str(exc), True
        )
    if isinstance(exc, ConnectionError):
        return ClassifiedError(
            ErrorCategory.TRANSIENT, "connection_error", str(exc), True
        )
    if isinstance(exc, PermissionError):
        return ClassifiedError(
            ErrorCategory.PERMISSION, "permission_error", str(exc), False
        )
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return ClassifiedError(
            ErrorCategory.VALIDATION,
            type(exc).__name__.lower(),
            str(exc),
            False,
        )
    return ClassifiedError(
        ErrorCategory.UNKNOWN,
        type(exc).__name__.lower() or "unknown_error",
        str(exc) or type(exc).__name__,
        False,
    )

