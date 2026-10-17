from __future__ import annotations

from nining.errors import (
    ErrorCategory,
    RetryableActionError,
    classify_exception,
)


class FakeHttpError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"fixture HTTP {status_code}")
        self.status_code = status_code


def test_error_classification_is_explicit_and_conservative() -> None:
    assert classify_exception(TimeoutError("late")).retryable is True
    assert classify_exception(ValueError("bad")).retryable is False
    assert classify_exception(FakeHttpError(429)).category is ErrorCategory.RATE_LIMIT
    assert classify_exception(FakeHttpError(503)).retryable is True
    assert classify_exception(FakeHttpError(401)).retryable is False
    classified = classify_exception(RetryableActionError("fixture"))
    assert classified.code == "retryable_action_error"
    assert classified.retryable is True

