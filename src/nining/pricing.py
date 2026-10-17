"""Conservative cost estimates from the committed official Azure snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .providers.contract import BillingState, RequestKind


@dataclass(frozen=True)
class CostEstimate:
    amount_micros: int | None
    currency: str | None
    billing_state: BillingState
    basis: str


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def estimate_provider_cost(
    *,
    provider: str,
    model: str | None,
    kind: RequestKind | str,
    usage: Mapping[str, Any],
    billing_state: BillingState | str,
) -> CostEstimate:
    state = BillingState(billing_state)
    if state is BillingState.ZERO:
        return CostEstimate(0, None, BillingState.ZERO, "provider reported zero billing")
    if provider != "azure_openai" or model is None:
        return CostEstimate(None, None, state, "no committed price rule")
    normalized_kind = RequestKind(kind)
    if normalized_kind is RequestKind.TEXT and model.startswith("gpt-5.6-sol"):
        input_tokens = _nonnegative_int(
            usage.get("input_tokens", usage.get("prompt_tokens"))
        )
        output_tokens = _nonnegative_int(
            usage.get("output_tokens", usage.get("completion_tokens"))
        )
        if input_tokens is None or output_tokens is None:
            return CostEstimate(None, None, state, "usage tokens unavailable")
        # USD $4 input and $20 output per 1M tokens. One token times that
        # rate equals the same numeric number of USD micros.
        amount = input_tokens * 4 + output_tokens * 20
        return CostEstimate(
            amount,
            "USD",
            BillingState.ESTIMATED,
            "Azure official snapshot 2026-10-15: gpt-5.6-sol short global",
        )
    if normalized_kind is RequestKind.IMAGE and model == "gpt-image-2":
        details = usage.get("input_tokens_details")
        if not isinstance(details, Mapping):
            details = {}
        text_tokens = _nonnegative_int(details.get("text_tokens"))
        image_tokens = _nonnegative_int(details.get("image_tokens"))
        output_tokens = _nonnegative_int(usage.get("output_tokens"))
        if text_tokens is None or image_tokens is None or output_tokens is None:
            return CostEstimate(None, None, state, "image token breakdown unavailable")
        amount = text_tokens * 5 + image_tokens * 8 + output_tokens * 30
        return CostEstimate(
            amount,
            "USD",
            BillingState.ESTIMATED,
            "Azure official snapshot 2026-10-15: gpt-image-2 global",
        )
    return CostEstimate(None, None, state, "model has no committed price rule")
