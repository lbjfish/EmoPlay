"""Conservative automatic correction and retry policy."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ActionResult, Correction, Evaluation
from .registry import ActionSpec


@dataclass(frozen=True)
class RetryDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class RetryPolicy:
    """Never blindly retries paid, unknown, unsafe, or non-retryable actions."""

    max_retries_per_action: int = 2

    def assess(
        self,
        *,
        source: ActionSpec | None,
        target: ActionSpec | None,
        result: ActionResult,
        evaluation: Evaluation,
        correction: Correction,
        prior_retries: int,
    ) -> RetryDecision:
        if not correction.retry:
            return RetryDecision(False, correction.stop_reason or "correction requested stop")
        if not correction.automatic:
            return RetryDecision(False, "manual approval required")
        if source is None or target is None:
            return RetryDecision(False, "unknown actions are never automatically retried")
        if target.paid:
            return RetryDecision(False, "paid actions require explicit approval before another call")
        if source.paid and source.name == target.name:
            return RetryDecision(False, "paid action retry blocked to prevent duplicate charges")
        if not target.safe_to_retry:
            return RetryDecision(False, "target action is not marked safe_to_retry")
        if prior_retries >= self.max_retries_per_action:
            return RetryDecision(False, "per-action automatic retry limit reached")
        if result.error is not None and not result.error.retryable:
            return RetryDecision(False, "error classification is non-retryable")
        if result.error is None and not evaluation.correctable:
            return RetryDecision(False, "evaluation is not correctable")
        return RetryDecision(True, "safe unpaid correction allowed")

